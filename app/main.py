import asyncio
import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers

from app import fx
from app.config import get_settings
from app.site import ASSET_VERSION
from app.routers import account, admin, api_v2, auth, orders, pages, services, topups, webhooks
from app.workers.sync import run_sync_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


async def sync_loop(interval: int):
    await asyncio.sleep(5)  # let the app finish booting
    while True:
        try:
            await run_sync_once()
        except Exception:
            log.exception("sync run failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    task = asyncio.create_task(sync_loop(s.sync_interval_seconds)) if s.sync_enabled else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="SMM Panel API", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)   # the full service list is a few MB
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, services.router, orders.router, topups.router, webhooks.router, admin.router,
          account.router, api_v2.router, pages.router):
    app.include_router(r)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/fx")
async def fx_rate():
    return fx.status()


# Frontend: serve web/ at the root. Mounted last so API routes above win.
# html=True serves index.html for "/", "/login/", "/dashboard/" and redirects "/login" → "/login/".
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


# Every deploy gets a new asset version, stamped onto script/stylesheet URLs in HTML and JS
# ("/assets/app.css" → "/assets/app.css?v=<commit>"), so no browser or CDN cache can serve an old file.
_ASSET_URL = re.compile(r"""((?:/assets/|\./)[\w.-]+\.(?:js|css))(["'])""")


class WebFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"   # always revalidate (cheap 304 via ETag)
        media = (getattr(response, "media_type", "") or "")
        if response.status_code != 200 or not isinstance(response, FileResponse) \
                or not ("html" in media or "javascript" in media):
            return response
        body = _ASSET_URL.sub(rf"\1?v={ASSET_VERSION}\2", Path(response.path).read_text("utf-8")).encode()
        etag = '"' + hashlib.md5(body).hexdigest() + '"'
        headers = {"Cache-Control": "no-cache", "ETag": etag}
        if etag in Headers(scope=scope).get("if-none-match", ""):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=response.media_type, headers=headers)


if WEB_DIR.is_dir():
    app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
