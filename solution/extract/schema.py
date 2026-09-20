"""Field definitions, reason-code constants, and empty-result builders."""

from typing import Any
from constants import COMPARISON_FIELDS

FIELDS: tuple[str, ...] = tuple(COMPARISON_FIELDS)

LIST_FIELDS: frozenset[str] = frozenset()

NUMERIC_FIELDS: frozenset[str] = frozenset({
    "container_count",
    "gross_weight_kg"
})

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

_REASON_STATUS: dict[str, str] = {
    REASON_MISSING_ATTACHMENT: "failed",
    REASON_PAIRING_FAILED: "failed",
    REASON_UNSUPPORTED_FORMAT: "failed",
    REASON_PARSE_ERROR: "failed",
    REASON_EXTRACTION_ERROR: "failed",
    REASON_FIELD_NULL: "partial",
}


def status_for_reason(reason: str | None) -> str:
    if reason is None:
        return "ok"
    return _REASON_STATUS[reason]


def empty_fields() -> dict[str, Any]:
    return {name: None for name in FIELDS}


def empty_result(
    email: dict,
    *,
    parse_status: str,
    reason: str | None,
    detail: str | None = None,
) -> dict:
    return {
        "email_id": email.get("email_id"),
        "parse_status": parse_status,
        "reason": reason,
        "detail": detail,
        "subject": email.get("subject"),
        "from": email.get("from"),
        "si": None,
        "bl": None,
        "missing_fields": None,
    }