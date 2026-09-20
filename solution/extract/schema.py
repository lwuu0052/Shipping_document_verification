"""Field definitions, reason-code constants, and empty-result builders.

Keeping these in one place means adding a field later only touches this file,
the normalize rules, and the LLM prompt — never the orchestrator.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Field set (Section 5.1)
#
# Order is deliberate: stays stable across SI and BL so Compare can zip them.
# `container_no` is always a list[str]; everything else is str | int | float | None.
# ---------------------------------------------------------------------------
FIELDS: tuple[str, ...] = (
    "booking_no",
    "bl_no",
    "consignee",
    "vessel",
    "voyage",
    "port_of_loading",
    "port_of_discharge",
    "container_no",
    "description_of_goods",
    "package_count",
    "gross_weight_kg",
)

# Fields whose canonical type is a list (kept even when empty -> counts as missing).
LIST_FIELDS: frozenset[str] = frozenset({"container_no"})

# Numeric fields whose canonical type is int / float.
NUMERIC_FIELDS: frozenset[str] = frozenset({"package_count", "gross_weight_kg"})

# ---------------------------------------------------------------------------
# Reason codes (Section 6.1). Compare against these strings, never inline them.
# ---------------------------------------------------------------------------
REASON_MISSING_ATTACHMENT = "missing_attachment"
REASON_PAIRING_FAILED = "pairing_failed"
REASON_UNSUPPORTED_FORMAT = "unsupported_format"
REASON_PARSE_ERROR = "parse_error"
REASON_EXTRACTION_ERROR = "extraction_error"
REASON_FIELD_NULL = "field_null"

ALL_REASONS: tuple[str, ...] = (
    REASON_MISSING_ATTACHMENT,
    REASON_PAIRING_FAILED,
    REASON_UNSUPPORTED_FORMAT,
    REASON_PARSE_ERROR,
    REASON_EXTRACTION_ERROR,
    REASON_FIELD_NULL,
)

# Reason -> resulting parse_status mapping (Section 6.1).
_REASON_STATUS: dict[str, str] = {
    REASON_MISSING_ATTACHMENT: "failed",
    REASON_PAIRING_FAILED: "failed",
    REASON_UNSUPPORTED_FORMAT: "failed",
    REASON_PARSE_ERROR: "failed",
    REASON_EXTRACTION_ERROR: "failed",
    REASON_FIELD_NULL: "partial",
}


def status_for_reason(reason: str | None) -> str:
    """Return the parse_status that corresponds to a reason code.

    `None` reason means everything extracted cleanly -> "ok".
    """
    if reason is None:
        return "ok"
    return _REASON_STATUS[reason]


# ---------------------------------------------------------------------------
# Empty / failure result builders
# ---------------------------------------------------------------------------

def empty_fields() -> dict[str, Any]:
    """Return a field dict with every key present, values set to None / [].

    Per Section 3.4: keys must always be present; missing values are `null`,
    `container_no` is always a list. This keeps downstream key-existence checks
    stable instead of fragile.
    """
    fields: dict[str, Any] = {name: None for name in FIELDS}
    fields["container_no"] = []
    return fields


def empty_result(email: dict, *, parse_status: str, reason: str | None,
                 detail: str | None = None) -> dict:
    """Build a top-level result dict per Section 3.

    `si`, `bl`, `missing_fields` are populated by the orchestrator; this helper
    only seeds the envelope and the always-present email metadata so failure
    cases carry the email_id/subject/from needed by escalation (Section 6.3).
    """
    return {
        "email_id": email.get("email_id"),
        "parse_status": parse_status,
        "reason": reason,
        "detail": detail,
        # Carried from the email even on failure so escalation can reason
        # without re-reading the original message.
        "subject": email.get("subject"),
        "from": email.get("from"),
        "si": None,
        "bl": None,
        "missing_fields": None,
    }
