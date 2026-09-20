"""SI/BL Extract module.

Second stage of the email-processing agent (Classify → Extract → Compare → Ask).
For emails classified as document-comparison requests, pairs the SI and BL
attachments, parses each to text, extracts structured shipment fields via an
LLM, normalizes them, and emits two isomorphic JSON documents for Compare.

Public entry point: :func:`extract.extractor.extract`.
"""

from .extractor import extract
from .schema import (
    FIELDS,
    REASON_MISSING_ATTACHMENT,
    REASON_PAIRING_FAILED,
    REASON_UNSUPPORTED_FORMAT,
    REASON_PARSE_ERROR,
    REASON_EXTRACTION_ERROR,
    REASON_FIELD_NULL,
    empty_fields,
    empty_result,
)

__all__ = [
    "extract",
    "FIELDS",
    "REASON_MISSING_ATTACHMENT",
    "REASON_PAIRING_FAILED",
    "REASON_UNSUPPORTED_FORMAT",
    "REASON_PARSE_ERROR",
    "REASON_EXTRACTION_ERROR",
    "REASON_FIELD_NULL",
    "empty_fields",
    "empty_result",
]
