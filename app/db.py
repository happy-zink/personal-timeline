"""SQLite data layer for Personal Data Timeline.

Dates are stored as local calendar strings YYYY-MM-DD (no timezone shift).
Sleep that crosses midnight is recorded under the wake date.
Missing metric values are stored as NULL and never coerced to 0.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "timeline.db")  # legacy / fallback

# Per-user DBs are selected at request time via set_current_db().
import threading
_local = threading.local()


def set_current_db(path: str) -> None:
    """Point subsequent db operations at a user-specific timeline.db."""
    _local.db_path = path


def current_db() -> str:
    return getattr(_local, "db_path", DB_PATH)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'custom',
    value_type TEXT NOT NULL DEFAULT 'number', -- number | scale | text
    min_value REAL,
    max_value REAL,
    decimals INTEGER NOT NULL DEFAULT 2,
    sort_order INTEGER NOT NULL DEFAULT 100,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS entry_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    metric_id INTEGER NOT NULL REFERENCES metrics(id) ON DELETE CASCADE,
    value_num REAL,
    value_text TEXT,
    UNIQUE(entry_id, metric_id)
);

CREATE TABLE IF NOT EXISTS report_summaries (
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    summary_text TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (year, month)
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'preview',
    mapping_json TEXT NOT NULL DEFAULT '{}',
    options_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entry_values_metric ON entry_values(metric_id);
CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(date);
"""

BUILTIN_METRICS = [
    # key, name, unit, category, value_type, min, max, decimals, sort_order
    ("weight", "体重", "kg", "body", "number", 20, 300, 1, 10),
    ("sleep_hours", "睡眠时长", "小时", "sleep", "number", 0, 24, 2, 20),
    ("sleep_quality", "睡眠主观感受", "分", "sleep", "scale", 1, 5, 0, 30),
    ("mood", "心情", "分", "mind", "scale", 1, 5, 0, 40),
    ("exercise_minutes", "运动时长", "分钟", "activity", "number", 0, 1440, 0, 50),
    ("exercise_type", "运动类型", "", "activity", "text", None, None, 0, 60),
    ("snore_burden", "鼾声负担", "分", "sleep", "number", 0, 100, 1, 70),
    ("sleep_note", "睡眠备注", "", "sleep", "text", None, None, 0, 80),
]


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


