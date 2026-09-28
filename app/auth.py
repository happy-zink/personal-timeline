"""Multi-user auth: accounts, sessions, API keys, roles.

App-level SQLite (data/app.sqlite) stores identity.
Personal metrics live in per-user DBs: data/users/<user_id>/timeline.db
Passwords: PBKDF2-HMAC-SHA256 (stdlib only).
API keys: shown once at creation; only the hash is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
APP_DB = os.path.join(DATA_DIR, "app.sqlite")
USERS_DIR = os.path.join(DATA_DIR, "users")

PBKDF2_ITERATIONS = 120_000

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    email TEXT,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',   -- admin | member
    status TEXT NOT NULL DEFAULT 'active', -- active | disabled
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    key_prefix TEXT NOT NULL,      -- tk_ab12 (display)
    key_hash TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT 'read,write',
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS invite_codes (
    code TEXT PRIMARY KEY,
    created_by TEXT,
    note TEXT DEFAULT '',
    role TEXT NOT NULL DEFAULT 'member',
    max_uses INTEGER NOT NULL DEFAULT 1,
    used_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',  -- active | used | revoked
    created_at TEXT NOT NULL,
    expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
"""


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(USERS_DIR, exist_ok=True)


@contextmanager
def connect():
    ensure_dirs()
    conn = sqlite3.connect(APP_DB, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 8000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_auth_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------- password ----------

def hash_password(password: str, salt: Optional[str] = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return dk.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    dk, _ = hash_password(password, salt)
    return hmac.compare_digest(dk, password_hash)


# ---------- users ----------

def user_dir(user_id: str) -> str:
    path = os.path.join(USERS_DIR, user_id)
    os.makedirs(path, exist_ok=True)
    return path


def user_db_path(user_id: str) -> str:
    return os.path.join(user_dir(user_id), "timeline.db")


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, email, role, status, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def get_user(user_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_username(username: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
        return dict(row) if row else None


def create_user(
    username: str,
    password: str,
    display_name: str = "",
    email: str = "",
    role: str = "member",
) -> dict[str, Any]:
    username = (username or "").strip().lower()
    if not username or len(username) < 2:
        raise ValueError("用户名至少 2 个字符")
    if not password or len(password) < 6:
        raise ValueError("密码至少 6 位")
    if role not in ("admin", "member"):
        raise ValueError("role 必须是 admin/member")
    if get_user_by_username(username):
        raise ValueError("用户名已存在")
    user_id = "u_" + secrets.token_hex(8)
    pw_hash, salt = hash_password(password)
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO users(id, username, display_name, email, password_hash, password_salt, role, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,'active',?,?)""",
            (user_id, username, display_name or username, email, pw_hash, salt, role, now, now),
        )
    user_dir(user_id)  # create data directory
    return {"id": user_id, "username": username, "role": role, "display_name": display_name or username}


def authenticate(username: str, password: str) -> Optional[dict[str, Any]]:
    user = get_user_by_username(username)
    if not user or user.get("status") != "active":
        return None
    if not verify_password(password, user["password_hash"], user["password_salt"]):
        return None
    return user


def update_user(user_id: str, **fields: Any) -> dict[str, Any]:
    allowed = {"display_name", "email", "role", "status"}
    cols, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            cols.append(f"{k} = ?")
            vals.append(v)
    if "password" in fields:
        h, s = hash_password(fields["password"])
        cols.extend(["password_hash = ?", "password_salt = ?"])
        vals.extend([h, s])
    if not cols:
        raise ValueError("没有可更新字段")
    cols.append("updated_at = ?")
    vals.append(_now())
    vals.append(user_id)
    with connect() as conn:
        cur = conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            raise ValueError("用户不存在")
    return get_user(user_id)  # type: ignore[return-value]


def delete_user(user_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ---------- sessions ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(user_id: str, ttl_hours: int = 24 * 14, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = datetime.utcnow().timestamp() + ttl_hours * 3600
    expires_at = datetime.utcfromtimestamp(expires).isoformat(timespec="seconds") + "Z"
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent) VALUES (?,?,?,?,?)",
            (_hash_token(token), user_id, now, expires_at, user_agent),
        )
    return token


def get_session_user(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    th = _hash_token(token)
    with connect() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND u.status = 'active'""",
            (th,),
        ).fetchone()
        if not row:
            return None
        # expiry check
        exp = conn.execute("SELECT expires_at FROM sessions WHERE token_hash = ?", (th,)).fetchone()
        if exp:
            try:
                exp_ts = datetime.fromisoformat(exp["expires_at"].replace("Z", "+00:00")).timestamp()
                if exp_ts < datetime.utcnow().timestamp():
                    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))
                    return None
            except ValueError:
                pass
        return dict(row)


def destroy_session(token: str) -> None:
    if not token:
        return
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


# ---------- API keys ----------

def generate_api_key(user_id: str, name: str = "default", scopes: str = "read,write") -> dict[str, Any]:
    raw = "tk_" + secrets.token_urlsafe(24)
    prefix = raw[:10]
    key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    key_id = "k_" + secrets.token_hex(8)
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO api_keys(id, user_id, name, key_prefix, key_hash, scopes, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (key_id, user_id, name, prefix, key_hash, scopes, now),
        )
    return {
        "id": key_id,
        "name": name,
        "api_key": raw,  # only returned once
        "key_prefix": prefix,
        "scopes": scopes,
        "created_at": now,
    }


def verify_api_key(raw_key: str) -> Optional[dict[str, Any]]:
    if not raw_key or not raw_key.startswith("tk_"):
        return None
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with connect() as conn:
        row = conn.execute(
            """SELECT k.*, u.username, u.display_name, u.role, u.status
               FROM api_keys k JOIN users u ON u.id = k.user_id
               WHERE k.key_hash = ? AND k.revoked = 0 AND u.status = 'active'""",
            (key_hash,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), row["id"]))
        return dict(row)


