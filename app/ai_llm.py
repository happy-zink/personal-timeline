"""Real LLM adapter — OpenAI-compatible chat completions.

Users can plug their own endpoint + key in Settings (per user), e.g.:
  AI_API_BASE=https://api.openai.com/v1
  AI_API_KEY=sk-...
  AI_MODEL=gpt-4o-mini

Flow:
  1) If a key is configured, call the model to extract structured JSON from text.
  2) Validate / clamp against known metrics.
  3) On failure or missing key, fall back to offline rule parser (ai_ingest).

Stdlib only (urllib). No third-party SDK required.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime
from typing import Any, Optional

from . import ai_ingest

DEFAULT_SYSTEM_PROMPT = """你是个人健康/生活数据录入助手。把用户的中文（或中英混合）一句话转成 JSON。

只输出 JSON，不要解释。格式：
{
  "date": "YYYY-MM-DD",
  "values": {
    "weight": number|null,
    "sleep_hours": number|null,
    "sleep_quality": 1-5|null,
    "mood": 1-5|null,
    "exercise_minutes": number|null,
    "exercise_type": string|null,
    "snore_burden": number|null
  },
  "note": "用户原话或简短备注"
}

规则：
- 日期默认本地今天；「昨天/前天/N天前/9月28日」要解析。
- 缺失的字段用 null，不要编造，不要写 0（除非用户明确说 0）。
- 体重统一换算成 kg（斤×0.5，lb×0.45359237）。
- 跨午夜睡眠记在起床日，sleep_hours 为总时长小时数。
- mood/睡眠质量：1=很差 … 5=很好；也可从「开心/一般/差」等词推断。
"""


def load_ai_config(user_id: str = "") -> dict[str, Any]:
    """Priority: per-user settings > env vars."""
    cfg = {
        "api_base": os.environ.get("AI_API_BASE", "").strip(),
        "api_key": os.environ.get("AI_API_KEY", "").strip(),
        "model": os.environ.get("AI_MODEL", "").strip(),
    }
    if user_id:
        try:
            from . import auth
            s = auth.get_all_settings(user_id)
            cfg["api_base"] = (s.get("ai_api_base") or cfg["api_base"]).strip()
            cfg["api_key"] = (s.get("ai_api_key") or cfg["api_key"]).strip()
            cfg["model"] = (s.get("ai_model") or cfg["model"]).strip()
        except Exception:
            pass
    if not cfg["model"]:
        cfg["model"] = "gpt-4o-mini"
    return cfg


def is_configured(user_id: str = "") -> bool:
    cfg = load_ai_config(user_id)
    return bool(cfg.get("api_key"))


def _chat(messages: list[dict[str, str]], cfg: dict[str, Any], timeout: float = 25.0) -> str:
    base = cfg["api_base"].rstrip("/")
    if not base:
        base = "https://api.openai.com/v1"
    if not base.startswith("http"):
        base = "https://" + base
    url = base + "/chat/completions"
    body = {
        "model": cfg.get("model") or "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload["choices"][0]["message"]["content"]


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|```$", "", text.strip(), flags=re.I).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))
        raise


def _clamp_values(values: dict[str, Any]) -> dict[str, Any]:
    """Keep only known metrics and coerce types / range."""
    known_num = {
        "weight": (20, 300),
        "sleep_hours": (0, 24),
        "sleep_quality": (1, 5),
        "mood": (1, 5),
        "exercise_minutes": (0, 1440),
        "snore_burden": (0, 100),
    }
    out: dict[str, Any] = {}
    for k, v in (values or {}).items():
        if v is None:
            continue
        if k in known_num:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            lo, hi = known_num[k]
            if fv < lo or fv > hi:
                # out of range — drop rather than write garbage
                continue
            if k in ("sleep_quality", "mood", "exercise_minutes", "snore_burden"):
                out[k] = int(round(fv)) if k != "snore_burden" else round(fv, 1)
            else:
                out[k] = round(fv, 3)
        elif k in ("exercise_type", "sleep_note"):
            s = str(v).strip()[:80]
            if s:
                out[k] = s
    return out


def parse_with_llm(text: str, user_id: str = "", default_date: Optional[date] = None) -> dict[str, Any]:
    """Try real LLM; always return a draft. Never raises to caller for network errors."""
    cfg = load_ai_config(user_id)
    if not cfg.get("api_key"):
        draft = ai_ingest.parse_personal_text(text, default_date=default_date)
        draft = ai_ingest.enrich_suggestions(draft)
        draft["parser"] = "rule-based-offline-v1"
        draft["llm_used"] = False
        return draft

    d = (default_date or date.today()).isoformat()
    try:
        content = _chat(
            [
                {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"今天是 {d}（本地日历日）。请解析：\n{text}",
                },
            ],
            cfg,
        )
        raw = _extract_json(content)
        vals = _clamp_values(raw.get("values") or {})
        date_str = raw.get("date") or d
        try:
            date_str = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            date_str = d
        draft = {
            "ok": True,
            "date": date_str,
            "values": vals,
            "note": str(raw.get("note") or text)[:500],
            "timezone": "",
            "parser": f"llm:{cfg.get('model')}",
            "llm_used": True,
            "raw_text": text,
            "suggestions": [],
            "confidence": {"source": "llm"},
        }
        return ai_ingest.enrich_suggestions(draft)
    except Exception as exc:
        draft = ai_ingest.parse_personal_text(text, default_date=default_date)
        draft = ai_ingest.enrich_suggestions(draft)
        draft["parser"] = "rule-based-offline-v1"
        draft["llm_used"] = False
        draft["llm_error"] = str(exc)[:300]
        draft["suggestions"] = list(draft.get("suggestions") or []) + [
            "LLM 调用失败，已回退离线规则解析。可在设置里检查 AI API 配置。"
        ]
        return draft


def health(user_id: str = "") -> dict[str, Any]:
    cfg = load_ai_config(user_id)
    return {
        "configured": bool(cfg.get("api_key")),
        "api_base": cfg.get("api_base") or "https://api.openai.com/v1",
        "model": cfg.get("model"),
        "has_key": bool(cfg.get("api_key")),
    }
