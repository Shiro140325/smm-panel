"""Manual provider refill test. Logs every call to refill_log.json (one JSON object per line).

  python scripts/refill_test.py status 123 124           # order statuses (run daily)
  python scripts/refill_test.py refill 123 124           # request refills once drops show
  python scripts/refill_test.py refill_status 9876 9877  # poll until Completed/Rejected

Env: PROVIDER_API_URL (default SMMGen), PROVIDER_API_KEY
"""
import json
import os
import sys
from datetime import datetime

import httpx

API = os.environ.get("PROVIDER_API_URL", "https://my.smmgen.com/api/v2")
KEY = os.environ["PROVIDER_API_KEY"]
LOG = "refill_log.json"


def call(**data):
    r = httpx.post(API, data={"key": KEY, **data}, timeout=30)
    r.raise_for_status()
    return r.json()


def log(entry: dict):
    entry["at"] = datetime.now().isoformat(timespec="seconds")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(entry)


if __name__ == "__main__":
    cmd, ids = sys.argv[1], sys.argv[2:]
    if cmd == "status":
        log({"status": call(action="status", orders=",".join(ids))})
    elif cmd == "refill":
        log({"refill": call(action="refill", orders=",".join(ids))})
    elif cmd == "refill_status":
        log({"refill_status": call(action="refill_status", refills=",".join(ids))})
    else:
        sys.exit(__doc__)
