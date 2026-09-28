"""导入策略验证脚本（overwrite / merge_empty / fail）。"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, timedelta

BASE = "http://127.0.0.1:8764"
SESSION = {"cookie": ""}


def req(method: str, path: str, body=None):
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if SESSION["cookie"]:
        headers["Cookie"] = SESSION["cookie"]
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            sc = resp.headers.get("Set-Cookie")
            if sc:
                SESSION["cookie"] = sc.split(";")[0]
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def ensure_login() -> None:
    import os
    user = os.environ.get("ADMIN_USER") or "admin"
    password = os.environ.get("ADMIN_PASS") or "Admin12345"
    st, _ = req("GET", "/api/auth/me")
    if st == 200:
        return
    st, body = req("POST", "/api/auth/login", {"username": user, "password": password})
    if st != 200:
        raise SystemExit(f"登录失败: {body}")


def main() -> None:
    ensure_login()
    d = (date.today() - timedelta(days=40)).isoformat()

    req("PUT", f"/api/entries/{d}", {"note": "原始", "values": {"weight": 70.0, "mood": 2}})

    st, r = req("POST", "/api/import/commit", {
        "csv_text": f"date,weight,note\n{d},69.5,覆盖后\n",
        "mapping": {"date": "date", "note": "note", "fields": {"weight": "weight"}, "units": {}},
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "overwrite"},
    })
    e = req("GET", f"/api/entries/{d}")[1]["entry"]
    print("overwrite:", r.get("message"))
    print("  weight=", e["values"]["weight"]["value"], "mood_kept=", e["values"].get("mood", {}).get("value"), "note=", e["note"])

    st, r2 = req("POST", "/api/import/commit", {
        "csv_text": f"date,weight,mood,note\n{d},68.0,5,合并\n",
        "mapping": {"date": "date", "note": "note", "fields": {"weight": "weight", "mood": "mood"}, "units": {}},
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "merge_empty"},
    })
    e2 = req("GET", f"/api/entries/{d}")[1]["entry"]
    print("merge_empty:", r2.get("message"))
    print("  weight_unchanged=", e2["values"]["weight"]["value"], "mood_unchanged=", e2["values"]["mood"]["value"])

    st, r3 = req("POST", "/api/import/commit", {
        "csv_text": f"date,weight\n{d},1\n",
        "mapping": {"date": "date", "fields": {"weight": "weight"}, "units": {}},
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "fail"},
    })
    print("fail_strategy ok=", r3.get("ok"), "error=", r3.get("error"))

    week = open("samples/week_sample.csv", encoding="utf-8").read()
    st, r4 = req("POST", "/api/import/commit", {
        "csv_text": week,
        "mapping": {
            "date": "date",
            "note": "note",
            "fields": {
                "weight": "weight",
                "sleep_hours": "sleep_hours",
                "sleep_quality": "sleep_quality",
                "mood": "mood",
                "exercise_minutes": "exercise_minutes",
                "snore_burden": "snore_burden",
            },
            "units": {},
        },
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "merge_empty"},
    })
    print("week_import:", r4.get("message"))

    tr = req("GET", "/api/trends?metrics=weight,snore_burden&start=2026-09-01&end=2026-09-07&granularity=day")[1]
    w = tr["series"]["weight"]
    print("trend_weight_points=", len(w), "non_null=", sum(1 for p in w if p["value"] is not None))

    # unit conversion lb -> kg
    d2 = (date.today() - timedelta(days=41)).isoformat()
    st, r5 = req("POST", "/api/import/commit", {
        "csv_text": f"date,w\n{d2},150\n",
        "mapping": {"date": "date", "fields": {"w": "weight"}, "units": {"weight": "lb"}},
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "overwrite"},
    })
    e5 = req("GET", f"/api/entries/{d2}")[1]["entry"]
    kg = e5["values"]["weight"]["value"]
    print("unit_lb_to_kg:", r5.get("message"), "weight_kg=", kg, "expected≈", round(150 * 0.45359237, 3))


if __name__ == "__main__":
    main()
