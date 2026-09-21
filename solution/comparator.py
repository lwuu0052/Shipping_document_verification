"""
Receives extracted SI and BL data, normalizes the values, compares
the 7 required shipment fields, and return either OK , MISMATCH , 
or NEEDS_REVIEW. Moreover it also lists mismatched and missing field.
"""


import math
import re


# The 7 fields required by the hackathon
FIELDS = [
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
]


# Company suffix tokens to ignore when comparing party names. These often
# appear in different orders / languages between SI and BL (e.g. "FZE" vs
# "F.Z.E", "PTE LTD" vs "PRIVATE LIMITED") and are not meaningful defects.
_COMPANY_SUFFIXES = {
    "ltd", "co", "inc", "llc", "llp", "plc", "gmbh", "ag", "bv", "nv",
    "pte", "private", "limited", "corp", "corporation", "group",
    "fze", "fzc", "fz", "fzco", "fzn", "dmcc",
    "sdn", "bhd", "srl", "spa", "sa", "oao", "ooo", "jsc",
    "the", "of", "and", "&",
}

# Common punctuation that should not affect name matching
_PUNCT_RE = re.compile(r"[.,;:()\[\]{}'\"`!?\\/_-]+")

# Fields where token-order-insensitive comparison is appropriate
_PARTY_FIELDS = {"shipper", "consignee", "notify_party"}


def _tokenize_for_compare(value: str) -> list[str]:
    """Tokenize a text value for comparison: casefold, strip punctuation,
    drop company suffix tokens."""
    text = " ".join(value.casefold().split())
    text = _PUNCT_RE.sub(" ", text)
    text = " ".join(text.split())
    return [t for t in text.split() if t not in _COMPANY_SUFFIXES]


def normalize_text(value):
    """
    Clean text so capitalization and extra spaces do not cause false mismatches.
    """
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    return " ".join(value.casefold().split())


def _text_equivalent(si_value, bl_value) -> bool:
    """Return True if si_value and bl_value should be considered the same
    party/address despite formatting differences.

    Two values are equivalent if their token sets (after casefolding,
    punctuation stripping, and dropping company suffix tokens) overlap by
    >= 85% (Jaccard similarity). This handles cases where the LLM extracts
    slightly different formats of the same party name
    (e.g. "APRIL FINE PAPER TRADING (MIDDLE EAST) FZE" vs
    "April Fine Paper Trading Middle East FZE") without flagging them as
    defects.
    """
    if si_value is None or bl_value is None:
        # Let the caller handle None
        return normalize_text(si_value) == normalize_text(bl_value)

    a = set(_tokenize_for_compare(str(si_value)))
    b = set(_tokenize_for_compare(str(bl_value)))

    if not a and not b:
        return True
    if not a or not b:
        return False

    # Exact set match
    if a == b:
        return True

    # High overlap — likely the same name with minor token differences
    intersection = len(a & b)
    union = len(a | b)
    jaccard = intersection / union if union else 0.0
    return jaccard >= 0.85


def normalize_number(value):
    """
    Convert numeric values like '22,000' and 22000 into the same format
    """

    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None

    value = str(value).strip()

    if value == "":
        return None

    # Remove commas
    value = value.replace(",", "")

    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def normalize_value(field, value):
    """
    Use text or numeric normalization depending on the field.
    """

    if field in ["container_count", "gross_weight_kg"]:
        return normalize_number(value)

    return normalize_text(value)


def compare_documents(si, bl):
    """
    Compare the 7 required SI and BL fields.

    Returns:
    - OK: all fields match
    - MISMATCH: one or more fields differ
    - NEEDS_REVIEW: a required value is missing/unusable
    """

    defect_fields = []
    missing_fields = []
    differences = {}

    for field in FIELDS:

        # Get original values
        si_original = si.get(field)
        bl_original = bl.get(field)

        # Clean values before comparing
        si_value = normalize_value(field, si_original)
        bl_value = normalize_value(field, bl_original)

        # If we cannot get a value from either document,
        # we cannot safely make a comparison.
        if si_value is None or bl_value is None:
            missing_fields.append(field)
            continue

        # Compare
        # For party fields (shipper/consignee/notify_party), use the
        # token-set similarity comparison so formatting differences
        # (case, punctuation, company suffix variations, token order)
        # don't cause false-positive defects.
        if field in _PARTY_FIELDS:
            is_match = _text_equivalent(si_original, bl_original)
        else:
            is_match = (si_value == bl_value)

        if not is_match:

            defect_fields.append(field)

            # Keep values so they can be displayed later
            differences[field] = {
                "si": si_original,
                "bl": bl_original,
            }

    # Missing information -> human should review
    if missing_fields:
        return {
            "status": "NEEDS_REVIEW",
            "review_reason": "missing_value",
            "has_defect": bool(defect_fields),
            "defect_fields": defect_fields,
            "missing_fields": missing_fields,
            "differences": differences,
        }

    # At least one real mismatch
    if defect_fields:
        return {
            "status": "MISMATCH",
            "review_reason": None,
            "has_defect": True,
            "defect_fields": defect_fields,
            "missing_fields": [],
            "differences": differences,
        }

    # Everything matches
    return {
        "status": "OK",
        "review_reason": None,
        "has_defect": False,
        "defect_fields": [],
        "missing_fields": [],
        "differences": {},
    }