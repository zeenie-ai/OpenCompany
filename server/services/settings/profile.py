"""Owner-profile normalization for ``save_user_settings``.

The Normal-mode Profile form writes free text that later lands in every
hired employee's instructions and in the Home greeting, so it is cleaned on
the way in rather than trusted:

- text fields are trimmed, stripped of control characters, cut to the
  column's ``max_length`` (read off :class:`models.database.UserSettings`, so
  the model stays the single source), and stored as NULL when empty; the
  single-line fields also collapse internal whitespace, while
  ``profile_preferences`` keeps its line breaks;
- ``profile_timezone`` must name a real IANA zone (``zoneinfo``), otherwise
  the key is dropped and the stored value stands;
- the two switches must be real booleans; anything else is dropped rather
  than coerced (``"false"`` is truthy).

Keys this module does not own pass through untouched.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.logging import get_logger
from models.database import UserSettings

logger = get_logger(__name__)

SINGLE_LINE_FIELDS = ("profile_full_name", "profile_call_name", "profile_role")
MULTI_LINE_FIELDS = ("profile_preferences",)
BOOLEAN_FIELDS = ("memory_across_chats", "prefer_local_ai")
TIMEZONE_FIELD = "profile_timezone"

_WHITESPACE = re.compile(r"\s+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _max_length(field: str) -> int:
    for constraint in UserSettings.model_fields[field].metadata:
        limit = getattr(constraint, "max_length", None)
        if isinstance(limit, int):
            return limit
    raise LookupError(f"UserSettings.{field} declares no max_length")


# Bidi embedding / override / isolate controls: invisible, and able to make
# text read differently from what it says. Other format characters stay:
# ZWJ and ZWNJ are part of emoji sequences and of words in scripts such as
# Persian.
_BIDI_CONTROLS = frozenset("‪‫‬‭‮⁦⁧⁨⁩")


def _strip_controls(value: str, keep_newlines: bool) -> str:
    kept = []
    for ch in value:
        if ch == "\n" and keep_newlines:
            kept.append(ch)
        elif ch in "\t\r\n\v\f":
            kept.append(" ")
        elif ch in _BIDI_CONTROLS or unicodedata.category(ch) == "Cc":
            continue
        else:
            kept.append(ch)
    return "".join(kept)


def _clean_text(value: Any, field: str, multiline: bool) -> Optional[str]:
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_controls(text, keep_newlines=multiline)
    if multiline:
        lines = [_WHITESPACE.sub(" ", line).strip() for line in text.split("\n")]
        text = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    else:
        text = _WHITESPACE.sub(" ", text).strip()
    text = text[: _max_length(field)].rstrip()
    return text or None


def _clean_timezone(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > _max_length(TIMEZONE_FIELD):
        return None
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return name


def normalize_profile_patch(settings_data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a settings patch with the profile keys cleaned.

    Invalid profile values are removed from the patch (so the stored value
    stands) instead of failing the whole save.
    """
    patch = dict(settings_data)

    for field in SINGLE_LINE_FIELDS + MULTI_LINE_FIELDS:
        if field not in patch:
            continue
        value = patch[field]
        if value is None:
            continue
        if not isinstance(value, str):
            logger.debug("Dropping non-text profile value", field=field)
            del patch[field]
            continue
        patch[field] = _clean_text(value, field, multiline=field in MULTI_LINE_FIELDS)

    if TIMEZONE_FIELD in patch and patch[TIMEZONE_FIELD] is not None:
        zone = _clean_timezone(patch[TIMEZONE_FIELD])
        if zone is None:
            logger.debug("Dropping unknown profile timezone")
            del patch[TIMEZONE_FIELD]
        else:
            patch[TIMEZONE_FIELD] = zone

    for field in BOOLEAN_FIELDS:
        if field in patch and not isinstance(patch[field], bool):
            logger.debug("Dropping non-boolean profile switch", field=field)
            del patch[field]

    return patch
