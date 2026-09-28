"""邀请码 / 全家备份 测试。

Run: python tests/test_invite_backup.py
需要服务已启动，且 ADMIN_USER / ADMIN_PASS 可登录。
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile

BASE = "http://127.0.0.1:8764"
ADMIN = {"cookie": ""}


def req(method: str, path: str, body=None, raw=False, headers=None, session=None):
    url = BASE + path
    data = None
    h = {"Content-Type": "application/json; charset=utf-8"}
    jar = session if session is not None else ADMIN
    if jar.get("cookie"):
        h["Cookie"] = jar["cookie"]
    if headers:
        h.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            sc = resp.headers.get("Set-Cookie")
            if sc:
                jar["cookie"] = sc.split(";")[0]
            payload = resp.read()
            if raw:
                return resp.status, payload
            return resp.status, json.loads(payload.decode("utf-8"))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload.decode("utf-8"))
        except Exception:
            return e.code, {"error": payload.decode("utf-8", errors="replace")[:300]}


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    print("=== 邀请码 / 全家备份 测试 ===")
    user = os.environ.get("ADMIN_USER") or "admin"
    password = os.environ.get("ADMIN_PASS") or "Admin12345"
    st, body = req("POST", "/api/auth/login", {"username": user, "password": password})
    ok &= check("管理员登录", st == 200, body.get("error", ""))

    # --- invite ---
    note = f"测试邀请 {int(time.time()) % 100000}"
    st, invres = req("POST", "/api/invites", {
        "role": "member", "note": note, "max_uses": 2, "expires_in_days": 7
    })
    ok &= check("生成邀请码", st == 200 and bool(invres.get("invite", {}).get("code")))
    code = invres["invite"]["code"]

    # public check
    st, chk = req("POST", "/api/auth/check-invite", {"code": code})
    ok &= check("邀请码可被查询", st == 200, str(chk))

    # register with invite (fresh sessions so admin cookie is not overwritten)
    uname = f"inv_{int(time.time()) % 100000}"
    st, reg = req("POST", "/api/auth/register", {
        "invite_code": code,
        "username": uname,
        "password": "invitepwd1",
        "display_name": "邀请用户",
    }, session={"cookie": ""})
    ok &= check("邀请注册成功", st == 200 and bool(reg.get("user")), reg.get("error", ""))

    # second use still works (max_uses=2)
    uname2 = uname + "b"
    st, reg2 = req("POST", "/api/auth/register", {
        "invite_code": code,
        "username": uname2,
        "password": "invitepwd1",
        "display_name": "邀请用户B",
    }, session={"cookie": ""})
    ok &= check("邀请码第2次可用", st == 200, reg2.get("error", ""))

    # third should fail
    st, reg3 = req("POST", "/api/auth/register", {
        "invite_code": code,
        "username": uname + "c",
        "password": "invitepwd1",
    }, session={"cookie": ""})
    ok &= check("邀请码次数用尽后拒绝", st == 400)

    # bad code
    st, bad = req("POST", "/api/auth/register", {
        "invite_code": "INV-NOPE0000",
        "username": "nobody_x",
        "password": "whatever1",
    }, session={"cookie": ""})
    ok &= check("错误邀请码拒绝", st == 400)

    # revoke future code
    st, inv2 = req("POST", "/api/invites", {"role": "member", "note": "to revoke", "max_uses": 5})
    code2 = inv2["invite"]["code"]
    st, _ = req("DELETE", f"/api/invites/{code2}")
    ok &= check("作废邀请码", st == 200)
    st, _ = req("POST", "/api/auth/register", {
        "invite_code": code2, "username": "revoked_u", "password": "whatever1"
    })
    ok &= check("作废后不能注册", st == 400)

    # --- family backup ---
    st, zip_bytes = req("GET", "/api/admin/backup/family", raw=True)
    ok &= check("导出全家备份 zip", st == 200 and zip_bytes[:2] == b"PK", f"bytes={len(zip_bytes)}")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        names = zf.namelist()
        ok &= check("zip 含 manifest", "manifest.json" in names, str(names[:8]))
        ok &= check("zip 含 app.sqlite", "app.sqlite" in names)
        ok &= check("zip 含用户库", any(n.startswith("users/") and n.endswith("timeline.db") for n in names), str([n for n in names if n.endswith('.db')]))
    except Exception as e:
        ok &= check("zip 可解析", False, str(e))

    # restore merge
    import base64
    b64 = base64.b64encode(zip_bytes).decode("ascii")
    st, rest = req("POST", "/api/admin/backup/family/restore", {"zip_base64": b64, "mode": "merge"})
    ok &= check("恢复全家备份", st == 200 and rest.get("ok") is True, rest.get("message", ""))

    print()
    print("=== 结果:", "全部通过" if ok else "存在失败", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
