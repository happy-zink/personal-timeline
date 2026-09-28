"""Natural-language → structured personal record parser (Chinese + mixed English).

Designed for the AI ingest API: a human (or another agent) can type
「今天体重68.5公斤，睡了7个半小时，心情不错，跑步30分钟」
and get a structured draft the user can confirm.

This is deterministic rule parsing — not an LLM call — so it works offline.
External agents can also call POST /api/ai/ingest with pre-structured JSON.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

# ---------- date ----------
_DATE_PATTERNS = [
    (r"(?i)\b(today|今天)\b", 0),
    (r"(?i)\b(yesterday|昨天)\b", -1),
    (r"(?i)\b(前天)\b", -2),
    (r"(?i)\b(tomorrow|明天)\b", 1),
    (r"(?i)\b(\d+)\s*天前", None),  # special
]

_CN_NUM = {
    "零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _cn_number(text: str) -> Optional[float]:
    """Parse a limited Chinese / arabic mixed number."""
    text = text.strip()
    if not text:
        return None
    # pure arabic / decimal
    try:
        return float(text)
    except ValueError:
        pass
    # 半
    if text == "半":
        return 0.5
    # 十二 / 二十 / 两百五十 etc. (limited)
    m = re.fullmatch(r"([零一两二三四五六七八九十]+)", text)
    if not m:
        return None
    s = m.group(1)
    if s == "十":
        return 10.0
    if s.startswith("十"):
        return 10 + _CN_NUM.get(s[1], 0)
    if "十" in s:
        a, b = s.split("十", 1)
        tens = _CN_NUM.get(a, 0) * 10
        ones = _CN_NUM.get(b, 0) if b else 0
        return float(tens + ones)
    return float(_CN_NUM.get(s, 0))


def parse_date_from_text(text: str, default: Optional[date] = None) -> tuple[date, str]:
    """Return (date, remaining_text_with_date_ish_removed_flag)."""
    today = default or date.today()
    # explicit ISO
    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d, text
        except ValueError:
            pass
    # Chinese 9月28日 / 9月28号
    m = re.search(r"(?:(20\d{2})年)?(\d{1,2})月(\d{1,2})[日号]", text)
    if m:
        year = int(m.group(1)) if m.group(1) else today.year
        try:
            d = date(year, int(m.group(2)), int(m.group(3)))
            return d, text
        except ValueError:
            pass
    # relative
    if re.search(r"(?i)前天", text):
        return today - timedelta(days=2), text
    if re.search(r"(?i)昨天|yesterday", text):
        return today - timedelta(days=1), text
    if re.search(r"(?i)明天|tomorrow", text):
        return today + timedelta(days=1), text
    if re.search(r"(?i)今天|today", text):
        return today, text
    m = re.search(r"(\d+)\s*天前", text)
    if m:
        return today - timedelta(days=int(m.group(1))), text
    return today, text


# ---------- value helpers ----------

def _num_after(text: str, patterns: list[str]) -> tuple[Optional[float], str]:
    """Find number after any pattern. Returns (value, matched_fragment)."""
    for pat in patterns:
        m = re.search(pat + r"\s*(?:[:：=约])?\s*([0-9]+(?:\.[0-9]+)?|[零一两二三四五六七八九十]+(?:\.[0-9]+)?(?:半)?)", text)
        if m:
            raw = m.group(1)
            if raw.endswith("半") and raw != "半":
                base = _cn_number(raw[:-1]) if not re.match(r"^[0-9]", raw) else float(raw[:-1])
                val = (base or 0) + 0.5
            else:
                val = _cn_number(raw)
            if val is not None:
                return float(val), m.group(0)
    return None, ""


_MOOD_WORDS = {
    # score 1-5
    5: ["很开心", "非常开心", "超开心", "特别好", "非常好", "极好", "很棒", "太棒", "满分", "完美", "excellent", "amazing", "很棒", "心情好", "心情很好", "愉悦", "狂喜"],
    4: ["开心", "不错", "挺好的", "很好", "好", "愉快", "舒服", "顺心", "good", "happy", "心情不错", "状态不错", "还行"],
    3: ["一般", "还行", "普通", "平常", "马马虎虎", "so so", "ok", "okay", "中等", "平淡"],
    2: ["不太好", "有点差", "郁闷", "低落", "累", "疲惫", "bad", "心情差", "烦躁", "焦虑", "压力"],
    1: ["很差", "非常差", "糟糕", "崩溃", "痛苦", "难受", "绝望", "terrible", "很糟", "抑郁"],
}


def parse_mood(text: str) -> Optional[int]:
    # explicit score
    m = re.search(r"(?i)(心情|情绪|mood)\s*(?:[:：=])?\s*([1-5])(?:\s*分)?", text)
    if m:
        return int(m.group(2))
    # words — pick the strongest match order (5→1)
    for score, words in sorted(_MOOD_WORDS.items(), key=lambda x: -x[0]):
        for w in words:
            if w in text:
                return score
    return None


_SLEEP_QUALITY_WORDS = {
    5: ["睡得特别好", "睡得很好", "深睡", "一夜好眠", "非常解乏", "睡得香"],
    4: ["睡得不错", "睡得挺好", "睡得好", "解乏", "睡得还行"],
    3: ["睡眠一般", "一般", "马马虎虎"],
    2: ["睡得不好", "没睡好", "浅睡", "多梦", "醒了好几次", "入睡困难"],
    1: ["失眠", "几乎没睡", "彻夜难眠", "完全没睡", "严重失眠"],
}


def parse_sleep_quality(text: str) -> Optional[int]:
    m = re.search(r"(?i)(睡眠(?:主观)?(?:感受|评分|质量)|sleep[_ ]?quality)\s*(?:[:：=])?\s*([1-5])(?:\s*分)?", text)
    if m:
        return int(m.group(2))
    for score, words in sorted(_SLEEP_QUALITY_WORDS.items(), key=lambda x: -x[0]):
        for w in words:
            if w in text:
                return score
    return None


def parse_duration_minutes(text: str, keywords: list[str]) -> Optional[float]:
    """Parse 做了X 30分钟 / 跑步半小时 / 动了1.5h"""
    for kw in keywords:
        # number + unit after keyword
        m = re.search(
            rf"{kw}\s*(?:了|约|大概|差不多)?\s*([0-9]+(?:\.[0-9]+)?|[零一两二三四五六七八九十半]+)\s*(小时|分钟|个半小时|h|min|分钟|小时)",
            text,
        )
        if m:
            raw, unit = m.group(1), m.group(2)
            if raw == "半" or raw.endswith("半"):
                base = 0.5 if raw == "半" else (_cn_number(raw[:-1]) or 0) + 0.5
                # 个半小时 = n + 0.5 hours
                if "小时" in unit or unit == "h":
                    return float(base) * 60
                return float(base)
            val = _cn_number(raw) if not re.match(r"^[0-9]", raw) else float(raw)
            if val is None:
                continue
            if unit in ("小时", "h", "小时"):
                return val * 60
            return val
        # keyword alone with bare number + default minutes
        m = re.search(rf"{kw}\s*(?:了|约)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?!\s*(?:小时|分钟|h|min))", text)
        if m:
            return float(m.group(1))
    return None


def parse_sleep_hours(text: str) -> Optional[float]:
    # 睡了7个半小时
    m = re.search(r"(?:睡(?:了|眠)?|睡眠时长|sleep)\s*([0-9]+(?:\.[0-9]+)?|[零一两二三四五六七八九十]+)?\s*个半小时", text)
    if m:
        base = float(m.group(1)) if m.group(1) and re.match(r"^[0-9]", m.group(1)) else (_cn_number(m.group(1) or "零") or 0)
        return base + 0.5
    m = re.search(r"(?:睡(?:了|眠)?|睡眠时长|sleep)\s*(?:约|大概|了)?\s*([0-9]+(?:\.[0-9]+)?|[零一两二三四五六七八九十半]+)\s*(个)?小时", text)
    if m:
        raw = m.group(1)
        if raw in ("半",) or raw.endswith("半"):
            base = 0.5 if raw == "半" else (_cn_number(raw[:-1]) or 0) + 0.5
            return float(base)
        val = float(raw) if re.match(r"^[0-9]", raw) else (_cn_number(raw) or 0)
        return float(val)
    # 小时数
    m = re.search(r"(?:睡(?:了|眠)?|睡眠)\s*(?:约|大概)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:h|H)\b", text)
    if m:
        return float(m.group(1))
    # 只睡了5个小时 / 睡眠 7.2
    val, _ = _num_after(text, [r"(?:睡(?:了|眠)?|睡眠时长)"])
    if val is not None and 0 <= val <= 24:
        # only if a time unit nearby or clearly sleep context
        if re.search(r"睡|睡眠", text):
            return val
    return None


def parse_weight(text: str) -> Optional[float]:
    m = re.search(
        r"(?:体重|weigh(?:t|ed)?|重(?:量)?)\s*(?:[:：=约是])?\s*([0-9]+(?:\.[0-9]+)?|[零一两二三四五六七八九十]+(?:\.[0-9]+)?)\s*(kg|KG|kG|公斤|千克|斤|磅|lb)?",
        text,
    )
    if not m:
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|公斤|千克)", text, re.I)
        if m:
            return float(m.group(1))
        return None
    val = float(m.group(1)) if re.match(r"^[0-9]", m.group(1)) else (_cn_number(m.group(1)) or 0)
    unit = (m.group(2) or "kg").lower()
    if unit == "斤":
        return val * 0.5
    if unit in ("磅", "lb"):
        return val * 0.45359237
    return float(val)


def parse_weight_with_unit(text: str) -> tuple[Optional[float], str]:
    m = re.search(
        r"(?:体重|weight)\s*(?:[:：=约是])?\s*([0-9]+(?:\.[0-9]+)?)\s*(kg|公斤|千克|斤|磅|lb)?",
        text,
        re.I,
    )
    if not m:
        m2 = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(kg|公斤|千克|斤|磅|lb)", text, re.I)
        if m2:
            val = float(m2.group(1))
            unit = m2.group(2).lower()
            if unit == "斤":
                return val * 0.5, "kg"
            if unit in ("磅", "lb"):
                return val * 0.45359237, "kg"
            return val, "kg"
        return None, ""
    val = float(m.group(1))
    unit = (m.group(2) or "kg").lower()
    if unit == "斤":
        return val * 0.5, "kg"
    if unit in ("磅", "lb"):
        return val * 0.45359237, "kg"
    return val, "kg"


def parse_exercise(text: str) -> tuple[Optional[float], Optional[str]]:
    # Negations first: 没运动 / 未锻炼 / 没跑步
    if re.search(r"(没|没有|未|无)\s*(去)?\s*(运动|锻炼|跑步|健身)", text):
        return 0.0, None
    minutes = parse_duration_minutes(
        text,
        ["运动", "锻炼", "跑步", "慢跑", "快走", "走路", "骑行", "骑车", "游泳", "健身", "力量", "瑜伽", "跳绳", "爬山", "球类", "散步"],
    )
    ex_type = None
    for t in ["跑步", "慢跑", "快走", "走路", "散步", "骑行", "骑车", "游泳", "健身", "力量", "瑜伽", "跳绳", "爬山", "球类", "运动", "锻炼"]:
        if t in text and not re.search(rf"(没|没有|未)\s*(去)?\s*{t}", text):
            ex_type = {"慢跑": "慢跑", "快走": "快走", "走路": "快走", "散步": "散步", "骑行": "骑行", "骑车": "骑行",
                       "游泳": "游泳", "健身": "健身", "力量": "力量", "瑜伽": "瑜伽", "跳绳": "跳绳",
                       "爬山": "爬山", "球类": "球类", "跑步": "跑步", "运动": "运动", "锻炼": "锻炼"}.get(t, t)
            break
    return minutes, ex_type


def extract_note(text: str, consumed: list[str]) -> str:
    """Heuristic leftover as note — remove matched fragments, strip filler."""
    rest = text
    for frag in consumed:
        if frag:
            rest = rest.replace(frag, " ")
    # remove common metric mentions leftovers
    rest = re.sub(r"(?i)体重|睡眠|心情|运动|备注|note|weight|sleep|mood", " ", rest)
    rest = re.sub(r"[，。,.!！;；:：\s]+", " ", rest).strip()
    return rest


def parse_personal_text(text: str, default_date: Optional[date] = None) -> dict[str, Any]:
    """Parse free text into a draft record."""
    if not text or not text.strip():
        return {"ok": False, "error": "文本为空"}

    original = text.strip()
    d, _ = parse_date_from_text(original, default_date)
    consumed: list[str] = []

    weight, _wu = parse_weight_with_unit(original)
    if weight is not None:
        m = re.search(r"(?:体重|weight)?\s*[:：=约是]?\s*[0-9]+(?:\.[0-9]+)?\s*(?:kg|公斤|千克|斤|磅|lb)?", original, re.I)
        # mark loosely
        consumed.append(str(weight))

    sleep_hours = parse_sleep_hours(original)
    sleep_quality = parse_sleep_quality(original)
    mood = parse_mood(original)
    ex_min, ex_type = parse_exercise(original)
    snore = None
    m = re.search(r"(?:鼾声|打鼾|snore)\s*[:：=]?\s*([0-9]+(?:\.[0-9]+)?)", original, re.I)
    if m:
        snore = float(m.group(1))

    values: dict[str, Any] = {}
    if weight is not None:
        values["weight"] = round(weight, 3)
    if sleep_hours is not None:
        values["sleep_hours"] = round(sleep_hours, 3)
    if sleep_quality is not None:
        values["sleep_quality"] = sleep_quality
    if mood is not None:
        values["mood"] = mood
    if ex_min is not None:
        values["exercise_minutes"] = round(ex_min, 1)
    if ex_type:
        values["exercise_type"] = ex_type
    if snore is not None:
        values["snore_burden"] = snore

    # note: keep full original as note if anything left interesting
    note = original
    # if we extracted things, still keep original as the note (user narrative)
    # but mark parsed fields

    return {
        "ok": True,
        "date": d.isoformat(),
        "values": values,
        "note": note,
        "timezone": "",
        "confidence": {
            "date": "high" if re.search(r"今天|昨天|前天|\d+月\d+|20\d{2}", original) else "default_today",
            "weight": "high" if weight is not None else None,
            "sleep_hours": "high" if sleep_hours is not None else None,
            "mood": "medium" if mood is not None else None,
            "exercise": "high" if ex_min is not None else None,
        },
        "suggestions": [],
        "parser": "rule-based-offline-v1",
        "raw_text": original,
    }


def enrich_suggestions(draft: dict[str, Any]) -> dict[str, Any]:
    vals = draft.get("values") or {}
    sug: list[str] = []
    if not vals:
        sug.append("没有识别到指标，可改用结构化 JSON 调用 /api/ai/ingest")
    if "sleep_hours" in vals and "sleep_quality" not in vals:
        sug.append("识别到睡眠时长，可补一句睡眠感受（1-5 或「睡得不错」）")
    if "weight" in vals and "mood" not in vals:
        sug.append("可补充心情，便于相关分析")
    if "exercise_minutes" in vals and "exercise_type" not in vals:
        sug.append("可补充运动类型")
    draft["suggestions"] = sug
    return draft
