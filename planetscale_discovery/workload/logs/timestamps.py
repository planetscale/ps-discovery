import re
from datetime import datetime, timezone
from typing import Any, Optional


def _strip_utc_gmt_suffix(text: str) -> str:
    for suffix in (" UTC", " GMT"):
        if text.endswith(suffix):
            return text[: -len(suffix)] + "+00:00"
    return text


def instant_utc(value: Any, log_timezone: Optional[str] = None) -> Optional[datetime]:
    if isinstance(value, datetime):
        instant = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        stripped = _strip_utc_gmt_suffix(text)
        if stripped.endswith("Z"):
            stripped = stripped[:-1] + "+00:00"
        try:
            instant = datetime.fromisoformat(stripped)
        except ValueError:
            head, _, zone = text.rpartition(" ")
            instant = _resolve_abbreviation(head, log_timezone) if head else None
            if instant is None:
                return None
    if instant.tzinfo is None:
        return None
    return instant.astimezone(timezone.utc)


def _looks_like_offset(zone: str) -> bool:
    return zone.startswith("+") or zone.startswith("-")


def _resolve_abbreviation(
    stamp: str, log_timezone: Optional[str]
) -> Optional[datetime]:
    if not log_timezone:
        return None
    try:
        from zoneinfo import ZoneInfo

        naive = datetime.fromisoformat(stamp.strip())
        return naive.replace(tzinfo=ZoneInfo(log_timezone))
    except Exception:
        return None


def to_rfc3339(log_time: Any, log_timezone: Optional[str] = None) -> str:
    text = str(log_time or "").strip()
    if not text:
        return ""
    text = text.replace(" UTC", "+00:00")
    if text.endswith(" GMT"):
        text = text[:-4] + "+00:00"
    head, sep, tail = text.partition(" ")
    if sep and tail:
        rest = tail
        if " " in rest:
            stamp, _, zone = rest.rpartition(" ")
            if _looks_like_offset(zone):
                rest = f"{stamp}{zone}"
            else:
                resolved = _resolve_abbreviation(f"{head} {stamp}", log_timezone)
                if resolved is not None:
                    return resolved.isoformat(timespec="milliseconds")
                rest = stamp
        rest = re.sub(r"([+-]\d{2})$", r"\1:00", rest)
        return f"{head}T{rest}"
    return text


def parse_window_bound(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    cleaned = str(text).strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    cleaned = cleaned.replace("T", " ", 1)
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def parse_log_stamp(text: str) -> Optional[datetime]:
    cleaned = str(text).replace(" UTC", "").replace(" GMT", "").strip()
    cleaned = re.sub(r"\s[A-Z]{2,5}$", "", cleaned)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned[:26], fmt)
        except ValueError:
            continue
    try:
        iso = cleaned[:-1] + "+00:00" if cleaned.endswith("Z") else cleaned
        return datetime.fromisoformat(iso).replace(tzinfo=None)
    except ValueError:
        return None
