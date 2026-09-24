"""Pull each provider's catalog and list Philippine-targeted services.

Usage:
  SMMGEN_API_KEY=... SMMFOLLOWOM_API_KEY=... python scripts/ph_services.py
Writes ph_services.csv (and all_services.csv with --all).
"""
import csv
import os
import re
import sys

import httpx

USD_TO_PHP = float(os.environ.get("USD_TO_PHP", "58"))
PH = re.compile(r"\b(philippines?|filipino|pinoy|ph)\b", re.I)

PROVIDERS = {
    # name: (api_url, api key env var, currency) — confirm currency with action=balance
    "smmgen": ("https://my.smmgen.com/api/v2", "SMMGEN_API_KEY", "USD"),
    "smmfollowom": ("https://smmfollowom.com/api/v2", "SMMFOLLOWOM_API_KEY", "USD"),  # confirm URL in dashboard
}


def main(include_all: bool):
    rows = []
    for name, (url, key_env, cur) in PROVIDERS.items():
        key = os.environ.get(key_env)
        if not key:
            print(f"[{name}] skipped: {key_env} not set")
            continue
        try:
            r = httpx.post(url, data={"key": key, "action": "services"}, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[{name}] failed: {e}")
            continue
        for s in data:
            text = f"{s.get('category', '')} {s.get('name', '')}"
            is_ph = bool(PH.search(text))
            if not include_all and not is_ph:
                continue
            rate = float(s["rate"])
            rows.append({
                "provider": name, "service_id": s["service"], "ph": is_ph,
                "category": s.get("category", ""), "name": s["name"], "type": s.get("type", ""),
                "rate_per_1k": rate,
                "rate_php_per_1k": round(rate * (USD_TO_PHP if cur == "USD" else 1), 2),
                "min": s.get("min"), "max": s.get("max"),
                "refill": s.get("refill"), "cancel": s.get("cancel"),
            })
        print(f"[{name}] {len(data)} services")

    rows.sort(key=lambda x: (x["category"], x["rate_php_per_1k"]))
    out = "all_services.csv" if include_all else "ph_services.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else ["provider"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} rows → {out}")


if __name__ == "__main__":
    main("--all" in sys.argv)
