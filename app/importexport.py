"""CSV / JSON import-export helpers.

Rules:
- Import never silently overwrites. Duplicates require an explicit strategy.
- Preview returns sample rows and parse issues without writing.
- Unit conversion is explicit via the mapping.
- JSON backup is a complete restore payload.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from . import db

# Common Project-01 style snore CSV aliases (column name -> logical field)
SNORE_FIELD_ALIASES = {
    "date": ["date", "day", "record_date", "日期", "记录日期"],
    "snore_burden": ["snore_burden", "snore_score", "burden", "score", "鼾声负担", "鼾声评分", "负担分"],
    "snore_minutes": ["snore_minutes", "duration", "minutes", "鼾声时长", "时长", "分钟"],
    "weight": ["weight", "weight_kg", "体重", "体重kg"],
    "sleep_quality": ["sleep_quality", "quality", "sleep_score", "睡眠评分", "睡眠主观感受", "质量"],
    "note": ["note", "notes", "remark", "备注", "说明"],
}


def read_csv_text(text: str) -> list[dict[str, str]]:
    # Strip BOM
    if text.startswith("﻿"):
        text = text[1:]
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for row in reader:
        cleaned = {(k or "").strip(): ("" if v is None else str(v).strip()) for k, v in row.items()}
        # skip completely empty rows
        if any(cleaned.values()):
            rows.append(cleaned)
    return rows


def guess_date_format(samples: list[str]) -> str:
    formats = ["%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d", "%d-%m-%Y"]
    for fmt in formats:
        ok = 0
        for s in samples:
            s = s.strip()
            if not s:
                continue
            try:
                datetime.strptime(s, fmt)
                ok += 1
            except ValueError:
                ok = -1
                break
        if ok > 0:
            return fmt
    return "%Y-%m-%d"


def parse_date(value: str, date_format: str) -> str:
    value = (value or "").strip()
    if not value:
        raise ValueError("日期为空")
    # Allow ISO datetime, take date part
    if "T" in value or " " in value:
        value = value.replace("T", " ").split(" ")[0]
    try:
        return datetime.strptime(value, date_format).date().isoformat()
    except ValueError:
        # fallback ISO
        try:
            return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ValueError(f"日期无法解析: {value!r}（格式 {date_format}）") from exc


UNIT_FACTORS = {
    # to base unit
    "weight": {
        "kg": 1.0,
        "g": 0.001,
        "lb": 0.45359237,
        "斤": 0.5,
    },
    "sleep_hours": {
        "小时": 1.0,
        "h": 1.0,
        "hour": 1.0,
        "hours": 1.0,
        "分钟": 1.0 / 60.0,
        "min": 1.0 / 60.0,
        "minutes": 1.0 / 60.0,
    },
}


def convert_value(metric_key: str, raw: str, from_unit: str) -> float:
    try:
        num = float(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"数值无法解析: {raw!r}") from exc
    from_unit = (from_unit or "").strip()
    factors = UNIT_FACTORS.get(metric_key)
    if factors and from_unit:
        if from_unit not in factors:
            raise ValueError(f"指标 {metric_key} 不支持单位: {from_unit}")
        return num * factors[from_unit]
    return num


def preview_import(
    text: str,
    mapping: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    rows = read_csv_text(text)
    if not rows:
        return {"ok": False, "error": "CSV 为空或无法解析", "rows": [], "issues": [], "preview": []}

    headers = list(rows[0].keys())
    date_col = mapping.get("date")
    if not date_col or date_col not in headers:
        return {
            "ok": False,
            "error": "缺少日期列映射。请把某一列映射为 date。",
            "headers": headers,
            "rows": rows[:50],
            "issues": [],
            "preview": [],
        }

    date_format = options.get("date_format") or guess_date_format([r.get(date_col, "") for r in rows[:20]])
    field_map: dict[str, str] = mapping.get("fields") or {}
    unit_map: dict[str, str] = mapping.get("units") or {}
    note_col = mapping.get("note")
    timezone = options.get("timezone") or ""

    metrics = {m["key"]: m for m in db.list_metrics(include_inactive=True)}
    issues: list[dict[str, Any]] = []
    planned: list[dict[str, Any]] = []
    seen_dates: dict[str, int] = {}

    for idx, row in enumerate(rows, start=1):
        raw_date = row.get(date_col, "")
        try:
            d = parse_date(raw_date, date_format)
        except ValueError as e:
            issues.append({"row": idx, "type": "date_error", "message": str(e), "data": row})
            continue

        values: dict[str, Any] = {}
        for col, metric_key in field_map.items():
            if not metric_key or col not in row:
                continue
            raw = row.get(col, "")
            if raw == "" or raw is None:
                continue
            metric = metrics.get(metric_key)
            if not metric:
                issues.append({"row": idx, "type": "unknown_metric", "message": f"未知指标 {metric_key}", "data": row})
                continue
            if metric["value_type"] == "text":
                values[metric_key] = raw
            else:
                try:
                    values[metric_key] = convert_value(metric_key, raw, unit_map.get(metric_key) or metric.get("unit") or "")
                except ValueError as e:
                    issues.append({"row": idx, "type": "value_error", "message": str(e), "data": row})

        note = row.get(note_col, "") if note_col else ""
        seen_dates[d] = seen_dates.get(d, 0) + 1
        planned.append(
            {
                "row": idx,
                "date": d,
                "values": values,
                "note": note,
                "duplicate_index": seen_dates[d],
            }
        )

    # Determine existing conflicts
    duplicate_strategy = options.get("duplicate_strategy") or "skip"
    conflicts = []
    to_apply = []
    for item in planned:
        existing = db.get_entry(item["date"])
        is_dup_in_file = item["duplicate_index"] > 1
        if existing or is_dup_in_file:
            conflicts.append(
                {
                    **item,
                    "existing": existing,
                    "is_dup_in_file": is_dup_in_file,
                    "has_existing": bool(existing),
                }
            )
            if existing and duplicate_strategy == "skip":
                continue
            if is_dup_in_file and duplicate_strategy == "skip":
                # keep first occurrence only
                continue
        to_apply.append(item)

    if options.get("dry_run_only_preview"):
        pass

    return {
        "ok": len(issues) == 0 or len(to_apply) > 0,
        "headers": headers,
        "date_format": date_format,
        "row_count": len(rows),
        "planned_count": len(planned),
        "apply_count": len(to_apply),
        "conflict_count": len(conflicts),
        "issues": issues,
        "preview": planned[:20],
        "conflicts": conflicts[:20],
        "mapping_used": {"date": date_col, "fields": field_map, "note": note_col, "units": unit_map},
        "options_used": {"date_format": date_format, "duplicate_strategy": duplicate_strategy, "timezone": timezone},
    }


def commit_import(
    text: str,
    mapping: dict[str, Any],
    options: dict[str, Any],
) -> dict[str, Any]:
    """Commit import. Never silently overwrite.

    duplicate_strategy:
      - skip: keep existing, skip incoming
      - overwrite: replace existing entry values that appear in the file
      - merge_empty: only fill missing metric values
      - fail: abort if any conflict
    """
    preview = preview_import(text, mapping, options)
    if not preview.get("ok") and preview.get("error"):
        return preview

    strategy = (options.get("duplicate_strategy") or "skip").lower()
    if strategy not in ("skip", "overwrite", "merge_empty", "fail"):
        raise ValueError("duplicate_strategy 必须是 skip/overwrite/merge_empty/fail")

    rows = read_csv_text(text)
    headers = list(rows[0].keys()) if rows else []
    date_col = mapping.get("date")
    date_format = options.get("date_format") or preview.get("date_format") or "%Y-%m-%d"
    field_map: dict[str, str] = mapping.get("fields") or {}
    unit_map: dict[str, str] = mapping.get("units") or {}
    note_col = mapping.get("note")
    timezone = options.get("timezone") or ""
    metrics = {m["key"]: m for m in db.list_metrics(include_inactive=True)}

    applied = 0
    skipped = 0
    merged = 0
    overwritten = 0
    errors: list[dict[str, Any]] = []
    file_seen: set[str] = set()

    # Pre-check conflicts for fail strategy
    planned_dates = []
    for idx, row in enumerate(rows, start=1):
        try:
            d = parse_date(row.get(date_col, ""), date_format)
            planned_dates.append(d)
        except ValueError:
            continue
    if strategy == "fail":
        existing_hits = []
        with db.connect() as conn:
            for d in planned_dates:
                if d in file_seen or conn.execute("SELECT 1 FROM entries WHERE date = ?", (d,)).fetchone():
                    if d in file_seen:
                        existing_hits.append({"date": d, "reason": "文件内重复"})
                    else:
                        existing_hits.append({"date": d, "reason": "数据库已有记录"})
                file_seen.add(d)
        if existing_hits:
            return {
                "ok": False,
                "error": "存在重复记录，导入已取消（策略 fail）",
                "conflicts": existing_hits,
                "applied": 0,
            }

    file_seen.clear()
    for idx, row in enumerate(rows, start=1):
        try:
            d = parse_date(row.get(date_col, ""), date_format)
        except ValueError as e:
            errors.append({"row": idx, "message": str(e)})
            continue

        values = {}
        for col, metric_key in field_map.items():
            if not metric_key or col not in row:
                continue
            raw = row.get(col, "")
            if raw == "" or raw is None:
                continue
            metric = metrics.get(metric_key)
            if not metric:
                continue
            if metric["value_type"] == "text":
                values[metric_key] = raw
            else:
                try:
                    values[metric_key] = convert_value(metric_key, raw, unit_map.get(metric_key) or metric.get("unit") or "")
                except ValueError as e:
                    errors.append({"row": idx, "message": str(e)})
        note = row.get(note_col, "") if note_col else ""
        is_dup_in_file = d in file_seen
        file_seen.add(d)

        existing = db.get_entry(d)
        if (existing or is_dup_in_file) and strategy == "skip":
            skipped += 1
            continue

        if existing and strategy == "merge_empty":
            existing_values = existing.get("values") or {}
            to_fill = {k: v for k, v in values.items() if k not in existing_values or existing_values[k].get("value") in (None, "")}
            if not to_fill and not (note and not existing.get("note")):
                skipped += 1
                continue
            payload = {"note": existing.get("note") or note, "timezone": timezone, "values": to_fill}
            # merge_empty only writes missing fields
            try:
                db.save_entry(d, payload)
                merged += 1
            except ValueError as e:
                errors.append({"row": idx, "message": str(e)})
            continue

        if existing and strategy == "overwrite":
            # Overwrite only keys present in file; preserve others by loading existing then updating.
            merged_values = dict(existing.get("values") or {})
            # save_entry with null removes; so build full payload carefully
            payload_values = {}
            for k, v in (existing.get("values") or {}).items():
                payload_values[k] = v.get("value")
            payload_values.update(values)
            # If a key was in existing but we want overwrite of file keys only, that's fine.
            note_to_use = note if note else (existing.get("note") or "")
            try:
                db.save_entry(d, {"note": note_to_use, "timezone": timezone, "values": payload_values})
                overwritten += 1
                applied += 1
            except ValueError as e:
                errors.append({"row": idx, "message": str(e)})
            continue

        # fresh insert (or skip already handled)
        try:
            db.save_entry(d, {"note": note, "timezone": timezone, "values": values})
            applied += 1
        except ValueError as e:
            errors.append({"row": idx, "message": str(e)})

    return {
        "ok": len(errors) == 0,
        "applied": applied,
        "skipped": skipped,
        "merged": merged,
        "overwritten": overwritten,
        "errors": errors,
        "date_format": date_format,
        "duplicate_strategy": strategy,
        "message": (
            f"导入完成：写入 {applied} 条，跳过 {skipped} 条，合并 {merged} 条，覆盖 {overwritten} 条，错误 {len(errors)} 条。"
        ),
    }


# ---------- export ----------

def export_csv(start: str = "1900-01-01", end: str = "2100-01-01") -> str:
    metrics = db.list_metrics()
    entries = db.list_entries(start, end)
    # date + note + all metric keys
    fieldnames = ["date", "note"] + [m["key"] for m in metrics]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for e in entries:
        row = {"date": e["date"], "note": e.get("note") or ""}
        for m in metrics:
            item = (e.get("values") or {}).get(m["key"])
            if item is None:
                row[m["key"]] = ""
            else:
                row[m["key"]] = item.get("value")
        writer.writerow(row)
    return buf.getvalue()


def export_json_backup() -> dict[str, Any]:
    metrics = db.list_metrics(include_inactive=True)
    entries = db.list_entries("1900-01-01", "2100-01-01")
    with db.connect() as conn:
        summaries = [
            dict(r)
            for r in conn.execute("SELECT year, month, summary_text FROM report_summaries ORDER BY year, month").fetchall()
        ]
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_meta").fetchall()}
    return {
        "format": "mimo-personal-timeline-backup",
        "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "timezone_note": "日期为本地日历日；跨午夜睡眠记在起床日。",
        "metrics": metrics,
        "entries": entries,
        "report_summaries": summaries,
        "meta": meta,
    }


def restore_json_backup(payload: dict[str, Any], mode: str = "replace") -> dict[str, Any]:
    """Restore backup. mode=replace clears current entries first; mode=merge overwrites by date."""
    if not isinstance(payload, dict) or payload.get("format") != "mimo-personal-timeline-backup":
        raise ValueError("不是有效的 Mimo 时光轴备份文件")
    metrics = payload.get("metrics") or []
    entries = payload.get("entries") or []
    summaries = payload.get("report_summaries") or []

    with db.connect() as conn:
        if mode == "replace":
            conn.execute("DELETE FROM entry_values")
            conn.execute("DELETE FROM entries")
            # keep builtin metrics, upsert custom
        elif mode != "merge":
            raise ValueError("mode 必须是 replace 或 merge")

        # Upsert metrics by key
        for m in metrics:
            if not m.get("key"):
                continue
            conn.execute(
                """INSERT INTO metrics(key, name, unit, category, value_type, min_value, max_value, decimals, sort_order, is_builtin, is_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     name=excluded.name, unit=excluded.unit, category=excluded.category,
                     value_type=excluded.value_type, min_value=excluded.min_value,
                     max_value=excluded.max_value, decimals=excluded.decimals,
                     sort_order=excluded.sort_order, is_active=excluded.is_active""",
                (
                    m.get("key"),
                    m.get("name") or m.get("key"),
                    m.get("unit") or "",
                    m.get("category") or "custom",
                    m.get("value_type") or "number",
                    m.get("min_value"),
                    m.get("max_value"),
                    int(m.get("decimals") or 2),
                    int(m.get("sort_order") or 100),
                    int(m.get("is_builtin") or 0),
                    int(m.get("is_active", 1)),
                ),
            )

        metric_by_key = {r["key"]: r for r in conn.execute("SELECT * FROM metrics").fetchall()}

        restored = 0
        for e in entries:
            d = e.get("date")
            if not d:
                continue
            note = e.get("note") or ""
            tz = e.get("timezone") or ""
            existing = conn.execute("SELECT id FROM entries WHERE date = ?", (d,)).fetchone()
            if existing:
                entry_id = existing["id"]
                conn.execute(
                    "UPDATE entries SET note=?, timezone=?, updated_at=datetime('now') WHERE id=?",
                    (note, tz, entry_id),
                )
                conn.execute("DELETE FROM entry_values WHERE entry_id = ?", (entry_id,))
            else:
                cur = conn.execute(
                    "INSERT INTO entries(date, note, timezone) VALUES (?,?,?)",
                    (d, note, tz),
                )
                entry_id = cur.lastrowid
            for key, item in (e.get("values") or {}).items():
                metric = metric_by_key.get(key)
                if not metric:
                    continue
                raw = item.get("value") if isinstance(item, dict) else item
                if raw is None or raw == "":
                    continue
                if metric["value_type"] == "text":
                    conn.execute(
                        "INSERT INTO entry_values(entry_id, metric_id, value_num, value_text) VALUES (?,?,?,?)",
                        (entry_id, metric["id"], None, str(raw)),
                    )
                else:
                    conn.execute(
                        "INSERT INTO entry_values(entry_id, metric_id, value_num, value_text) VALUES (?,?,?,?)",
                        (entry_id, metric["id"], float(raw), None),
                    )
            restored += 1

        for s in summaries:
            if s.get("year") is None or s.get("month") is None:
                continue
            conn.execute(
                """INSERT INTO report_summaries(year, month, summary_text, updated_at)
                   VALUES (?,?,?,datetime('now'))
                   ON CONFLICT(year, month) DO UPDATE SET
                     summary_text=excluded.summary_text, updated_at=excluded.updated_at""",
                (int(s["year"]), int(s["month"]), s.get("summary_text") or ""),
            )

    return {"ok": True, "mode": mode, "restored_entries": restored, "metrics": len(metrics), "summaries": len(summaries)}


# ---------- Project-01 snore CSV helper ----------

def map_snore_csv(text: str) -> dict[str, Any]:
    """Best-effort auto-mapping for Project-01 snore exports."""
    rows = read_csv_text(text)
    if not rows:
        return {"ok": False, "error": "CSV 为空", "mapping": {}, "headers": []}
    headers = list(rows[0].keys())
    lower = {h.lower(): h for h in headers}

    def find(logical: str) -> Optional[str]:
        for alias in SNORE_FIELD_ALIASES[logical]:
            if alias.lower() in lower:
                return lower[alias.lower()]
        return None

    date_col = find("date")
    fields = {}
    for logical in ("snore_burden", "snore_minutes", "weight", "sleep_quality"):
        col = find(logical)
        if col:
            # snore_minutes may map to snore_burden if that's all we have? keep as note field instead
            if logical == "snore_minutes":
                continue
            fields[col] = logical if logical in ("snore_burden", "weight", "sleep_quality") else logical
    note_col = find("note")

    if not date_col:
        return {
            "ok": False,
            "error": "未找到日期列。请手动映射 date 列。",
            "headers": headers,
            "rows": rows[:20],
            "mapping": {"date": None, "fields": fields, "note": note_col},
            "steps": [
                "1. 在字段映射界面选择哪一列是 date",
                "2. 将鼾声相关列映射到 snore_burden（或自定义指标）",
                "3. 可选映射 weight / sleep_quality",
                "4. 选择日期格式与重复记录策略后预览",
            ],
        }

    date_format = guess_date_format([r.get(date_col, "") for r in rows[:20]])
    return {
        "ok": True,
        "headers": headers,
        "rows": rows[:20],
        "mapping": {"date": date_col, "fields": fields, "note": note_col},
        "options": {"date_format": date_format, "duplicate_strategy": "skip"},
        "message": "已自动猜测字段映射，请确认后预览导入。未识别的列不会被写入。",
    }
