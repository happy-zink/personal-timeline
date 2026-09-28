"""多用户 / API Key / 隔离测试。

Run: python tests/test_multiuser.py
需要服务已启动。会创建临时用户后清理。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8764"


def req(method: str, path: str, body=None, headers=None):
    url = BASE + path
    data = None
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                payload = {"raw": raw[:200]}
            return resp.status, payload, resp.headers
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {"error": raw.decode("utf-8", errors="replace")[:200]}
        return e.code, payload, e.headers


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def cookie_from_headers(headers) -> str:
    sc = headers.get("Set-Cookie") or ""
    return sc.split(";")[0]


def main() -> int:
    ok = True
    print("=== 多用户 / API Key / 隔离测试 ===")

    st, health, _ = req("GET", "/api/health")
    ok &= check("服务健康", st == 200 and health.get("ok"), str(health.get("version")))
    ok &= check("返回用户数", "users" in health, str(health.get("users")))

    # unauthenticated
    st, body, _ = req("GET", "/api/meta")
    ok &= check("未登录被拒", st == 401)

    # need existing admin — try login with common bootstrap, or register if empty
    # First, check /api/auth/me
    st, me, _ = req("GET", "/api/auth/me")
    if st != 200:
        # try register (only works if empty)
        st, reg, hdrs = req("POST", "/api/auth/register", {
            "username": "testadmin",
            "password": "testpass123",
            "display_name": "测试管理员",
        })
        if st == 200:
            cookie = cookie_from_headers(hdrs)
            ok &= check("注册首个管理员", True, reg.get("user", {}).get("username", ""))
        else:
            # already has users — need admin password. Use env or fail gracefully
            print("[INFO] 库中已有用户，请用已有管理员登录。")
            print("[INFO] 改用无鉴权路径验证跳过，或设置 ADMIN_PASS 环境变量。")
            import os
            password = os.environ.get("ADMIN_PASS") or os.environ.get("TIMELINE_ADMIN_PASS") or ""
            username = os.environ.get("ADMIN_USER") or "admin"
            if not password:
                print("[FAIL] 无法登录：未设置 ADMIN_PASS，多用户测试跳过登录部分")
                # still verify unauthorized paths
                st, _, _ = req("GET", "/api/keys")
                ok &= check("API 无 Key 被拒", st == 401)
                print("=== 结果: 部分跳过 ===")
                return 0
            st, login, hdrs = req("POST", "/api/auth/login", {"username": username, "password": password})
            ok &= check("管理员登录", st == 200, login.get("error", ""))
            cookie = cookie_from_headers(hdrs)
    else:
        cookie = ""
        ok &= check("已有会话", True, me.get("user", {}).get("username", ""))

    H = {"Cookie": cookie} if cookie else {}

    # if we have cookie, continue
    if not cookie:
        st, login, hdrs = req("POST", "/api/auth/login", {"username": "testadmin", "password": "testpass123"})
        cookie = cookie_from_headers(hdrs)
        H = {"Cookie": cookie}

    st, me, _ = req("GET", "/api/auth/me", headers=H)
    ok &= check("me 返回身份", st == 200 and bool(me.get("user")), str((me.get("user") or {}).get("username")))

    # create member (unique per run)
    import time
    uname = f"family_{int(time.time()) % 100000}"
    st, mem, _ = req("POST", "/api/users", {
        "username": uname,
        "password": "familypwd1",
        "display_name": "家人测试",
        "role": "member",
    }, headers=H)
    ok &= check("创建成员", st == 200, mem.get("error", ""))
    member_id = mem.get("user", {}).get("id")

    # member login
    st, login2, hdrs2 = req("POST", "/api/auth/login", {"username": uname, "password": "familypwd1"})
    ok &= check("成员登录", st == 200)

    # member login
    st, login2, hdrs2 = req("POST", "/api/auth/login", {"username": "family_test", "password": "familypwd1"})
    ok &= check("成员登录", st == 200)
    H2 = {"Cookie": cookie_from_headers(hdrs2)}

    # isolation: admin writes weight 99, member must not see it
    st, _, _ = req("PUT", "/api/entries/2026-09-10", {"note": "admin-only", "values": {"weight": 99.0}}, headers=H)
    ok &= check("管理员写入", st == 200)
    st, e_mem, _ = req("GET", "/api/entries/2026-09-10", headers=H2)
    mem_has = bool((e_mem.get("entry") or {}).get("values"))
    ok &= check("成员看不到管理员数据（分库隔离）", not mem_has, json.dumps(e_mem, ensure_ascii=False)[:120])

    st, e_adm, _ = req("GET", "/api/entries/2026-09-10", headers=H)
    ok &= check("管理员能看到自己的数据", (e_adm.get("entry") or {}).get("values", {}).get("weight", {}).get("value") == 99.0)

    # API key
    st, keyres, _ = req("POST", "/api/keys", {"name": "test-key", "scopes": "read,write"}, headers=H)
    api_key = keyres.get("key", {}).get("api_key")
    ok &= check("创建 API Key", st == 200 and api_key and api_key.startswith("tk_"), (api_key or "")[:12] + "…")

    # use API key
    st, entry, _ = req("PUT", "/api/entries/2026-09-11", {
        "note": "via api key", "values": {"weight": 70.5, "mood": 4}
    }, headers={"Authorization": f"Bearer {api_key}"})
    ok &= check("API Key 写入", st == 200)

    st, mek, _ = req("GET", "/api/auth/me", headers={"Authorization": f"Bearer {api_key}"})
    ok &= check("API Key 身份识别", st == 200 and mek.get("via") == "api_key")

    # revoke
    kid = keyres.get("key", {}).get("id")
    st, _, _ = req("DELETE", f"/api/keys/{kid}", headers=H)
    ok &= check("吊销 Key", st == 200)
    st, _, _ = req("GET", "/api/entries/2026-09-11", headers={"Authorization": f"Bearer {api_key}"})
    ok &= check("吊销后 Key 失效", st == 401)

    # AI with key still works (structured)
    st, keyres2, _ = req("POST", "/api/keys", {"name": "ai-key", "scopes": "read,write"}, headers=H)
    api_key2 = keyres2.get("key", {}).get("api_key")
    st, ai, _ = req("POST", "/api/ai/ingest", {
        "text": "今天体重71公斤，睡了6小时，心情一般",
        "confirm": True
    }, headers={"Authorization": f"Bearer {api_key2}"})
    ok &= check("API Key + AI ingest", st == 200 and ai.get("saved") is True, ai.get("parser", ""))

    # openapi
    st, spec, _ = req("GET", "/api/openapi.json")
    ok &= check("OpenAPI 含鉴权说明", st == 200 and "Bearer" in json.dumps(spec, ensure_ascii=False))

    print()
    print("=== 结果:", "全部通过" if ok else "存在失败", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
