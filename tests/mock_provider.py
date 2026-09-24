"""Mock SMM panel API v2 for local testing."""
from fastapi import FastAPI, Form

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
