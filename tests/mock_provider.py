"""Mock SMM panel API v2 for local testing."""
from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse

app = FastAPI()
STATE = {"next_order": 5000, "next_refill": 900, "orders": {}, "refills": {}}
KEY = "mockkey"


@app.post("/api/v2")
async def api(key: str = Form(...), action: str = Form(...), service: int | None = Form(None),
              link: str | None = Form(None), quantity: int | None = Form(None),
              order: str | None = Form(None), orders: str | None = Form(None),
              refills: str | None = Form(None), comments: str | None = Form(None)):
    if key != KEY:
        return {"error": "Invalid API key"}
    if action == "services":
        return [
            {"service": 1, "name": "TikTok Followers [Bot]", "type": "Default", "category": "TikTok Followers",
             "rate": "0.50", "min": "100", "max": "50000", "refill": False, "cancel": True},
            {"service": 2, "name": "TikTok Followers [HQ] R30", "type": "Default", "category": "TikTok Followers",
             "rate": "1.20", "min": "100", "max": "20000", "refill": True, "cancel": True},
            {"service": 3, "name": "Instagram Comments [Custom]", "type": "Custom Comments", "category": "Instagram Comments",
             "rate": "1.00", "min": "1", "max": "100", "refill": False, "cancel": False},
        ]
    if action == "balance":
        return {"balance": "100.00", "currency": "USD"}
    if action == "add":
        if "reject" in (link or ""):
            return {"error": "Incorrect link"}
        STATE["next_order"] += 1
        oid = STATE["next_order"]
        STATE["last_add"] = {"service": service, "quantity": quantity, "comments": comments}
        STATE["orders"][str(oid)] = {"charge": "0.12", "start_count": "10", "status": "Pending",
                                     "remains": str(quantity), "currency": "USD"}
        return {"order": oid}
    if action == "status":
        return {o: STATE["orders"].get(o, {"error": "Incorrect order ID"}) for o in orders.split(",")}
    if action == "refill":
        STATE["next_refill"] += 1
        STATE["refills"][str(STATE["next_refill"])] = "Pending"
        return {"refill": str(STATE["next_refill"])}
    if action == "cancel":
        out = []
        for o in orders.split(","):
            st = STATE["orders"].get(o)
            if not st:
                out.append({"order": int(o), "cancel": {"error": "Incorrect order ID"}})
            elif st["status"] in ("Completed", "Partial", "Canceled"):
                out.append({"order": int(o), "cancel": {"error": "Order can't be canceled"}})
            else:
                STATE.setdefault("cancel_requests", []).append(o)
                out.append({"order": int(o), "cancel": 1})
        return out
    if action == "refill_status":
        return [{"refill": int(r), "status": STATE["refills"].get(r, {"error": "Refill not found"})}
                for r in refills.split(",")]
    return {"error": "Incorrect action"}


@app.post("/_set_order")
async def set_order(oid: str = Form(...), status: str = Form(...), remains: str = Form("0")):
    STATE["orders"][oid].update(status=status, remains=remains)
    return {"ok": True}


@app.post("/_set_refill")
async def set_refill(rid: str = Form(...), status: str = Form(...)):
    STATE["refills"][rid] = status
    return {"ok": True}


@app.get("/_last_add")
async def last_add():
    return STATE.get("last_add", {})


# ---------------------------------------------------------------- mock PayMongo
from fastapi import Request  # noqa: E402

PM = {"sessions": {}, "last_create": None}


@app.post("/v1/checkout_sessions")
async def pm_create(request: Request):
    body = await request.json()
    PM["last_create"] = body
    sid = f"cs_mock_{len(PM['sessions']) + 1}"
    PM["sessions"][sid] = {"payments": []}
    return {"data": {"id": sid, "attributes": {"checkout_url": f"https://checkout.example/{sid}"}}}


@app.get("/v1/checkout_sessions/{sid}")
async def pm_get(sid: str):
    if sid not in PM["sessions"]:
        return JSONResponse({"errors": [{"detail": "not found"}]}, status_code=404)
    return {"data": {"id": sid, "attributes": PM["sessions"][sid]}}


@app.post("/v1/checkout_sessions/{sid}/expire")
async def pm_expire(sid: str):
    s = PM["sessions"].get(sid)
    if s is None:
        return JSONResponse({"errors": [{"detail": "not found"}]}, status_code=404)
    if s.get("payments"):
        return JSONResponse({"errors": [{"detail": "session already paid"}]}, status_code=400)
    s["status"] = "expired"
    return {"data": {"id": sid, "attributes": s}}


@app.post("/_pm_pay")
async def pm_pay(sid: str = Form(...), amount_php: int = Form(...), source: str = Form("gcash")):
    PM["sessions"].setdefault(sid, {"payments": []})["payments"] = [
        {"attributes": {"amount": amount_php * 100, "status": "paid", "source": {"type": source}}}]
    return {"ok": True}


@app.get("/_pm_last_create")
async def pm_last_create():
    return PM["last_create"] or {}