@contextmanager
def connect(db_path: Optional[str] = None):
    path = db_path or current_db()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # timeout + WAL make concurrent readers/writers from the threaded server safe
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 8000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    path = db_path or current_db()
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        existing = conn.execute("SELECT COUNT(*) AS c FROM metrics").fetchone()["c"]
        if existing == 0:
            for row in BUILTIN_METRICS:
                conn.execute(
                    """INSERT INTO metrics
                       (key, name, unit, category, value_type, min_value, max_value, decimals, sort_order, is_builtin)
                       VALUES (?,?,?,?,?,?,?,?,?,1)""",
                    row,
                )
        # First-run marker
        conn.execute(
            "INSERT OR IGNORE INTO app_meta(key, value) VALUES('initialized_at', ?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )


def dict_rows(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------- metrics ----------

def list_metrics(include_inactive: bool = False) -> list[dict[str, Any]]:
    with connect() as conn:
        sql = "SELECT * FROM metrics"
        if not include_inactive:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY sort_order, id"
        return dict_rows(conn.execute(sql).fetchall())


def get_metric_by_key(key: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM metrics WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None


def get_metric_by_id(metric_id: int) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM metrics WHERE id = ?", (metric_id,)).fetchone()
        return dict(row) if row else None


def create_metric(data: dict[str, Any]) -> dict[str, Any]:
    key = (data.get("key") or "").strip()
    name = (data.get("name") or "").strip()
    if not key or not name:
        raise ValueError("指标 key 与名称不能为空")
    if get_metric_by_key(key):
        raise ValueError(f"指标 key 已存在: {key}")
    value_type = data.get("value_type") or "number"
    if value_type not in ("number", "scale", "text"):
        raise ValueError("value_type 必须是 number/scale/text")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO metrics
               (key, name, unit, category, value_type, min_value, max_value, decimals, sort_order, is_builtin)
               VALUES (?,?,?,?,?,?,?,?,?,0)""",
            (
                key,
                name,
                data.get("unit") or "",
                data.get("category") or "custom",
                value_type,
                data.get("min_value"),
                data.get("max_value"),
                int(data.get("decimals") or 2),
                int(data.get("sort_order") or 100),
            ),
        )
        row = conn.execute("SELECT * FROM metrics WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def update_metric(metric_id: int, data: dict[str, Any]) -> dict[str, Any]:
    metric = get_metric_by_id(metric_id)
    if not metric:
        raise ValueError("指标不存在")
    fields = []
    values = []
    for col in ("name", "unit", "category", "value_type", "min_value", "max_value", "decimals", "sort_order", "is_active"):
        if col in data:
            fields.append(f"{col} = ?")
            values.append(data[col])
    if "value_type" in data and data["value_type"] not in ("number", "scale", "text"):
        raise ValueError("value_type 必须是 number/scale/text")
    if not fields:
        return metric
    values.append(metric_id)
    with connect() as conn:
        conn.execute(f"UPDATE metrics SET {', '.join(fields)} WHERE id = ?", values)
        row = conn.execute("SELECT * FROM metrics WHERE id = ?", (metric_id,)).fetchone()
        return dict(row)


def delete_metric(metric_id: int) -> None:
    metric = get_metric_by_id(metric_id)
    if not metric:
        raise ValueError("指标不存在")
    if metric["is_builtin"]:
        raise ValueError("内置指标不可删除，可选择停用")
    with connect() as conn:
        conn.execute("DELETE FROM metrics WHERE id = ?", (metric_id,))


# ---------- entries ----------

def normalize_date(value: str) -> str:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"无法识别日期: {value}")


def get_entry(date_str: str) -> Optional[dict[str, Any]]:
    d = normalize_date(date_str)
    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE date = ?", (d,)).fetchone()
        if not entry:
            return None
        values = conn.execute(
            """SELECT ev.metric_id, m.key, m.name, m.unit, m.value_type,
                      ev.value_num, ev.value_text
               FROM entry_values ev
               JOIN metrics m ON m.id = ev.metric_id
               WHERE ev.entry_id = ?
               ORDER BY m.sort_order""",
            (entry["id"],),
        ).fetchall()
        return {
            **dict(entry),
            "values": {
                v["key"]: {
                    "metric_id": v["metric_id"],
                    "name": v["name"],
                    "unit": v["unit"],
                    "value_type": v["value_type"],
                    "value": v["value_text"] if v["value_type"] == "text" else v["value_num"],
                }
                for v in values
            },
        }


def list_entries(start: str, end: str) -> list[dict[str, Any]]:
    start_d = normalize_date(start)
    end_d = normalize_date(end)
    with connect() as conn:
        rows = conn.execute(
            """SELECT ev.metric_id, m.key, m.name, m.unit, m.value_type,
                      e.date, e.note, ev.value_num, ev.value_text
               FROM entries e
               LEFT JOIN entry_values ev ON ev.entry_id = e.id
               LEFT JOIN metrics m ON m.id = ev.metric_id
               WHERE e.date BETWEEN ? AND ?
               ORDER BY e.date, m.sort_order""",
            (start_d, end_d),
        ).fetchall()
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            item = out.setdefault(
                r["date"],
                {"date": r["date"], "note": r["note"] or "", "values": {}},
            )
            if r["key"]:
                item["values"][r["key"]] = {
                    "value": r["value_text"] if r["value_type"] == "text" else r["value_num"],
                    "unit": r["unit"],
                    "name": r["name"],
                    "value_type": r["value_type"],
                }
        return [out[k] for k in sorted(out.keys())]


def save_entry(date_str: str, payload: dict[str, Any]) -> dict[str, Any]:
    d = normalize_date(date_str)
    note = payload.get("note")
    if note is None:
        note = ""
    timezone = payload.get("timezone") or ""
    values = payload.get("values") or {}
    metrics = {m["key"]: m for m in list_metrics(include_inactive=True)}

    with connect() as conn:
        entry = conn.execute("SELECT * FROM entries WHERE date = ?", (d,)).fetchone()
        if entry:
            entry_id = entry["id"]
            conn.execute(
                "UPDATE entries SET note = ?, timezone = ?, updated_at = datetime('now') WHERE id = ?",
                (str(note), timezone, entry_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO entries(date, note, timezone) VALUES (?,?,?)",
                (d, str(note), timezone),
            )
            entry_id = cur.lastrowid

        # Remove values explicitly set to null; upsert others.
        for key, raw in values.items():
            metric = metrics.get(key)
            if not metric:
                raise ValueError(f"未知指标: {key}")
            if raw is None or raw == "":
                conn.execute(
                    "DELETE FROM entry_values WHERE entry_id = ? AND metric_id = ?",
                    (entry_id, metric["id"]),
                )
                continue
            value_num = None
            value_text = None
            if metric["value_type"] == "text":
                value_text = str(raw)
            else:
                try:
                    value_num = float(raw)
                except (TypeError, ValueError):
                    raise ValueError(f"指标「{metric['name']}」需要数字，收到: {raw}")
                if metric["min_value"] is not None and value_num < float(metric["min_value"]):
                    raise ValueError(
                        f"指标「{metric['name']}」低于合理范围 {metric['min_value']}–{metric['max_value']}"
                    )
                if metric["max_value"] is not None and value_num > float(metric["max_value"]):
                    raise ValueError(
                        f"指标「{metric['name']}」高于合理范围 {metric['min_value']}–{metric['max_value']}"
                    )
            conn.execute(
                """INSERT INTO entry_values(entry_id, metric_id, value_num, value_text)
                   VALUES (?,?,?,?)
                   ON CONFLICT(entry_id, metric_id) DO UPDATE SET
                     value_num = excluded.value_num,
                     value_text = excluded.value_text""",
                (entry_id, metric["id"], value_num, value_text),
            )
    return get_entry(d)  # type: ignore[return-value]


def delete_entry(date_str: str) -> bool:
    d = normalize_date(date_str)
    with connect() as conn:
        cur = conn.execute("DELETE FROM entries WHERE date = ?", (d,))
        return cur.rowcount > 0


def missing_dates(start: str, end: str) -> list[str]:
    """Dates in [start, end] with no entry row at all."""
    start_d = date.fromisoformat(normalize_date(start))
    end_d = date.fromisoformat(normalize_date(end))
    with connect() as conn:
        existing = {
            r["date"]
            for r in conn.execute(
                "SELECT date FROM entries WHERE date BETWEEN ? AND ?",
                (start_d.isoformat(), end_d.isoformat()),
            ).fetchall()
        }
    out = []
    cur = start_d
    while cur <= end_d:
        if cur.isoformat() not in existing:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


# ---------- series / trends ----------

def metric_series(metric_keys: list[str], start: str, end: str) -> dict[str, list[dict[str, Any]]]:
    """Return date-keyed series. Missing days are included with value=None (never 0)."""
    start_d = date.fromisoformat(normalize_date(start))
    end_d = date.fromisoformat(normalize_date(end))
    metrics = {m["key"]: m for m in list_metrics(include_inactive=True)}
    for k in metric_keys:
        if k not in metrics:
            raise ValueError(f"未知指标: {k}")

    with connect() as conn:
        placeholders = ",".join("?" for _ in metric_keys)
        rows = conn.execute(
            f"""SELECT e.date, m.key, ev.value_num, ev.value_text, m.value_type
                FROM entries e
                JOIN entry_values ev ON ev.entry_id = e.id
                JOIN metrics m ON m.id = ev.metric_id
                WHERE e.date BETWEEN ? AND ? AND m.key IN ({placeholders})
                ORDER BY e.date""",
            [start_d.isoformat(), end_d.isoformat(), *metric_keys],
        ).fetchall()

    raw: dict[str, dict[str, Any]] = {k: {} for k in metric_keys}
    for r in rows:
        if r["value_type"] == "text":
            raw[r["key"]][r["date"]] = r["value_text"]
        else:
            raw[r["key"]][r["date"]] = r["value_num"]

    out: dict[str, list[dict[str, Any]]] = {}
    for k in metric_keys:
        series = []
        cur = start_d
        while cur <= end_d:
            ds = cur.isoformat()
            val = raw[k].get(ds)
            # Explicitly keep None for missing values.
            series.append({"date": ds, "value": val})
            cur += timedelta(days=1)
        out[k] = series
    return out


def aggregate_series(
    series: list[dict[str, Any]], granularity: str, value_type: str = "number"
) -> list[dict[str, Any]]:
    """Aggregate by day/week/month. Missing values stay missing (not zero-filled)."""
    if granularity == "day":
        return series
    buckets: dict[str, list[float]] = {}
    order: list[str] = []
    for item in series:
        d = date.fromisoformat(item["date"])
        if granularity == "week":
            # ISO week start Monday
            key = (d - timedelta(days=d.weekday())).isoformat()
        elif granularity == "month":
            key = d.replace(day=1).isoformat()
        else:
            raise ValueError("granularity 必须是 day/week/month")
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        if item["value"] is not None and value_type != "text":
            buckets[key].append(float(item["value"]))

    out = []
    for key in order:
        vals = buckets[key]
        if not vals:
            out.append({"date": key, "value": None, "n": 0})
        elif value_type == "scale":
            out.append({"date": key, "value": round(sum(vals) / len(vals), 2), "n": len(vals)})
        else:
            out.append({"date": key, "value": round(sum(vals) / len(vals), 2), "n": len(vals)})
    return out


def correlation(key_a: str, key_b: str, start: str, end: str) -> dict[str, Any]:
    """Pearson correlation on days where BOTH metrics have numeric values."""
    series = metric_series([key_a, key_b], start, end)
    xs: list[float] = []
    ys: list[float] = []
    for a, b in zip(series[key_a], series[key_b]):
        if a["value"] is not None and b["value"] is not None:
            try:
                xs.append(float(a["value"]))
                ys.append(float(b["value"]))
            except (TypeError, ValueError):
                continue
    n = len(xs)
    result = {
        "metric_a": key_a,
        "metric_b": key_b,
        "n": n,
        "r": None,
        "disclaimer": "这是描述性相关关系，不是因果结论。样本量有限时请谨慎解读。",
    }
    if n < 3:
        result["message"] = "有效配对样本不足 3 个，无法计算相关系数。"
        return result
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x = sum((x - mean_x) ** 2 for x in xs) ** 0.5
    den_y = sum((y - mean_y) ** 2 for y in ys) ** 0.5
    if den_x == 0 or den_y == 0:
        result["message"] = "存在常量序列，相关系数无法计算。"
        return result
    result["r"] = round(num / (den_x * den_y), 4)
    return result


def search_notes(query: str = "", start: str = "", end: str = "") -> list[dict[str, Any]]:
    sql = "SELECT date, note FROM entries WHERE note IS NOT NULL AND TRIM(note) != ''"
    params: list[Any] = []
    if start:
        sql += " AND date >= ?"
        params.append(normalize_date(start))
    if end:
        sql += " AND date <= ?"
        params.append(normalize_date(end))
    if query:
        sql += " AND note LIKE ?"
        params.append(f"%{query}%")
    sql += " ORDER BY date DESC"
    with connect() as conn:
        return dict_rows(conn.execute(sql, params).fetchall())


# ---------- calendar / month stats ----------

def calendar_month(year: int, month: int) -> dict[str, Any]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    entries = list_entries(start.isoformat(), end.isoformat())
    by_date = {e["date"]: e for e in entries}
    days = []
    missing = []
    cur = start
    while cur <= end:
        ds = cur.isoformat()
        entry = by_date.get(ds)
        if entry:
            days.append({"date": ds, "has_entry": True, "note": entry["note"], "values": entry["values"]})
        else:
            days.append({"date": ds, "has_entry": False, "note": "", "values": {}})
            missing.append(ds)
        cur += timedelta(days=1)
    return {
        "year": year,
        "month": month,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "missing_dates": missing,
        "entry_count": len(entries),
    }


def month_report(year: int, month: int) -> dict[str, Any]:
    cal = calendar_month(year, month)
    metrics = [m for m in list_metrics() if m["value_type"] != "text"]
    series_map = metric_series([m["key"] for m in metrics], cal["start"], cal["end"]) if metrics else {}
    stats = []
    for m in metrics:
        vals = [p["value"] for p in series_map.get(m["key"], []) if p["value"] is not None]
        if not vals:
            stats.append(
                {
                    "key": m["key"],
                    "name": m["name"],
                    "unit": m["unit"],
                    "n": 0,
                    "missing_days": cal["end"] and (
                        date.fromisoformat(cal["end"]).day - date.fromisoformat(cal["start"]).day + 1
                    ),
                    "mean": None,
                    "median": None,
                    "min": None,
                    "max": None,
                    "sum": None,
                }
            )
            continue
        vals_sorted = sorted(vals)
        n = len(vals)
        mid = n // 2
        median = vals_sorted[mid] if n % 2 == 1 else (vals_sorted[mid - 1] + vals_sorted[mid]) / 2
        stats.append(
            {
                "key": m["key"],
                "name": m["name"],
                "unit": m["unit"],
                "n": n,
                "missing_days": len(cal["missing_dates"]),
                "mean": round(sum(vals) / n, 3),
                "median": round(median, 3),
                "min": round(min(vals), 3),
                "max": round(max(vals), 3),
                "sum": round(sum(vals), 3),
            }
        )

    with connect() as conn:
        row = conn.execute(
            "SELECT summary_text FROM report_summaries WHERE year = ? AND month = ?",
            (year, month),
        ).fetchone()
        summary = row["summary_text"] if row else ""

    notes = [e for e in cal_days_notes(cal)]
    return {
        "year": year,
        "month": month,
        "start": cal["start"],
        "end": cal["end"],
        "entry_count": cal["entry_count"],
        "missing_dates": cal["missing_dates"],
        "stats": stats,
        "summary_text": summary,
        "notes": notes,
        "series": series_map,
        "timezone_note": "日期按本地日历日记录；跨午夜睡眠记在起床日。",
    }


def cal_days_notes(cal: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for d in cal["days"]:
        if d.get("note"):
            out.append({"date": d["date"], "note": d["note"]})
    return out


def save_report_summary(year: int, month: int, text: str) -> dict[str, Any]:
    with connect() as conn:
        conn.execute(
            """INSERT INTO report_summaries(year, month, summary_text, updated_at)
               VALUES (?,?,?,datetime('now'))
               ON CONFLICT(year, month) DO UPDATE SET
                 summary_text = excluded.summary_text,
                 updated_at = excluded.updated_at""",
            (year, month, text),
        )
    return {"year": year, "month": month, "summary_text": text}


def set_meta(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_meta(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
