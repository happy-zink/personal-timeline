"""私人数据时光轴 — 多用户本地服务端（stdlib only）

启动: python server.py
默认: http://127.0.0.1:8764

特性:
- 注册 / 登录 / 会话
- 按用户分库: data/users/<user_id>/timeline.db
- API Key (tk_...) 供脚本 / Agent / MCP
- AI: 真 LLM（可配置）+ 离线规则回退
- 首次启动自动创建管理员
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote as urlquote, urlparse

from app import auth
from app import db
from app import importexport as ie
from app import ai_ingest
from app import ai_llm
from app import family_backup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = BASE_DIR
EXPORT_DIR = os.path.join(BASE_DIR, "exports")
BACKUP_DIR_NAME = "backups"
PREFERRED_PORT = 8764

SESSION_COOKIE = "timeline_session"


def json_response(handler: BaseHTTPRequestHandler, data, status: int = 200, headers: dict | None = None) -> None:
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if headers:
        for k, v in headers.items():
            handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200,
                  content_type: str = "text/plain; charset=utf-8") -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length") or 0)
    return handler.rfile.read(length) if length else b""


def read_json(handler: BaseHTTPRequestHandler):
    raw = read_body(handler)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc}") from exc


def get_cookie(handler: BaseHTTPRequestHandler, name: str) -> str:
    raw = handler.headers.get("Cookie") or ""
    for part in raw.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1 :].strip()
    return ""


def set_session_cookie(handler: BaseHTTPRequestHandler, token: str) -> dict[str, str]:
    # 14 days; HttpOnly; SameSite=Lax
    return {
        "Set-Cookie": f"{SESSION_COOKIE}={token}; Path=/; Max-Age={14*24*3600}; HttpOnly; SameSite=Lax"
    }


def clear_session_cookie(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    return {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"}


def resolve_actor(handler: BaseHTTPRequestHandler) -> dict | None:
    """Return {user, via, scopes} or None. via: session|api_key"""
    authz = handler.headers.get("Authorization") or ""
    if authz.lower().startswith("bearer "):
        token = authz[7:].strip()
        if token.startswith("tk_"):
            key = auth.verify_api_key(token)
            if not key:
                return None
            user = auth.get_user(key["user_id"])
            if not user or user.get("status") != "active":
                return None
            scopes = set((key.get("scopes") or "read,write").split(","))
            return {"user": user, "via": "api_key", "scopes": scopes, "api_key": key}
        # also accept raw session token as Bearer
        user = auth.get_session_user(token)
        if user:
            return {"user": user, "via": "session", "scopes": {"read", "write"}}
        return None

    cookie = get_cookie(handler, SESSION_COOKIE)
    if cookie:
        user = auth.get_session_user(cookie)
        if user:
            return {"user": user, "via": "session", "scopes": {"read", "write"}}
    return None


def bind_user_db(user_id: str) -> str:
    path = auth.user_db_path(user_id)
    db.set_current_db(path)
    db.init_db(path)
    return path


def auto_backup(user_id: str) -> str | None:
    try:
        user_dir = auth.user_dir(user_id)
        backup_dir = os.path.join(user_dir, BACKUP_DIR_NAME)
        os.makedirs(backup_dir, exist_ok=True)
        backup = ie.export_json_backup()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = os.path.join(backup_dir, f"auto-{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        files = sorted(
            os.path.join(backup_dir, n) for n in os.listdir(backup_dir)
            if n.startswith("auto-") and n.endswith(".json")
        )
        for old in files[:-7]:
            try:
                os.remove(old)
            except OSError:
                pass
        return path
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    server_version = "PersonalTimeline/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            self.route("GET")
        except Exception as exc:
            traceback.print_exc()
            json_response(self, {"ok": False, "error": str(exc)}, 500)

    def do_POST(self) -> None:
        try:
            self.route("POST")
        except Exception as exc:
            traceback.print_exc()
            json_response(self, {"ok": False, "error": str(exc)}, 500)

    def do_PUT(self) -> None:
        try:
            self.route("PUT")
        except Exception as exc:
            traceback.print_exc()
            json_response(self, {"ok": False, "error": str(exc)}, 500)

    def do_DELETE(self) -> None:
        try:
            self.route("DELETE")
        except Exception as exc:
            traceback.print_exc()
            json_response(self, {"ok": False, "error": str(exc)}, 500)

    # ---------- auth helpers ----------
    def require_user(self, need_write: bool = False):
        actor = resolve_actor(self)
        if not actor:
            json_response(self, {"ok": False, "error": "未登录或 API Key 无效", "code": "unauthorized"}, 401)
            return None
        if need_write and "write" not in actor["scopes"]:
            json_response(self, {"ok": False, "error": "API Key 无写权限", "code": "forbidden"}, 403)
            return None
        return actor

    # ---------- routing ----------
    def route(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}

        if not path.startswith("/api/"):
            if method == "GET":
                return self.serve_static(path)
            return json_response(self, {"ok": False, "error": "Not found"}, 404)

        # ---- public ----
        if path == "/api/health" and method == "GET":
            return json_response(self, {
                "ok": True,
                "name": "私人数据时光轴",
                "version": "2.0-multiuser",
                "time": datetime.now().isoformat(timespec="seconds"),
                "port": self.server.server_address[1],
                "users": len(auth.list_users()),
                "data_root": os.path.join(BASE_DIR, "data", "users"),
            })

        if path == "/api/openapi.json" and method == "GET":
            return json_response(self, openapi_spec())

        # ---- auth endpoints ----
        if path == "/api/auth/register" and method == "POST":
            return self.handle_register()
        if path == "/api/auth/login" and method == "POST":
            return self.handle_login()
        if path == "/api/auth/logout" and method == "POST":
            return self.handle_logout()
        if path == "/api/auth/me" and method == "GET":
            return self.handle_me()

        # invite code check (public)
        if path == "/api/auth/check-invite" and method == "POST":
            payload = read_json(self)
            code = (payload.get("code") or "").strip().upper()
            codes = {c["code"]: c for c in auth.list_invite_codes()}
            c = codes.get(code)
            if not c or c["status"] != "active" or c["used_count"] >= c["max_uses"]:
                return json_response(self, {"ok": False, "error": "邀请码无效或已用完"}, 400)
            return json_response(self, {"ok": True, "role": c.get("role") or "member"})

        # ---- everything below requires auth ----
        actor = resolve_actor(self)
        if not actor:
            return json_response(self, {"ok": False, "error": "未登录或 API Key 无效", "code": "unauthorized"}, 401)
        user = actor["user"]
        user_id = user["id"]
        is_admin = user.get("role") == "admin"

        # bind personal db
        if path.startswith("/api/users") or path.startswith("/api/admin") or path.startswith("/api/auth"):
            pass  # app-level routes, no personal db needed
        else:
            bind_user_db(user_id)

        # admin / user management
        if path == "/api/users" and method == "GET":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            return json_response(self, {"ok": True, "users": auth.list_users()})
        if path == "/api/users" and method == "POST":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            payload = read_json(self)
            try:
                nu = auth.create_user(
                    payload.get("username") or "",
                    payload.get("password") or "",
                    display_name=payload.get("display_name") or "",
                    email=payload.get("email") or "",
                    role=payload.get("role") or "member",
                )
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, "user": nu})
        if path.startswith("/api/users/") and method in ("PUT", "DELETE"):
            uid = path.rsplit("/", 1)[-1]
            if not is_admin and uid != user_id:
                return json_response(self, {"ok": False, "error": "无权操作其他用户"}, 403)
            if method == "DELETE":
                if not is_admin:
                    return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
                auth.delete_user(uid)
                return json_response(self, {"ok": True})
            payload = read_json(self)
            try:
                auth.update_user(uid, **payload)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, "user": auth.get_user(uid)})

        # invite codes (admin)
        if path == "/api/invites" and method == "GET":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            return json_response(self, {"ok": True, "invites": auth.list_invite_codes()})
        if path == "/api/invites" and method == "POST":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            payload = read_json(self)
            try:
                inv = auth.create_invite_code(
                    created_by=user_id,
                    role=payload.get("role") or "member",
                    note=payload.get("note") or "",
                    max_uses=int(payload.get("max_uses") or 1),
                    expires_in_days=int(payload.get("expires_in_days") or 30),
                )
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, "invite": inv})
        if path.startswith("/api/invites/") and method == "DELETE":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            code = path.rsplit("/", 1)[-1]
            ok = auth.revoke_invite_code(code)
            return json_response(self, {"ok": ok})

        # family backup (admin)
        if path == "/api/admin/backup/family" and method == "GET":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            include_hist = qs.get("include_history") in ("1", "true", "yes")
            data = family_backup.build_family_backup(include_history_backups=include_hist)
            fname = family_backup.backup_filename()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/admin/backup/family/restore" and method == "POST":
            if not is_admin:
                return json_response(self, {"ok": False, "error": "需要管理员"}, 403)
            payload = read_json(self)
            mode = payload.get("mode") or "merge"
            # accept base64 zip or raw if sent as multipart — we use JSON {zip_base64}
            import base64
            b64 = payload.get("zip_base64") or ""
            if not b64:
                return json_response(self, {"ok": False, "error": "缺少 zip_base64"}, 400)
            try:
                raw = base64.b64decode(b64)
            except Exception:
                return json_response(self, {"ok": False, "error": "base64 解码失败"}, 400)
            try:
                result = family_backup.restore_family_backup(raw, mode=mode)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, result)

        # API keys
        if path == "/api/keys" and method == "GET":
            return json_response(self, {"ok": True, "keys": auth.list_api_keys(user_id)})
        if path == "/api/keys" and method == "POST":
            payload = read_json(self)
            created = auth.generate_api_key(
                user_id,
                name=payload.get("name") or "default",
                scopes=payload.get("scopes") or "read,write",
            )
            return json_response(self, {
                "ok": True,
                "key": created,
                "warning": "请立即保存 api_key，它只会显示这一次。",
            })
        if path.startswith("/api/keys/") and method == "DELETE":
            kid = path.rsplit("/", 1)[-1]
            ok = auth.revoke_api_key(user_id, kid)
            return json_response(self, {"ok": ok})

        # settings (AI config etc.)
        if path == "/api/settings" and method == "GET":
            s = auth.get_all_settings(user_id)
            # never return full api key in list — mask
            masked = dict(s)
            if masked.get("ai_api_key"):
                k = masked["ai_api_key"]
                masked["ai_api_key"] = k[:6] + "…" + k[-4:] if len(k) > 12 else "****"
            return json_response(self, {"ok": True, "settings": masked, "ai": ai_llm.health(user_id)})
        if path == "/api/settings" and method == "POST":
            payload = read_json(self)
            for k, v in payload.items():
                if k.startswith("ai_"):
                    auth.set_user_setting(user_id, k, str(v or ""))
            return json_response(self, {"ok": True, "ai": ai_llm.health(user_id)})

        # meta / first-run (per user)
        if path == "/api/meta" and method == "GET":
            return json_response(self, {
                "ok": True,
                "initialized_at": db.get_meta("initialized_at"),
                "first_run_done": db.get_meta("first_run_done") == "1",
                "timezone": db.get_meta("timezone") or "",
                "app_name": "私人数据时光轴",
                "user": {"id": user_id, "username": user["username"], "display_name": user.get("display_name"), "role": user.get("role")},
            })
        if path == "/api/meta/first-run" and method == "POST":
            payload = read_json(self)
            db.set_meta("first_run_done", "1")
            if payload.get("timezone"):
                db.set_meta("timezone", str(payload["timezone"]))
            return json_response(self, {"ok": True})

        # metrics
        if path == "/api/metrics" and method == "GET":
            include_inactive = qs.get("include_inactive") in ("1", "true", "yes")
            return json_response(self, {"ok": True, "metrics": db.list_metrics(include_inactive)})
        if path == "/api/metrics" and method == "POST":
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            payload = read_json(self)
            try:
                metric = db.create_metric(payload)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, "metric": metric})
        if path.startswith("/api/metrics/") and method in ("PUT", "DELETE"):
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            metric_id = int(path.rsplit("/", 1)[-1])
            if method == "DELETE":
                try:
                    db.delete_metric(metric_id)
                except ValueError as e:
                    return json_response(self, {"ok": False, "error": str(e)}, 400)
                return json_response(self, {"ok": True})
            payload = read_json(self)
            try:
                metric = db.update_metric(metric_id, payload)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, "metric": metric})

        # entries
        if path == "/api/entries" and method == "GET":
            start = qs.get("start") or (date.today() - timedelta(days=30)).isoformat()
            end = qs.get("end") or date.today().isoformat()
            return json_response(self, {"ok": True, "entries": db.list_entries(start, end)})

        if path.startswith("/api/entries/") and method in ("GET", "PUT", "DELETE"):
            date_str = path.rsplit("/", 1)[-1]
            if method == "GET":
                entry = db.get_entry(date_str)
                return json_response(self, {"ok": True, "entry": entry})
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            if method == "DELETE":
                deleted = db.delete_entry(date_str)
                return json_response(self, {"ok": True, "deleted": deleted})
            payload = read_json(self)
            try:
                entry = db.save_entry(date_str, payload)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            auto_backup(user_id)
            return json_response(self, {"ok": True, "entry": entry})

        # calendar / missing / trends / correlation / notes
        if path == "/api/calendar" and method == "GET":
            today = date.today()
            year = int(qs.get("year") or today.year)
            month = int(qs.get("month") or today.month)
            return json_response(self, {"ok": True, "calendar": db.calendar_month(year, month)})
        if path == "/api/missing" and method == "GET":
            today = date.today()
            start = qs.get("start") or (today - timedelta(days=14)).isoformat()
            end = qs.get("end") or today.isoformat()
            return json_response(self, {"ok": True, "missing_dates": db.missing_dates(start, end)})
        if path == "/api/trends" and method == "GET":
            keys = qs.get("metrics") or "weight,sleep_hours,mood"
            metric_keys = [k.strip() for k in keys.split(",") if k.strip()]
            start = qs.get("start") or (date.today() - timedelta(days=30)).isoformat()
            end = qs.get("end") or date.today().isoformat()
            granularity = qs.get("granularity") or "day"
            try:
                series = db.metric_series(metric_keys, start, end)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            metrics_meta = {m["key"]: m for m in db.list_metrics(include_inactive=True)}
            aggregated = {}
            for k, pts in series.items():
                vt = metrics_meta.get(k, {}).get("value_type", "number")
                aggregated[k] = db.aggregate_series(pts, granularity, vt)
            return json_response(self, {
                "ok": True, "start": start, "end": end, "granularity": granularity,
                "series": aggregated, "metrics": {k: metrics_meta.get(k) for k in metric_keys},
            })
        if path == "/api/correlation" and method == "GET":
            a = qs.get("a") or "weight"
            b = qs.get("b") or "sleep_hours"
            start = qs.get("start") or (date.today() - timedelta(days=90)).isoformat()
            end = qs.get("end") or date.today().isoformat()
            try:
                result = db.correlation(a, b, start, end)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            return json_response(self, {"ok": True, **result})
        if path == "/api/notes" and method == "GET":
            return json_response(self, {
                "ok": True,
                "notes": db.search_notes(qs.get("q") or "", qs.get("start") or "", qs.get("end") or ""),
            })

        # report
        if path.startswith("/api/report/") and method == "GET":
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                return json_response(self, {"ok": False, "error": "路径应为 /api/report/YYYY/MM"}, 400)
            year, month = int(parts[2]), int(parts[3])
            return json_response(self, {"ok": True, "report": db.month_report(year, month)})
        if path.startswith("/api/report/") and method == "PUT":
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            parts = path.strip("/").split("/")
            year, month = int(parts[2]), int(parts[3])
            payload = read_json(self)
            saved = db.save_report_summary(year, month, payload.get("summary_text") or "")
            return json_response(self, {"ok": True, **saved})

        # AI
        if path == "/api/ai/parse" and method == "POST":
            payload = read_json(self)
            text = payload.get("text") or payload.get("content") or ""
            d = payload.get("date")
            default_date = date.fromisoformat(d) if d else None
            draft = ai_llm.parse_with_llm(text, user_id=user_id, default_date=default_date)
            return json_response(self, draft)

        if path == "/api/ai/ingest" and method == "POST":
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            payload = read_json(self)
            if payload.get("text") or payload.get("content"):
                text = payload.get("text") or payload.get("content")
                d = payload.get("date")
                default_date = date.fromisoformat(d) if d else None
                draft = ai_llm.parse_with_llm(text, user_id=user_id, default_date=default_date)
                if not draft.get("ok"):
                    return json_response(self, draft, 400)
                if payload.get("confirm") is False:
                    draft["saved"] = False
                    return json_response(self, draft)
                date_str = payload.get("target_date") or draft["date"]
                values = {**(draft.get("values") or {}), **(payload.get("values") or {})}
                note = payload.get("note", draft.get("note") or text)
                try:
                    entry = db.save_entry(date_str, {"note": note, "timezone": payload.get("timezone") or "", "values": values})
                except ValueError as e:
                    return json_response(self, {"ok": False, "error": str(e), "draft": draft}, 400)
                auto_backup(user_id)
                return json_response(self, {
                    "ok": True, "saved": True, "date": date_str, "entry": entry, "draft": draft,
                    "message": f"已写入 {date_str}：{', '.join(values.keys()) or '仅备注'}",
                    "parser": draft.get("parser"),
                })
            date_str = payload.get("date") or date.today().isoformat()
            values = payload.get("values") or {}
            try:
                entry = db.save_entry(date_str, {
                    "note": payload.get("note") or "",
                    "timezone": payload.get("timezone") or "",
                    "values": values,
                })
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            auto_backup(user_id)
            return json_response(self, {"ok": True, "saved": True, "date": date_str, "entry": entry, "parser": "structured"})

        if path == "/api/ai/batch" and method == "POST":
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            payload = read_json(self)
            items = payload.get("items") or []
            if not items:
                return json_response(self, {"ok": False, "error": "缺少 items"}, 400)
            results = []
            for i, item in enumerate(items):
                try:
                    if item.get("text"):
                        draft = ai_llm.parse_with_llm(item["text"], user_id=user_id)
                        date_str = item.get("date") or draft.get("date")
                        values = {**draft.get("values", {}), **(item.get("values") or {})}
                        note = item.get("note", draft.get("note"))
                    else:
                        date_str = item.get("date") or date.today().isoformat()
                        values = item.get("values") or {}
                        note = item.get("note") or ""
                    db.save_entry(date_str, {"note": note or "", "timezone": item.get("timezone") or "", "values": values})
                    results.append({"index": i, "ok": True, "date": date_str})
                except Exception as e:
                    results.append({"index": i, "ok": False, "error": str(e)})
            auto_backup(user_id)
            ok_n = sum(1 for r in results if r["ok"])
            return json_response(self, {"ok": ok_n == len(results), "results": results, "message": f"批量写入 {ok_n}/{len(results)}"})

        # import / export
        if path == "/api/import/preview" and method == "POST":
            payload = read_json(self)
            text = payload.get("csv_text") or ""
            if not text:
                return json_response(self, {"ok": False, "error": "缺少 csv_text"}, 400)
            result = ie.preview_import(text, payload.get("mapping") or {}, payload.get("options") or {})
            if result.get("error") and not result.get("headers"):
                return json_response(self, result, 400)
            return json_response(self, result, 200)
        if path == "/api/import/commit" and method == "POST":
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            payload = read_json(self)
            text = payload.get("csv_text") or ""
            if not text:
                return json_response(self, {"ok": False, "error": "缺少 csv_text"}, 400)
            try:
                result = ie.commit_import(text, payload.get("mapping") or {}, payload.get("options") or {})
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            auto_backup(user_id)
            status = 200 if result.get("ok") else 400
            return json_response(self, result, status)
        if path == "/api/import/snore-map" and method == "POST":
            payload = read_json(self)
            text = payload.get("csv_text") or ""
            if not text:
                return json_response(self, {"ok": False, "error": "缺少 csv_text"}, 400)
            result = ie.map_snore_csv(text)
            status = 200 if result.get("ok") else 400
            return json_response(self, result, status)

        if path == "/api/export/csv" and method == "GET":
            csv_text = ie.export_csv(qs.get("start") or "1900-01-01", qs.get("end") or "2100-01-01")
            fname = f"personal-timeline-{user['username']}-{date.today().isoformat()}.csv"
            body = ("﻿" + csv_text).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/export/json" and method == "GET":
            backup = ie.export_json_backup()
            backup["user"] = {"id": user_id, "username": user["username"]}
            fname = f"timeline-backup-{user['username']}-{date.today().isoformat()}.json"
            body = json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/export/markdown" and method == "GET":
            year = int(qs.get("year") or date.today().year)
            month = int(qs.get("month") or date.today().month)
            md = build_markdown_report(year, month)
            fname = f"report-{year}-{month:02d}.md"
            utf8_name = urlquote(f"月报-{user['username']}-{year}-{month:02d}.md")
            body = md.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"; filename*=UTF-8\'\'{utf8_name}')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/restore" and method == "POST":
            if "write" not in actor["scopes"]:
                return json_response(self, {"ok": False, "error": "无写权限"}, 403)
            payload = read_json(self)
            backup = payload.get("backup")
            mode = payload.get("mode") or "replace"
            if not backup:
                return json_response(self, {"ok": False, "error": "缺少 backup"}, 400)
            try:
                result = ie.restore_json_backup(backup, mode=mode)
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            auto_backup(user_id)
            return json_response(self, result)

        return json_response(self, {"ok": False, "error": f"未知接口 {method} {path}"}, 404)

    # ---------- auth handlers ----------
    def handle_register(self):
        payload = read_json(self)
        invite = (payload.get("invite_code") or "").strip()
        # 1) Empty DB: first user becomes admin (no invite needed)
        if not auth.list_users():
            try:
                user = auth.create_user(
                    payload.get("username") or "",
                    payload.get("password") or "",
                    display_name=payload.get("display_name") or "",
                    email=payload.get("email") or "",
                    role="admin",
                )
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            token = auth.create_session(user["id"], user_agent=self.headers.get("User-Agent") or "")
            return json_response(self, {"ok": True, "user": user, "message": "首个账号已创建为管理员"},
                                 headers=set_session_cookie(self, token))
        # 2) Invite code registration
        if invite:
            try:
                user = auth.register_with_invite(
                    payload.get("username") or "",
                    payload.get("password") or "",
                    invite,
                    display_name=payload.get("display_name") or "",
                    email=payload.get("email") or "",
                )
            except ValueError as e:
                return json_response(self, {"ok": False, "error": str(e)}, 400)
            token = auth.create_session(user["id"], user_agent=self.headers.get("User-Agent") or "")
            return json_response(self, {"ok": True, "user": user, "message": "邀请注册成功"},
                                 headers=set_session_cookie(self, token))
        return json_response(self, {
            "ok": False,
            "error": "需要邀请码。可向管理员索取，或让管理员在「成员」页直接创建账号。",
        }, 403)

    def handle_login(self):
        payload = read_json(self)
        user = auth.authenticate(payload.get("username") or "", payload.get("password") or "")
        if not user:
            return json_response(self, {"ok": False, "error": "用户名或密码错误"}, 401)
        token = auth.create_session(user["id"], user_agent=self.headers.get("User-Agent") or "")
        return json_response(self, {
            "ok": True,
            "user": {
                "id": user["id"], "username": user["username"],
                "display_name": user.get("display_name"), "role": user.get("role"),
            },
        }, headers=set_session_cookie(self, token))

    def handle_logout(self):
        token = get_cookie(self, SESSION_COOKIE)
        auth.destroy_session(token)
        return json_response(self, {"ok": True}, headers=clear_session_cookie(self))

    def handle_me(self):
        actor = resolve_actor(self)
        if not actor:
            return json_response(self, {"ok": False, "error": "未登录"}, 401)
        u = actor["user"]
        return json_response(self, {
            "ok": True,
            "user": {
                "id": u["id"], "username": u["username"],
                "display_name": u.get("display_name"), "role": u.get("role"),
            },
            "via": actor["via"],
            "scopes": sorted(actor["scopes"]),
        })

    # ---------- static ----------
    def serve_static(self, path: str) -> None:
        allowed_roots = {
            "css": os.path.join(BASE_DIR, "css"),
            "js": os.path.join(BASE_DIR, "js"),
            "samples": os.path.join(BASE_DIR, "samples"),
        }
        if path in ("/", "/index.html"):
            full = os.path.join(BASE_DIR, "index.html")
        else:
            rel = path.lstrip("/")
            top = rel.split("/", 1)[0]
            if top in allowed_roots and "/" in rel:
                base = allowed_roots[top]
                rest = rel.split("/", 1)[1]
                full = os.path.normpath(os.path.join(base, rest))
                if not full.startswith(os.path.normpath(base)):
                    return text_response(self, "Forbidden", 403)
            else:
                full = os.path.join(BASE_DIR, "index.html")
        if not os.path.isfile(full):
            full = os.path.join(BASE_DIR, "index.html")
            if not os.path.isfile(full):
                return text_response(self, "Not Found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.endswith(".js"):
            ctype = "application/javascript; charset=utf-8"
        elif full.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif full.endswith(".html"):
            ctype = "text/html; charset=utf-8"
        elif full.endswith(".svg"):
            ctype = "image/svg+xml"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def build_markdown_report(year: int, month: int) -> str:
    report = db.month_report(year, month)
    lines = [
        f"# 个人数据月报 · {year} 年 {month} 月",
        "",
        f"- 统计区间：{report['start']} ~ {report['end']}",
        f"- 有记录天数：{report['entry_count']} / {len(report['missing_dates']) + report['entry_count']}",
        f"- 缺失天数：{len(report['missing_dates'])}",
        f"- 时区说明：{report['timezone_note']}",
        "",
        "## 统计摘要",
        "",
        "| 指标 | 单位 | 样本数 | 缺失天数 | 均值 | 中位数 | 最小 | 最大 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for s in report["stats"]:
        lines.append(
            f"| {s['name']} | {s['unit'] or '-'} | {s['n']} | {s['missing_days']} | "
            f"{s['mean'] if s['mean'] is not None else '-'} | {s['median'] if s['median'] is not None else '-'} | "
            f"{s['min'] if s['min'] is not None else '-'} | {s['max'] if s['max'] is not None else '-'} |"
        )
    lines += ["", "## 日记备注", ""]
    if report.get("notes"):
        for n in report["notes"]:
            lines.append(f"- **{n['date']}**：{n['note']}")
    else:
        lines.append("- （本月无文字备注）")
    lines += ["", "## 可编辑总结", "", report.get("summary_text") or "（尚未填写）", ""]
    lines += ["---", "", "统计口径：仅对有数值的日期计算统计量；缺失日期不计入均值/中位数，也不按 0 处理。", ""]
    return "\n".join(lines)


def openapi_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "私人数据时光轴 API",
            "version": "2.0.0",
            "description": (
                "Multi-user local-first personal metrics timeline. "
                "Authenticate with session cookie or `Authorization: Bearer tk_...` API key. "
                "AI entry: POST /api/ai/ingest (natural language or JSON). "
                "Missing values are null — never 0. Cross-midnight sleep is on the wake date."
            ),
        },
        "servers": [{"url": "http://127.0.0.1:8764"}],
        "components": {
            "securitySchemes": {
                "apiKey": {"type": "http", "scheme": "bearer", "description": "API Key: Bearer tk_..."},
                "sessionCookie": {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE},
            }
        },
        "security": [{"apiKey": []}, {"sessionCookie": []}],
        "paths": {
            "/api/auth/register": {"post": {"summary": "首次注册管理员（仅当无用户）"}},
            "/api/auth/login": {"post": {"summary": "登录，返回 Set-Cookie"}},
            "/api/auth/logout": {"post": {"summary": "登出"}},
            "/api/auth/me": {"get": {"summary": "当前身份"}},
            "/api/users": {
                "get": {"summary": "成员列表（管理员）"},
                "post": {"summary": "创建成员（管理员）"},
            },
            "/api/keys": {
                "get": {"summary": "列出 API Key"},
                "post": {"summary": "新建 API Key（明文只返回一次）"},
            },
            "/api/ai/ingest": {
                "post": {
                    "summary": "AI 自然语言 / 结构化写入",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "content": {"type": "string"},
                                        "date": {"type": "string"},
                                        "values": {"type": "object"},
                                        "note": {"type": "string"},
                                        "confirm": {"type": "boolean"},
                                    },
                                }
                            }
                        },
                    },
                }
            },
            "/api/ai/parse": {"post": {"summary": "只解析不写入"}},
            "/api/ai/batch": {"post": {"summary": "批量写入"}},
            "/api/entries/{date}": {
                "get": {"summary": "读某天"},
                "put": {"summary": "写某天"},
                "delete": {"summary": "删某天"},
            },
            "/api/metrics": {"get": {"summary": "指标"}, "post": {"summary": "新建指标"}},
            "/api/trends": {"get": {"summary": "趋势序列（缺失为 null）"}},
            "/api/export/json": {"get": {"summary": "JSON 备份"}},
            "/api/export/csv": {"get": {"summary": "CSV 导出"}},
            "/api/restore": {"post": {"summary": "从备份恢复"}},
            "/api/openapi.json": {"get": {"summary": "本契约"}},
        },
        "x-data-contract": {
            "auth": "Cookie session or Authorization: Bearer tk_...",
            "isolation": "data/users/<user_id>/timeline.db",
            "date": "local calendar day YYYY-MM-DD",
            "missing_value": "null / omitted",
            "cross_midnight_sleep": "record on wake date",
        },
    }


def pick_port(preferred: int = PREFERRED_PORT) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("没有可用端口")


def main() -> None:
    parser = argparse.ArgumentParser(description="私人数据时光轴")
    parser.add_argument("--port", type=int, default=PREFERRED_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--https", action="store_true", help="启用 HTTPS（需 --cert/--key）")
    parser.add_argument("--cert", default="", help="TLS 证书路径（PEM）")
    parser.add_argument("--key", default="", help="TLS 私钥路径（PEM）")
    args = parser.parse_args()

    auth.init_auth_db()
    os.makedirs(EXPORT_DIR, exist_ok=True)

    boot = auth.bootstrap_first_admin()
    if boot:
        print("=" * 52)
        print("  已创建首个管理员账号")
        print(f"  用户名: {boot['username']}")
        print(f"  初始密码: {boot['password']}")
        print("  请登录后在「设置」中修改密码")
        print("=" * 52)

    port = args.port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((args.host, port))
        except OSError:
            port = pick_port(port)
            print(f"[提示] 端口 {args.port} 被占用，已改用 {port}")

    httpd = ThreadingHTTPServer((args.host, port), Handler)

    scheme = "http"
    if args.https or args.cert or args.key:
        if not (args.cert and args.key):
            print("[错误] HTTPS 需要同时提供 --cert 与 --key")
            sys.exit(2)
        if not os.path.isfile(args.cert) or not os.path.isfile(args.key):
            print("[错误] 证书或私钥文件不存在")
            sys.exit(2)
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=args.cert, keyfile=args.key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"

    url = f"{scheme}://{args.host}:{port}"
    print("=" * 52)
    print("  私人数据时光轴  v2 多用户")
    print(f"  打开: {url}")
    print(f"  用户数据目录: {os.path.join(BASE_DIR, 'data', 'users')}")
    if scheme == "https":
        print("  TLS: 已启用")
    print("  Ctrl+C 停止")
    print("=" * 52)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
