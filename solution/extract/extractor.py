"""Main orchestrator — :func:`extract`.

Wires the four steps defined in Section 4 of the development doc:

    should_process check
      ↓
    Step 1  pair attachments         → missing_attachment / pairing_failed
      ↓
    Step 2  parse to text            → unsupported_format / parse_error
      ↓
    Step 3  LLM extract (per file)   → extraction_error
      ↓
    Step 4  normalize + collect missing → ok / partial

Design rules enforced here (not in the inner modules):

  - **No exception escapes** :func:`extract`. A failed email must not crash
    a batch. Every Step's exception is caught and converted to a result dict.
  - **Step-3 isolation**: SI and BL are extracted in two separate LLM calls.
    The model never sees both at once.
  - **Failure still carries email metadata** (Section 6.3): `email_id`,
    `subject`, `from` are always present so escalation can reason without
    going back to the inbox.
  - **`raw` retained** (Section 5.3): the pre-normalization string values are
    kept under ``si.raw`` / ``bl.raw`` for human review; Compare ignores them.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from .llm_client import ExtractionError, extract_fields, extract_fields_from_pdf
from .normalize import find_missing, normalize
from .pairing import pair_attachments
from .parsers import ParseError, ScannedPdfError, parse_to_text
from .schema import (
    REASON_EXTRACTION_ERROR,
    REASON_FIELD_NULL,
    REASON_MISSING_ATTACHMENT,
    REASON_PAIRING_FAILED,
    REASON_PARSE_ERROR,
    REASON_UNSUPPORTED_FORMAT,
    empty_result,
    status_for_reason,
)

log = logging.getLogger(__name__)

# Default attachment root: the participant bundle that ships with this repo.
# Override per call via the ``attachment_root`` argument — never hardcode a
# path inside a step module.
DEFAULT_ATTACHMENT_ROOT = os.environ.get(
    "ATTACHMENT_ROOT",
    str(Path(__file__).resolve().parents[1].parent / "sdoc-hackathon-bundle"),
)


def _failed(email: dict, reason: str, detail: str | None = None) -> dict:
    """Build a `failed` result envelope with email metadata attached."""
    return empty_result(email, parse_status="failed", reason=reason, detail=detail)


def _extract_one(doc_text: str, doc_type: str, email_id: str,
                 pdf_path: str | None = None) -> tuple[dict, dict]:
    """Run Step 3 + Step 4 for one document.

    Returns ``(normalized_fields, raw_fields)``. On extraction failure raises
    :class:`ExtractionError` so the caller can produce a single `failed`
    result covering both SI and BL — partial half-extraction is misleading
    because Compare expects two sibling documents.

    If ``pdf_path`` is provided (scanned PDF fallback), use the multimodal
    extractor instead of the text one. The rest of the contract is identical.
    """
    if pdf_path is not None:
        raw_fields = extract_fields_from_pdf(pdf_path, doc_type)
    else:
        raw_fields = extract_fields(doc_text, doc_type)  # raises ExtractionError
    normalized = normalize(raw_fields)
    return normalized, raw_fields


def _conflict_detail(email: dict) -> str | None:
    """Detect a subject/body SI-number conflict to flag for escalation.

    Per Section 6.3, email_003 is the canonical example: its subject quotes
    ``SIN525534192`` but the body asks about ``SIN832764835`` — two different
    SI numbers. A human must intervene to disambiguate. We don't act on the
    conflict, only surface it in ``detail`` so escalation can show it.
    """
    import re

    subject = email.get("subject") or ""
    body = email.get("body") or ""
    si_pat = re.compile(r"\bSIN\d{6,}\b", re.IGNORECASE)
    subj_matches = set(m.group(0).upper() for m in si_pat.finditer(subject))
    body_matches = set(m.group(0).upper() for m in si_pat.finditer(body))
    # Numbers present in only one of subject/body -> potential conflict.
    only_subj = subj_matches - body_matches
    only_body = body_matches - subj_matches
    if only_subj and only_body:
        return (
            f"subject references SI {sorted(only_subj)} while body references "
            f"SI {sorted(only_body)} — clarification needed"
        )
    return None


def extract(email: dict, classification: dict,
            attachment_root: str = DEFAULT_ATTACHMENT_ROOT) -> dict | None:
    """Extract SI/BL structured fields for one email.

    Returns:
        - ``None`` if ``classification['should_process']`` is False
          (Section 2.4 — caller skips this email entirely).
        - A result dict per Section 3 otherwise. ``parse_status`` is one of
          ``ok``, ``partial``, ``failed``.

    The function never raises. Every internal failure is caught and turned
    into a ``failed`` (or ``partial``) result with a populated ``reason``
    and ``detail``. Section 7.3: "Don't let exceptions escape extract()."
    """
    start = time.time()
    email_id = email.get("email_id", "<unknown>")

    # -----------------------------------------------------------------
    # 2.4 — Entry gate. should_process False => immediate None.
    # -----------------------------------------------------------------
    if not classification.get("should_process", False):
        log.info("extract.skip email_id=%s reason=should_process_false", email_id)
        return None

    # -----------------------------------------------------------------
    # Step 1 — Attachment pairing.
    # -----------------------------------------------------------------
    attachments = email.get("attachments") or []
    si_path, bl_path, pair_err = pair_attachments(attachments, attachment_root)

    if pair_err == REASON_MISSING_ATTACHMENT:
        # Empty attachment list. Section 6.3: still surface any conflict
        # visible in the email body so escalation has something to act on.
        detail = "Classified as comparison request but attachments list is empty"
        conflict = _conflict_detail(email)
        if conflict:
            detail = f"{detail}; {conflict}"
        result = _failed(email, REASON_MISSING_ATTACHMENT, detail)
        _log_done(email_id, result, start)
        return result

    if pair_err is not None or si_path is None or bl_path is None:
        # Some attachments present but we can't pair them into 1 SI + 1 BL.
        result = _failed(
            email, REASON_PAIRING_FAILED,
            detail=f"could not identify one SI + one BL from "
                   f"{len(attachments)} attachment(s): {attachments}",
        )
        _log_done(email_id, result, start)
        return result

    # -----------------------------------------------------------------
    # Step 2 — Parse both to text. A failure on either side is terminal:
    # Compare needs both documents; one unreadable doc = needs_review.
    #
    # Exception: ScannedPdfError (PDF has no text layer) is not terminal —
    # we fall back to the multimodal LLM extractor in Step 3, which reads
    # the rendered page image directly. The fallback path is tracked
    # separately so we can downgrade cleanly if Gemini can't read it either.
    # -----------------------------------------------------------------
    si_pdf_path: str | None = None  # set if SI is a scanned PDF
    try:
        si_text = parse_to_text(Path(attachment_root) / si_path)
    except ScannedPdfError as e:
        # Defer to Step 3 multimodal path. Keep the absolute path so the
        # extractor can re-open the file and render pages.
        si_pdf_path = e.path
        si_text = ""  # placeholder; the multimodal path ignores it
        log.info("extract email_id=%s SI is scanned, using multimodal fallback",
                 email_id)
    except ParseError as e:
        result = _failed(email, e.reason,
                         detail=f"SI parse failed: {e.detail} (file={si_path})")
        _log_done(email_id, result, start)
        return result

    bl_pdf_path: str | None = None  # set if BL is a scanned PDF
    try:
        bl_text = parse_to_text(Path(attachment_root) / bl_path)
    except ScannedPdfError as e:
        bl_pdf_path = e.path
        bl_text = ""
        log.info("extract email_id=%s BL is scanned, using multimodal fallback",
                 email_id)
    except ParseError as e:
        result = _failed(email, e.reason,
                         detail=f"BL parse failed: {e.detail} (file={bl_path})")
        _log_done(email_id, result, start)
        return result

    # -----------------------------------------------------------------
    # Step 3 — LLM extract, two separate calls. SI first, BL second.
    # A failure on either is terminal for the same reason as Step 2.
    # If a document was scanned, route to the multimodal extractor.
    # -----------------------------------------------------------------
    try:
        si_norm, si_raw = _extract_one(si_text, "SI", email_id,
                                      pdf_path=si_pdf_path)
    except ExtractionError as e:
        # Multimodal fallback failure maps to parse_error, not extraction_error,
        # because the root cause is the source document being a scan — Gemini
        # not reading it is a parse failure, not an LLM call failure.
        reason = REASON_PARSE_ERROR if si_pdf_path else REASON_EXTRACTION_ERROR
        detail = (f"SI {'multimodal ' if si_pdf_path else ''}extraction "
                  f"failed: {e.detail}")
        result = _failed(email, reason, detail=detail)
        _log_done(email_id, result, start)
        return result

    try:
        bl_norm, bl_raw = _extract_one(bl_text, "BL", email_id,
                                       pdf_path=bl_pdf_path)
    except ExtractionError as e:
        reason = REASON_PARSE_ERROR if bl_pdf_path else REASON_EXTRACTION_ERROR
        detail = (f"BL {'multimodal ' if bl_pdf_path else ''}extraction "
                  f"failed: {e.detail}")
        result = _failed(email, reason, detail=detail)
        _log_done(email_id, result, start)
        return result

    # -----------------------------------------------------------------
    # Step 4 — Already normalized inside _extract_one; now collect missing.
    # -----------------------------------------------------------------
    si_missing = find_missing(si_norm)
    bl_missing = find_missing(bl_norm)
    has_missing = bool(si_missing) or bool(bl_missing)

    reason = REASON_FIELD_NULL if has_missing else None
    parse_status = status_for_reason(reason)

    result = empty_result(email, parse_status=parse_status, reason=reason,
                          detail=("missing fields: SI=%s BL=%s" %
                                  (si_missing, bl_missing)) if has_missing else None)
    result["si"] = {**si_norm, "raw": si_raw}
    result["bl"] = {**bl_norm, "raw": bl_raw}
    result["missing_fields"] = {"si": si_missing, "bl": bl_missing}

    _log_done(email_id, result, start)
    return result


def _log_done(email_id: str, result: dict, start: float) -> None:
    """Section 7.5: every email logs at least one line with id, status, time."""
    log.info(
        "extract.done email_id=%s status=%s reason=%s elapsed=%.2fs",
        email_id, result.get("parse_status"), result.get("reason"),
        time.time() - start,
    )
