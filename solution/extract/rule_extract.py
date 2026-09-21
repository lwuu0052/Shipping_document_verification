"""Deterministic extraction for structured SI / BL text.

Used before the LLM. It recognizes the field labels used by the shipping
forms and returns the seven required fields. If the document layout is not
recognized with enough confidence, the caller can fall back to the LLM.
"""
from __future__ import annotations

import re

FIELDS = [
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
]

ALIASES = {
    "shipper": [
        "Shipper (Principal or Seller)", "Shipper/Exporter", "SHIPPER", "Shipper",
    ],
    "consignee": [
        "Consignee (Non-Negotiable)", "To the Order of", "CONSIGNEE", "Consignee",
    ],
    "notify_party": [
        "Notify Party/Intermediate Consignee", "NOTIFY PARTY", "Notify Party", "Notify",
    ],
    "port_of_loading": [
        "Port of Loading (POL)", "PORT OF LOADING", "Port of Loading", "Load Port", "POL",
    ],
    "port_of_discharge": [
        "Port of Discharge (POD)", "PORT OF DISCHARGE", "Port of Discharge", "Discharge Port", "POD",
    ],
    "container_count": [
        "No. of Containers or Packages", "Number of Containers", "No. of Containers", "Total Containers", "Container Count",
    ],
    "gross_weight_kg": [
        "Gross Weight毛重(KGS)", "Gross Wt (kgs)", "Gross Weight (KG)", "GROSS WEIGHT", "Gross Weight", "Gross Wt",
    ],
}
for _f in ALIASES:
    ALIASES[_f] = sorted(ALIASES[_f], key=len, reverse=True)

_BLANK = {"", "???", "_______", "TBA", "TBC", "N/A", "____MT"}
_COMPANY_FIELDS = {"shipper", "consignee", "notify_party"}
_TEXT_FIELDS = _COMPANY_FIELDS | {"port_of_loading", "port_of_discharge"}


def _match_alias(line: str, alias: str) -> tuple[bool, str]:
    """Return (matched, remainder after label)."""
    s = line.strip()
    pat = re.escape(alias).replace(r"\ ", r"\s+")
    m = re.match(
        r"^" + pat + r"(?:\s*\([^)]*[\u4e00-\u9fff][^)]*\))?\s*(?::|\t)?\s*(.*)$",
        s,
        flags=re.IGNORECASE,
    )
    if not m:
        return False, ""
    return True, m.group(1).strip()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    upper = value.upper()
    if upper in _BLANK or "?" in value or re.fullmatch(r"_+(?:MT)?", value, flags=re.I):
        return None
    return value


def _company_only(value: str | None) -> str | None:
    value = _clean_text(value)
    if not value:
        return None
    # XLSX / DOCX readers preserve the first logical line before " | ".
    return value.split(" | ", 1)[0].strip() or None


def _port_only(value: str | None) -> str | None:
    value = _clean_text(value)
    if not value:
        return None
    # TXT renderings append a UN/LOCODE such as (MYPKG); keep meaningful
    # names such as PORT KLANG (WESTPORT), MALAYSIA.
    value = re.sub(r"\s+\([A-Z]{5}\)\s*$", "", value).strip()
    return value or None


def _number(value: str | None) -> int | None:
    value = _clean_text(value)
    if not value:
        return None
    m = re.search(r"\d[\d,]*", value)
    return int(m.group(0).replace(",", "")) if m else None


def _weight(value: str | None) -> float | None:
    value = _clean_text(value)
    if not value:
        return None
    m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Za-z/]+)?", value)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "KG").upper().replace(".", "")
    if unit in {"MT", "M/T", "TON", "TONS", "TONNE", "TONNES", "T"}:
        n *= 1000.0
    return n


def extract_fields_rule_based(doc_text: str) -> dict | None:
    """Extract the seven required fields without an LLM.

    Returns None only when the layout is not recognized confidently enough,
    so callers may fall back to the LLM. Explicit blank values remain None.
    """
    lines = [ln.strip() for ln in (doc_text or "").splitlines() if ln.strip()]
    if not lines:
        return None

    out = {f: None for f in FIELDS}
    recognized = set()

    for field in (
        "shipper", "consignee", "notify_party",
        "port_of_loading", "port_of_discharge", "container_count",
    ):
        found = False
        for i, line in enumerate(lines):
            for alias in ALIASES[field]:
                matched, remainder = _match_alias(line, alias)
                if not matched:
                    continue
                recognized.add(field)

                value = remainder
                # pypdf commonly emits a label on one line and its value on
                # the next. An explicit ':' or tab with no value means the
                # source field is truly blank and must stay None.
                if not value and field != "container_count" and i + 1 < len(lines):
                    if not line.rstrip().endswith(":") and "\t" not in line:
                        value = lines[i + 1].strip()

                if field in _COMPANY_FIELDS:
                    out[field] = _company_only(value)
                elif field in {"port_of_loading", "port_of_discharge"}:
                    out[field] = _port_only(value)
                else:
                    out[field] = _number(value)
                found = True
                break
            if found:
                break

    # XLSX/DOCX/TXT weight rows normally contain label+value on one line.
    for line in lines:
        for alias in ALIASES["gross_weight_kg"]:
            matched, remainder = _match_alias(line, alias)
            if matched and remainder:
                recognized.add("gross_weight_kg")
                out["gross_weight_kg"] = _weight(remainder)
                break
        if "gross_weight_kg" in recognized:
            break

    # PDF total line is safer than the table heading, and some PDF text
    # extractors mangle the bilingual glyphs inside the weight label.
    if out["gross_weight_kg"] is None:
        for line in lines:
            if line.upper().startswith("TOTAL"):
                m = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*KG\b", line, flags=re.I)
                if m:
                    recognized.add("gross_weight_kg")
                    out["gross_weight_kg"] = float(m.group(1).replace(",", ""))
                    break

    # If most of the required labels are visible, trust the deterministic
    # result (including intentional blanks). Otherwise let the LLM handle an
    # unfamiliar real-world layout.
    if len(recognized) < 5:
        return None
    return out
