"""Family backup: one zip containing all user DBs + account metadata.

Layout inside zip:
  manifest.json
  app.sqlite                 # accounts / keys / invites (hashes only for passwords)
  users/<user_id>/timeline.db
  users/<user_id>/backups/   # optional, skipped by default to keep zip small
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import zipfile
from datetime import datetime
from typing import Any, Optional

from . import auth

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")


def build_family_backup(include_history_backups: bool = False) -> bytes:
    """Return zip bytes of the whole multi-user installation."""
    auth.init_auth_db()
    stamp = datetime.now().isoformat(timespec="seconds")
    users = auth.list_users()
    manifest: dict[str, Any] = {
        "format": "mimo-timeline-family-backup",
        "version": 1,
        "exported_at": stamp,
        "users": [{"id": u["id"], "username": u["username"], "role": u["role"]} for u in users],
        "note": "含账号库 app.sqlite 与每位用户 timeline.db。恢复会覆盖同名用户数据。",
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        app_db = os.path.join(DATA_DIR, "app.sqlite")
        if os.path.isfile(app_db):
            zf.write(app_db, "app.sqlite")

        users_root = os.path.join(DATA_DIR, "users")
        if os.path.isdir(users_root):
            for name in sorted(os.listdir(users_root)):
                udir = os.path.join(users_root, name)
                if not os.path.isdir(udir):
                    continue
                dbf = os.path.join(udir, "timeline.db")
                if os.path.isfile(dbf):
                    zf.write(dbf, f"users/{name}/timeline.db")
                # WAL sidecars if present
                for extra in ("timeline.db-wal", "timeline.db-shm"):
                    p = os.path.join(udir, extra)
                    if os.path.isfile(p):
                        zf.write(p, f"users/{name}/{extra}")
                if include_history_backups:
                    bdir = os.path.join(udir, "backups")
                    if os.path.isdir(bdir):
                        for fn in os.listdir(bdir):
                            if fn.endswith(".json"):
                                zf.write(os.path.join(bdir, fn), f"users/{name}/backups/{fn}")
    return buf.getvalue()


def restore_family_backup(
    zip_bytes: bytes,
    mode: str = "merge",
) -> dict[str, Any]:
    """Restore a family zip.

    mode=merge    : keep existing app.sqlite users if conflict; overwrite user timeline.db files
    mode=replace  : overwrite app.sqlite and all included user DBs (dangerous)
    """
    if mode not in ("merge", "replace"):
        raise ValueError("mode 必须是 merge 或 replace")

    restored_users: list[str] = []
    warnings: list[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names and "app.sqlite" not in names:
            raise ValueError("不是有效的全家备份 zip")

        # restore app.sqlite
        if "app.sqlite" in names:
            target_app = os.path.join(DATA_DIR, "app.sqlite")
            if mode == "replace" or not os.path.isfile(target_app):
                # close potential locks by not opening it here; just write file
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(target_app, "wb") as f:
                    f.write(zf.read("app.sqlite"))
            else:
                # merge: keep existing accounts; still copy invite codes? skip to avoid duplicate PKs
                warnings.append("merge 模式保留现有账号，不覆盖 app.sqlite")

        # restore user DBs
        users_root = os.path.join(DATA_DIR, "users")
        os.makedirs(users_root, exist_ok=True)
        for name in zf.namelist():
            if not name.startswith("users/"):
                continue
            parts = name.split("/")
            if len(parts) < 3:
                continue
            user_id = parts[1]
            rel = "/".join(parts[2:])
            if not rel.endswith(".db") and not rel.endswith(".db-wal") and not rel.endswith(".db-shm"):
                if not rel.startswith("backups/"):
                    continue
            dest_dir = os.path.join(users_root, user_id)
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(zf.read(name))
            if rel == "timeline.db":
                restored_users.append(user_id)

    return {
        "ok": True,
        "mode": mode,
        "restored_users": sorted(set(restored_users)),
        "restored_count": len(set(restored_users)),
        "warnings": warnings,
        "message": f"已恢复 {len(set(restored_users))} 个用户的数据库（mode={mode}）",
    }


def backup_filename() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"timeline-family-backup-{stamp}.zip"
