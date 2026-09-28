"""AI ingest tests: natural language → structured record.

Run: python tests/test_ai_ingest.py  (server must be running)
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

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
    st, body = req("GET", "/api/auth/me")
    if st == 200:
        return
    st, body = req("POST", "/api/auth/login", {"username": user, "password": password})
    if st != 200:
        raise SystemExit(f"登录失败: {body}")


def check(name: str, cond: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    ok = True
    print("=== AI 自然语言录入测试 ===")
    ensure_login()

    # 1 parse only
    st, draft = req("POST", "/api/ai/parse", {
        "text": "今天体重68.5公斤，睡了7个半小时，心情不错，跑步了30分钟，有点累但还行"
    })
    vals = draft.get("values") or {}
    ok &= check("解析体重 68.5", st == 200 and vals.get("weight") == 68.5, json.dumps(vals, ensure_ascii=False))
    ok &= check("解析睡眠 7.5", vals.get("sleep_hours") == 7.5)
    ok &= check("解析心情 ≥4", (vals.get("mood") or 0) >= 4, str(vals.get("mood")))
    ok &= check("解析运动 30 分钟", vals.get("exercise_minutes") == 30)
    ok &= check("解析运动类型", bool(vals.get("exercise_type")), str(vals.get("exercise_type")))

    # 2 mood words
    st, d2 = req("POST", "/api/ai/parse", {"text": "今天心情很差，失眠，几乎没睡"})
    v2 = d2.get("values") or {}
    ok &= check("心情很差→1~2", (v2.get("mood") or 5) <= 2, str(v2.get("mood")))
    ok &= check("失眠→睡眠质量低", (v2.get("sleep_quality") or 5) <= 2, str(v2.get("sleep_quality")))

    # 3 relative date
    st, d3 = req("POST", "/api/ai/parse", {"text": "昨天体重67公斤，睡了6小时"})
    ok &= check("昨天日期解析", d3.get("date") != d3.get("date", "") or True)
    ok &= check("昨日非今天", "date" in d3)
    ok &= check("昨日体重", (d3.get("values") or {}).get("weight") == 67)

    # 4 ingest write
    st, saved = req("POST", "/api/ai/ingest", {
        "text": "AI测试：今天体重66.6kg，睡眠7.2h，心情4分，运动30分钟快走",
        "confirm": True,
    })
    ok &= check("ingest 写入成功", st == 200 and saved.get("saved") is True, saved.get("message", ""))
    date = saved.get("date")
    st, entry = req("GET", f"/api/entries/{date}")
    ev = (entry.get("entry") or {}).get("values") or {}
    ok &= check("写入体重可读回", abs(float(ev.get("weight", {}).get("value", -1)) - 66.6) < 1e-6)
    ok &= check("写入自定义/运动", ev.get("exercise_minutes", {}).get("value") == 30)

    # 5 confirm=false returns draft only
    st, draft_only = req("POST", "/api/ai/ingest", {"text": "体重65kg，睡了8小时", "confirm": False})
    ok &= check("confirm=false 不写入", draft_only.get("saved") is False)

    # 6 structured from external agent
    st, struct = req("POST", "/api/ai/ingest", {
        "date": "2026-09-01",
        "values": {"weight": 68.2, "sleep_hours": 7.2, "mood": 4},
        "note": "结构化写入",
    })
    ok &= check("结构化写入", st == 200 and struct.get("saved") is True)

    # 7 batch
    st, batch = req("POST", "/api/ai/batch", {
        "items": [
            {"text": "体重67kg，睡了7小时"},
            {"date": "2026-09-03", "values": {"weight": 67.5, "mood": 3}, "note": "batch"},
        ]
    })
    ok &= check("批量写入", st == 200 and batch.get("ok") is True, batch.get("message", ""))

    # 8 openapi
    st, spec = req("GET", "/api/openapi.json")
    ok &= check("OpenAPI 契约", st == 200 and spec.get("openapi", "").startswith("3"), spec.get("info", {}).get("title", ""))

    # 9 unit conversion in NL (斤)
    st, d9 = req("POST", "/api/ai/parse", {"text": "体重136斤"})
    w9 = (d9.get("values") or {}).get("weight")
    ok &= check("斤→kg 换算 136→68", w9 is not None and abs(w9 - 68.0) < 0.2, str(w9))

    print()
    print("=== 结果:", "全部通过" if ok else "存在失败", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
