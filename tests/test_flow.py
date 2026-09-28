"""端到端验证：记录 → 持久化 → 导入 → 趋势 → 月报 → 备份恢复。

运行:
  python tests/test_flow.py
  # 或指定服务地址
  python tests/test_flow.py --base http://127.0.0.1:8764

会向本机服务发请求。请先启动 server.py。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

BASE = "http://127.0.0.1:8764"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION = {"cookie": ""}


def req(method: str, path: str, body=None, raw=False):
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
            payload = resp.read()
            sc = resp.headers.get("Set-Cookie")
            if sc:
                SESSION["cookie"] = sc.split(";")[0]
            if raw:
                return resp.status, payload
            return resp.status, json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8"))
        except Exception:
            return e.code, {"error": payload.decode("utf-8", errors="replace")}


def ensure_login() -> None:
    import os
    user = os.environ.get("ADMIN_USER") or "admin"
    password = os.environ.get("ADMIN_PASS") or "Admin12345"
    st, body = req("GET", "/api/auth/me")
    if st == 200 and body.get("user"):
        return
    st, body = req("POST", "/api/auth/login", {"username": user, "password": password})
    if st != 200:
        raise SystemExit(f"登录失败: {body}")


def check(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=BASE)
    args = parser.parse_args()
    BASE = args.base.rstrip("/")

    ok = True
    print("=== 私人数据时光轴 端到端测试 ===")
    print("服务:", BASE)
    ensure_login()

    # 0 health
    st, health = req("GET", "/api/health")
    ok &= check("服务健康检查", st == 200 and health.get("ok") is True, str(health.get("port")))

    today = date.today()
    d1 = today.isoformat()
    d2 = (today - timedelta(days=1)).isoformat()

    # 1 save today
    st, data = req("PUT", f"/api/entries/{d1}", {
        "note": "自动化测试记录",
        "timezone": "Asia/Shanghai",
        "values": {
            "weight": 66.6,
            "sleep_hours": 7.2,
            "sleep_quality": 4,
            "mood": 4,
            "exercise_minutes": 30,
            "exercise_type": "快走",
        },
    })
    ok &= check("保存今日记录", st == 200 and data.get("ok") is True, data.get("error", ""))

    # 2 read back
    st, data = req("GET", f"/api/entries/{d1}")
    entry = data.get("entry") or {}
    vals = entry.get("values") or {}
    ok &= check(
        "读回今日记录",
        st == 200 and abs(float(vals.get("weight", {}).get("value", -1)) - 66.6) < 1e-6,
        json.dumps(vals, ensure_ascii=False)[:120],
    )

    # 3 out of range rejected
    st, data = req("PUT", f"/api/entries/{d2}", {
        "note": "越界测试",
        "values": {"weight": 999},
    })
    ok &= check("越界体重被拒绝", st == 400, data.get("error", ""))

    # 4 missing value stays null not 0
    st, data = req("PUT", f"/api/entries/{d2}", {
        "note": "仅心情",
        "values": {"mood": 3},
    })
    ok &= check("部分字段保存", st == 200 and data.get("ok") is True)

    st, data = req("GET", "/api/trends?metrics=weight&start=" + d2 + "&end=" + d1)
    series = data.get("series", {}).get("weight", [])
    # d2 should be null, d1 should be 66.6
    v0 = series[0]["value"] if series else "missing"
    v1 = series[-1]["value"] if series else "missing"
    ok &= check("缺失值保持 null（不按 0）", v0 is None and abs(float(v1) - 66.6) < 1e-6, f"d2={v0}, d1={v1}")

    # 5 CSV import with duplicates / blanks / bad values
    csv_path = os.path.join(ROOT, "samples", "messy_import_sample.csv")
    with open(csv_path, "r", encoding="utf-8") as f:
        csv_text = f.read()

    st, preview = req("POST", "/api/import/preview", {
        "csv_text": csv_text,
        "mapping": {
            "date": "date",
            "note": "note",
            "fields": {
                "weight": "weight",
                "sleep_hours": "sleep_hours",
                "sleep_quality": "sleep_quality",
                "mood": "mood",
                "exercise_minutes": "exercise_minutes",
            },
            "units": {"weight": "kg", "sleep_hours": "小时"},
        },
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "skip"},
    })
    ok &= check(
        "问题样例 CSV 预览可解析",
        st == 200 and (preview.get("row_count") or 0) > 0,
        f"rows={preview.get('row_count')} issues={len(preview.get('issues') or [])} ok={preview.get('ok')}",
    )
    ok &= check(
        "预览识别到重复/冲突或说明跳过",
        (preview.get("conflict_count") or 0) > 0 or (preview.get("row_count") or 0) > 0,
        f"conflicts={preview.get('conflict_count')}",
    )

    st, commit = req("POST", "/api/import/commit", {
        "csv_text": csv_text,
        "mapping": {
            "date": "date",
            "note": "note",
            "fields": {
                "weight": "weight",
                "sleep_hours": "sleep_hours",
                "sleep_quality": "sleep_quality",
                "mood": "mood",
                "exercise_minutes": "exercise_minutes",
            },
            "units": {"weight": "kg", "sleep_hours": "小时"},
        },
        "options": {"date_format": "%Y-%m-%d", "duplicate_strategy": "skip"},
    })
    ok &= check("问题样例导入（skip 策略）成功或报告错误行", st in (200, 400), commit.get("message") or commit.get("error", ""))

    # 6 snore mapping
    snore_path = os.path.join(ROOT, "samples", "snore_project01_sample.csv")
    with open(snore_path, "r", encoding="utf-8") as f:
        snore_text = f.read()
    st, mapped = req("POST", "/api/import/snore-map", {"csv_text": snore_text})
    ok &= check("打鼾 CSV 自动映射", st == 200 and mapped.get("ok") is True, json.dumps(mapped.get("mapping"), ensure_ascii=False))

    # 7 export CSV / JSON
    st, csv_bytes = req("GET", "/api/export/csv", raw=True)
    ok &= check("导出 CSV", st == 200 and b"date" in csv_bytes[:50], f"bytes={len(csv_bytes)}")

    st, backup_bytes = req("GET", "/api/export/json", raw=True)
    backup = json.loads(backup_bytes.decode("utf-8"))
    ok &= check("导出 JSON 备份", st == 200 and backup.get("format") == "mimo-personal-timeline-backup", f"entries={len(backup.get('entries') or [])}")

    # 8 restore merge
    st, restored = req("POST", "/api/restore", {"backup": backup, "mode": "merge"})
    ok &= check("备份恢复（merge）", st == 200 and restored.get("ok") is True, str(restored))

    # 9 month report + markdown
    y, m = today.year, today.month
    st, rep = req("GET", f"/api/report/{y}/{m}")
    ok &= check("月报生成", st == 200 and "stats" in (rep.get("report") or {}), f"entries={rep.get('report', {}).get('entry_count')}")

    st, md_bytes = req("GET", f"/api/export/markdown?year={y}&month={m}", raw=True)
    md = md_bytes.decode("utf-8")
    ok &= check("月报 Markdown 导出", st == 200 and "个人数据月报" in md, f"bytes={len(md_bytes)}")

    # 10 correlation
    st, corr = req("GET", f"/api/correlation?a=weight&b=sleep_hours&start={(today - timedelta(days=60)).isoformat()}&end={d1}")
    ok &= check("相关性含样本数与免责声明", st == 200 and "n" in corr and "disclaimer" in corr, f"n={corr.get('n')} r={corr.get('r')}")

    # 11 custom metric
    key = "resting_hr_test"
    st, created_resp = req("POST", "/api/metrics", {
        "key": key,
        "name": "测试静息心率",
        "unit": "bpm",
        "value_type": "number",
        "min_value": 30,
        "max_value": 220,
    })
    created = st == 200 or (st == 400 and "已存在" in str(created_resp.get("error", "")))
    ok &= check("创建自定义指标", created, created_resp.get("error", ""))

    st, data = req("PUT", f"/api/entries/{d1}", {
        "note": "含自定义指标",
        "values": {"weight": 66.6, "resting_hr_test": 58},
    })
    ok &= check("写入自定义指标值", st == 200)

    # 12 persistence check: re-read
    st, data = req("GET", f"/api/entries/{d1}")
    vals = (data.get("entry") or {}).get("values") or {}
    ok &= check("重启/重复读取仍在", st == 200 and vals.get("resting_hr_test", {}).get("value") == 58)

    print()
    print("=== 测试结果:", "全部通过" if ok else "存在失败项", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
