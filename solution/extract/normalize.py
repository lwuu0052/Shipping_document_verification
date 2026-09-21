"""Field normalization (Section 5.2).

Pure functions, no I/O, no LLM. Same value written differently on SI vs BL
must collapse to the same canonical form here, otherwise Compare would emit a
storm of false mismatches and drown the escalation queue.

Public surface:
- :func:`normalize` — apply all rules to a fields dict, returns a new dict.
- :func:`find_missing` — list field names whose value is None / empty list.
"""

from __future__ import annotations

import re
from typing import Any

from .schema import FIELDS, LIST_FIELDS, NUMERIC_FIELDS, empty_fields

# ---------------------------------------------------------------------------
# Regexes compiled once. Numbers and units are case-insensitive; we strip
# whitespace and thousands separators before parsing.
# ---------------------------------------------------------------------------
# Section 5.2 says "去掉千分位逗号" — comma only. Period stays as a decimal
# point. Anglo SI/BL formatting uses commas for thousands; a period in a
# weight string is always a decimal (e.g. `138.000 MT` = 138 metric tons).
_THOUSAND_SEP = re.compile(r"(?<=\d),(?=\d{3}\b)")  # 1,234 only
_LEADING_TRAILING_WS = re.compile(r"^\s+|\s+$")
_MULTI_WS = re.compile(r"\s+")
_TRAILING_PERIOD = re.compile(r"\.+\s*$")
# Space immediately after a comma or period is almost always a formatting
# artifact (`CO., LTD` vs `CO.,LTD`). Removing it lets the two variants
# collapse to the same canonical form, which is what the dev doc requires.
_SPACE_AFTER_PUNCT = re.compile(r"([,\.])(\s+)(?=[A-Za-z])")

# Weight unit -> multiplier to kilograms. Matched case-insensitively as a word.
_WEIGHT_UNITS_KG = {
    "kg": 1.0, "kgs": 1.0, "kgm": 1.0, "kilogram": 1.0, "kilograms": 1.0,
}
_WEIGHT_UNITS_TON = {
    "mt": 1000.0, "m/t": 1000.0, "ton": 1000.0, "tons": 1000.0,
    "tonne": 1000.0, "tonnes": 1000.0, "t": 1000.0,
}


# ---------------------------------------------------------------------------
# Per-field normalizers. Each takes the raw extracted value (str | None) and
# returns the canonical form. None in -> None out (we don't fabricate values,
# Section 3.4 hard constraint).
# ---------------------------------------------------------------------------

def _strip_or_none(s: str | None) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    return s or None


def normalize_weight(raw: str | None) -> float | None:
    """Convert a weight string to kilograms.

    Rules (Section 5.2):
      1. Strip whitespace and thousands separators (`21,577` -> `21577`).
      2. Detect unit. MT/M/T/TON/Tonne → ×1000; KG/KGS/KGM/Kilogram → ×1.
         No unit is also acceptable — assume kg per SI/BL convention.
      3. Return float. Unparseable -> None.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None

    # Pull the unit (trailing word) off the number, case-insensitive.
    m = re.match(
        r"^\s*([0-9]+(?:[.,][0-9]{3})*(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?)"
        r"\s*([a-zA-Z/]+)?\s*$",
        s,
    )
    if not m:
        # Fallback: keep only digits/decimal/sep, try to parse, treat as kg.
        digits = re.sub(r"[^0-9.,]", "", s).replace(",", "")
        try:
            return float(digits) if digits else None
        except ValueError:
            return None

    num_str, unit = m.group(1), (m.group(2) or "").lower()
    # Strip thousands separators (commas only — period is the decimal point).
    # `21,577` → `21577`; `138.000` keeps its period and parses as `138.0`.
    num_clean = _THOUSAND_SEP.sub("", num_str)
    try:
        value = float(num_clean)
    except ValueError:
        return None

    unit_norm = unit.replace(".", "").strip()
    if unit_norm in _WEIGHT_UNITS_TON:
        value *= 1000.0
    elif unit_norm in _WEIGHT_UNITS_KG or not unit_norm:
        pass  # already kg
    else:
        # Unknown unit: don't guess — return None rather than risk a wrong
        # conversion that would silently corrupt Compare.
        return None
    return float(value)


def normalize_count(raw: str | None) -> int | None:
    """Extract container count.

    Examples:
    "3 x 20'GP" -> 3
    "1 x 40'HC" -> 1
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    m = re.search(r"\d[\d,]*", s)
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None

def normalize_company(value):
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    # Ignore harmless punctuation differences in company names
    text = re.sub(r"[.,;:()]", " ", text)

    # Collapse repeated whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def normalize_port(raw: str | None) -> str | None:
    """Port / location: keep the original text (incl. UN/LOCODE) untouched.

    Section 5.2 explicitly says *do not* translate or split. Only trim/collapse
    whitespace so `  PORT KLANG ,  MALAYSIA ` aligns with itself across docs.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s = _MULTI_WS.sub(" ", s).strip()
    return s or None


def normalize_container(raw: Any | None) -> list[str]:
    """Container numbers -> list[str], uppercase, no spaces/hyphens.

    Section 5.2: `MEDU 104332-0` -> `MEDU1043320`. Multiple containers keep
    their source order — order is itself a comparison point, don't sort.
    Always returns a list (length 0 if missing, length 1 for a single box).
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw if x not in (None, "")]
    else:
        items = [str(raw)]

    out: list[str] = []
    for it in items:
        cleaned = it.upper()
        cleaned = cleaned.replace(" ", "").replace("-", "")
        cleaned = _MULTI_WS.sub("", cleaned)
        if cleaned:
            out.append(cleaned)
    return out


# Per-field dispatch table. Fields not listed default to a "clean string" pass.
_FIELD_NORMALIZERS: dict[str, Any] = {
    "shipper": normalize_company,
    "consignee": normalize_company,
    "notify_party": normalize_company,
    "port_of_loading": normalize_port,
    "port_of_discharge": normalize_port,
    "container_count": normalize_count,
    "gross_weight_kg": normalize_weight,
}


def _clean_string(raw: Any | None) -> str | None:
    """Default normalizer: trim and collapse whitespace, keep case."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    return _MULTI_WS.sub(" ", s).strip() or None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize(fields: dict) -> dict:
    """Apply Section 5.2 rules to every field in ``fields``.

    Returns a fresh dict with the same key set (per Section 3.4 hard constraint:
    never drop keys). Input is not mutated. Unknown keys are passed through
    using the default string cleaner so a schema addition doesn't silently
    skip normalization.
    """
    out = empty_fields()
    for name in FIELDS:
        raw = fields.get(name)
        fn = _FIELD_NORMALIZERS.get(name, _clean_string)
        try:
            out[name] = fn(raw)
        except Exception:
            # A normalizer must never raise into the orchestrator. If a value
            # is so malformed we can't process it, mark it missing rather than
            # crash the whole email.
            out[name] = [] if name in LIST_FIELDS else None
    return out


def find_missing(fields: dict) -> list[str]:
    """Return field names whose value is None or an empty list.

    Per Section 3.2: a field that couldn't be extracted is recorded here so
    escalation knows what to ask a human to fill in.
    """
    missing: list[str] = []
    for name in FIELDS:
        value = fields.get(name)
        if value is None:
            missing.append(name)
        elif name in LIST_FIELDS and isinstance(value, list) and len(value) == 0:
            missing.append(name)
    return missing
