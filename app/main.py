import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import fx
from app.config import get_settings
from app.routers import auth, orders, services, topups, webhooks
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, services.router, orders.router, topups.router, webhooks.router):
    app.include_router(r)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/fx")
async def fx_rate():
    return fx.status()