def list_api_keys(user_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, user_id, name, key_prefix, scopes, created_at, last_used_at, revoked
               FROM api_keys WHERE user_id = ? ORDER BY created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_api_key(user_id: str, key_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE id = ? AND user_id = ?",
            (key_id, user_id),
        )
        return cur.rowcount > 0


# ---------- per-user settings (AI config etc.) ----------

def set_user_setting(user_id: str, key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO settings(user_id, key, value) VALUES (?,?,?)
               ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value""",
            (user_id, key, value),
        )


def get_user_setting(user_id: str, key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE user_id = ? AND key = ?", (user_id, key)
        ).fetchone()
        return row["value"] if row else default


def get_all_settings(user_id: str) -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE user_id = ?", (user_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}


def bootstrap_first_admin(username: str = "admin", password: str = "") -> Optional[dict[str, Any]]:
    """Create the first admin if no users exist. Returns generated password if created."""
    init_auth_db()
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if n > 0:
            return None
    if not password:
        password = secrets.token_urlsafe(10)
    user = create_user(username, password, display_name="管理员", role="admin")
    return {**user, "password": password}


# ---------- invite codes ----------

def create_invite_code(
    created_by: str,
    role: str = "member",
    note: str = "",
    max_uses: int = 1,
    expires_in_days: int = 30,
) -> dict[str, Any]:
    if role not in ("admin", "member"):
        raise ValueError("role 必须是 admin/member")
    if max_uses < 1:
        raise ValueError("max_uses 至少为 1")
    code = "INV-" + secrets.token_hex(4).upper()
    now = _now()
    expires = None
    if expires_in_days:
        from datetime import timedelta
        expires = (datetime.utcnow() + timedelta(days=expires_in_days)).isoformat(timespec="seconds") + "Z"
    with connect() as conn:
        conn.execute(
            """INSERT INTO invite_codes(code, created_by, note, role, max_uses, used_count, status, created_at, expires_at)
               VALUES (?,?,?,?,?,0,'active',?,?)""",
            (code, created_by, note, role, max_uses, now, expires),
        )
    return {
        "code": code,
        "role": role,
        "note": note,
        "max_uses": max_uses,
        "used_count": 0,
        "status": "active",
        "created_at": now,
        "expires_at": expires,
    }


def list_invite_codes() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM invite_codes ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_invite_code(code: str) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE invite_codes SET status = 'revoked' WHERE code = ?",
            (code.strip().upper(),),
        )
        return cur.rowcount > 0


def consume_invite_code(code: str) -> dict[str, Any]:
    """Validate and consume one use. Raises ValueError on failure."""
    code = (code or "").strip().upper()
    if not code:
        raise ValueError("缺少邀请码")
    with connect() as conn:
        row = conn.execute("SELECT * FROM invite_codes WHERE code = ?", (code,)).fetchone()
        if not row:
            raise ValueError("邀请码不存在")
        if row["status"] != "active":
            raise ValueError("邀请码已失效")
        if row["used_count"] >= row["max_uses"]:
            raise ValueError("邀请码使用次数已达上限")
        if row["expires_at"]:
            try:
                exp = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00")).timestamp()
                if exp < datetime.utcnow().timestamp():
                    conn.execute("UPDATE invite_codes SET status='revoked' WHERE code=?", (code,))
                    raise ValueError("邀请码已过期")
            except ValueError as e:
                if "过期" in str(e):
                    raise
        conn.execute(
            "UPDATE invite_codes SET used_count = used_count + 1 WHERE code = ?",
            (code,),
        )
        # auto-mark used if exhausted
        row2 = conn.execute("SELECT used_count, max_uses FROM invite_codes WHERE code = ?", (code,)).fetchone()
        if row2 and row2["used_count"] >= row2["max_uses"]:
            conn.execute("UPDATE invite_codes SET status='used' WHERE code=?", (code,))
        return dict(row)


def register_with_invite(
    username: str,
    password: str,
    invite_code: str,
    display_name: str = "",
    email: str = "",
) -> dict[str, Any]:
    inv = consume_invite_code(invite_code)
    role = inv.get("role") or "member"
    user = create_user(
        username, password,
        display_name=display_name or username,
        email=email or "",
        role=role,
    )
    return user
