"""Step 3 — LLM field extraction (one document per call).

Why one document per call (Section 4 / Step 3): a single combined prompt
lets the model "fix" a smudged number on BL by copying it from SI. That
silent copy would erase exactly the kind of mismatch the downstream
Compare step exists to flag, so SI and BL MUST be extracted independently.

LLM access is delegated to the project-level :mod:`llm_factory` — this
module never imports a provider SDK directly. ``get_llm()`` returns a
``genai.Client`` for the Gemini provider (per the factory contract), and
we call its native ``client.models.generate_content(...)`` here. Provider
and API-key handling live in :mod:`llm_factory` / ``.env`` (gitignored).

Backends (``LLM_BACKEND`` env var):

  - ``factory`` (default) → call :func:`llm_factory.get_llm` then
    ``client.models.generate_content``. Provider (Gemini/OpenAI) is chosen
    in the factory via ``LLM_PROVIDER``.
  - ``stub`` → :data:`STUB_RESPONSE` is returned as-is, no network. Used
    by tests and the offline dev order in Section 8.3.

The orchestrator never sees the backend — only the contract
:func:`extract_fields(doc_text, doc_type) -> dict`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Callable

from .prompts import SYSTEM_PROMPT, user_prompt
from .schema import FIELDS, REASON_EXTRACTION_ERROR, empty_fields

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# Model name passed to generate_content(). The factory returns a genai.Client
# without a bound model, so we supply it here. Override via LLM_MODEL.
MODEL_NAME = os.environ.get("LLM_MODEL", "gemini-3.6-flash")
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4096"))
# Base delay between retries. Multiplied by 2^(attempt-1) for transient errors.
RETRY_DELAY_SECONDS = float(os.environ.get("LLM_RETRY_DELAY", "1.5"))
# Hard cap on backoff so a long overload doesn't translate to minutes of waiting.
RETRY_DELAY_MAX_SECONDS = float(os.environ.get("LLM_RETRY_DELAY_MAX", "15.0"))
# Additional retries (beyond the first one) reserved for transient errors
# (429 / 503 / network). Semantic errors (bad JSON) still only get 1 retry.
MAX_TRANSIENT_RETRIES = int(os.environ.get("LLM_MAX_TRANSIENT_RETRIES", "4"))

# Strip ```json ... ``` or ``` ... ``` fences, plus leading "Here is..."
# prefixes that some models emit despite instructions not to.
_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n?(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_PREFIX_RE = re.compile(r"^\s*(?:here(?:'s| is)[^:]*?:\s*)", re.IGNORECASE)


class ExtractionError(Exception):
    """Raised when the LLM call or JSON parse fails after one retry."""

    def __init__(self, reason: str = REASON_EXTRACTION_ERROR, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# JSON post-processing
# ---------------------------------------------------------------------------

def _strip_fence(text: str) -> str:
    """Remove markdown code fences and stray prose prefixes.

    Models are told not to wrap output, but some do anyway. We peel off
    the outer ```json``` fence (and a leading "Here is the JSON:" line)
    so a well-formed JSON body survives. Anything more aggressive is the
    model's fault and bubbles up as :class:`ExtractionError`.
    """
    text = text.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group("body").strip()
    text = _PREFIX_RE.sub("", text).strip()
    return text


# Markdown-ish junk some models emit despite response_mime_type=json.
#   `- "key": value`     →  `"key": value`
#   `* "key": value`     →  `"key": value`
#   `` `key`: value ``   →  `"key": value`
# We strip the leading bullet and unquote backtick-quoted field names.
_BULLET_PREFIX = re.compile(r"^[ \t]*[-*+][ \t]+", re.MULTILINE)
_BACKTICK_KEY = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`(\s*:)")


def _sanitize_json_text(text: str) -> str:
    """Best-effort cleanup before json.loads.

    Applies: fence stripping, bullet removal, backtick-key conversion. We
    never invent or drop field values — only cosmetic transformations that
    recover JSON the model nearly produced. If this still doesn't parse,
    we surface as :class:`ExtractionError` (one retry already happened).
    """
    text = _strip_fence(text)
    text = _BULLET_PREFIX.sub("", text)
    text = _BACKTICK_KEY.sub(r'"\1"\2', text)
    return text


def _coerce_fields(parsed: Any) -> dict:
    """Keep only the required comparison fields."""

    if not isinstance(parsed, dict):
        raise ExtractionError(
            detail=f"expected a JSON object, got {type(parsed).__name__}"
        )

    out = empty_fields()

    for field in FIELDS:
        out[field] = parsed.get(field)

    return out


def parse_llm_response(raw_text: str) -> dict:
    """Public helper (also used by tests) — parse one LLM response to a
    fields dict. Raises :class:`ExtractionError` on malformed JSON.
    """
    body = _sanitize_json_text(raw_text)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as e:
        raise ExtractionError(
            detail=f"model returned invalid JSON: {e.msg}; head={body[:80]!r}") from e
    return _coerce_fields(parsed)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------
def _call_via_factory(doc_text: str, doc_type: str) -> str:
    """Call Gemini through the shared llm_factory."""

    try:
        from llm_factory import get_llm
    except ImportError as e:
        raise ExtractionError(
            detail="llm_factory not importable"
        ) from e

    try:
        client = get_llm(
            model_name=MODEL_NAME,
            temperature=0.0
        )
    except Exception as e:
        raise ExtractionError(
            detail=f"LLM factory init failed: {e}"
        ) from e

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user_prompt(doc_text, doc_type),
            config={
                "system_instruction": SYSTEM_PROMPT,
                "max_output_tokens": MAX_TOKENS,
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )

        raw = response.text

    except Exception as e:
        raise ExtractionError(
            detail=f"LLM call failed: {e}"
        ) from e

    if not raw:
        raise ExtractionError(
            detail="LLM returned empty content"
        )

    return raw


# ---------------------------------------------------------------------------
# Multimodal fallback (scanned PDFs)
# ---------------------------------------------------------------------------
def _pdf_to_image_parts(pdf_path: str):
    """Render each PDF page to a PNG and wrap as a genai Part.

    Uses pypdfium2 (installed alongside pdfplumber). Scale=2 gives
    ~144 DPI on a standard A4 — enough for the LLM to read small print
    without exploding the token budget.
    """
    try:
        import pypdfium2 as pdfium  # lazy import
    except ImportError as e:  # pragma: no cover - env dependent
        raise ExtractionError(
            detail="pypdfium2 is required for scanned-PDF fallback"
        ) from e

    try:
        from google.genai import types
    except ImportError as e:  # pragma: no cover
        raise ExtractionError(
            detail="google-genai is required for multimodal fallback"
        ) from e

    import io
    try:
        pdf = pdfium.PdfDocument(pdf_path)
    except Exception as e:
        raise ExtractionError(
            detail=f"failed to open scanned pdf {pdf_path}: {e}"
        ) from e

    parts = []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=2)
            pil_image = bitmap.to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            parts.append(types.Part.from_bytes(
                data=buf.getvalue(),
                mime_type="image/png",
            ))
    finally:
        # pypdfium2 holds the file open until close.
        try:
            pdf.close()
        except Exception:
            pass

    if not parts:
        raise ExtractionError(
            detail=f"scanned pdf {pdf_path} rendered 0 pages"
        )
    return parts


def _call_via_factory_multimodal(pdf_path: str, doc_type: str) -> str:
    """Call Gemini with rendered page images + the same JSON prompt.

    Used by the orchestrator when parse_to_text() raises ScannedPdfError.
    The model reads the rendered image directly — no separate OCR step.
    """
    try:
        from llm_factory import get_llm
    except ImportError as e:
        raise ExtractionError(
            detail="llm_factory not importable"
        ) from e

    try:
        client = get_llm(
            model_name=MODEL_NAME,
            temperature=0.0
        )
    except Exception as e:
        raise ExtractionError(
            detail=f"LLM factory init failed: {e}"
        ) from e

    image_parts = _pdf_to_image_parts(pdf_path)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            # Image parts first, then the same user prompt as the text path.
            # The system prompt instructs the model to extract the field set
            # as JSON — Gemini's vision model honors response_mime_type=json.
            contents=[*image_parts, user_prompt("", doc_type)],
            config={
                "system_instruction": SYSTEM_PROMPT,
                "max_output_tokens": MAX_TOKENS,
                "response_mime_type": "application/json",
                "temperature": 0.0,
            },
        )
        raw = response.text
    except Exception as e:
        raise ExtractionError(
            detail=f"multimodal LLM call failed: {e}"
        ) from e

    if not raw:
        raise ExtractionError(
            detail="multimodal LLM returned empty content"
        )
    return raw


# A pluggable stub function. Tests override this (or set LLM_BACKEND=stub)
# to drive the extraction layer without network access.
STUB_RESPONSE: Callable[[str, str], str] | None = None


def _call_stub(doc_text: str, doc_type: str) -> str:
    if STUB_RESPONSE is None:
        raise ExtractionError(
            detail="LLM_BACKEND=stub but no STUB_RESPONSE registered")
    return STUB_RESPONSE(doc_text, doc_type)


def _dispatch_call(doc_text: str, doc_type: str) -> str:
    backend = os.environ.get("LLM_BACKEND", "factory").lower()
    if backend == "stub":
        return _call_stub(doc_text, doc_type)
    if backend == "factory":
        return _call_via_factory(doc_text, doc_type)
    raise ExtractionError(detail=f"unknown LLM_BACKEND={backend!r}; "
                                   "use 'factory' (default) or 'stub'")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_fields(doc_text: str, doc_type: str) -> dict:
    """Extract the SI/BL field set from a single document's text.

    ``doc_type`` is 'SI' or 'BL'. Returns an *un-normalized* fields dict —
    normalization is the job of :mod:`extract.normalize`. The values here
    are verbatim strings as the model copied them from the source.

    Failures:
      - The LLM call or JSON parse fails once -> retry.
      - Retry also fails -> :class:`ExtractionError` raised.

    The orchestrator catches :class:`ExtractionError` and converts to a
    ``failed`` result; we don't swallow it here because retry policy lives
    at this layer, not the orchestrator.
    """
    if not doc_text or not doc_text.strip():
        raise ExtractionError(detail=f"empty document text for {doc_type}")

    return _retry_loop(
        lambda: _dispatch_call(doc_text, doc_type),
        doc_type,
        context="text",
    )


def extract_fields_from_pdf(pdf_path: str, doc_type: str) -> dict:
    """Extract the SI/BL field set from a scanned PDF via Gemini vision.

    Called by the orchestrator when :func:`parsers.parse_to_text` raises
    :class:`parsers.ScannedPdfError`. Renders each PDF page to a PNG and
    sends the image(s) + the same JSON prompt to the model.

    Returns the same shape as :func:`extract_fields` — the orchestrator
    does not know whether text or multimodal was used. Raises
    :class:`ExtractionError` on failure; the orchestrator then downgrades
    to ``parse_error`` for the downstream reviewer.
    """
    backend = os.environ.get("LLM_BACKEND", "factory").lower()
    if backend == "stub":
        # Tests can't exercise the real multimodal path; reuse the text
        # stub. The orchestrator only calls this when parse_to_text raised
        # ScannedPdfError, so tests wanting to exercise the fallback
        # should register a STUB_RESPONSE and call extract_fields_from_pdf
        # directly.
        return _retry_loop(lambda: _call_stub("", doc_type), doc_type,
                           context="stub-pdf")
    if backend != "factory":
        raise ExtractionError(
            detail=f"unknown LLM_BACKEND={backend!r}; use 'factory' (default) "
                   "or 'stub'"
        )

    return _retry_loop(
        lambda: _call_via_factory_multimodal(pdf_path, doc_type),
        doc_type,
        context="multimodal",
    )


def _retry_loop(call_fn: Callable[[], str], doc_type: str,
                context: str = "text") -> dict:
    """Shared retry policy for both text and multimodal extraction.

    Same backoff rules as :func:`extract_fields`: transient errors get
    exponential backoff with up to MAX_TRANSIENT_RETRIES retries, semantic
    errors get one quick retry.
    """
    last_error: Exception | None = None
    attempt = 0
    max_attempts = MAX_TRANSIENT_RETRIES + 1
    while attempt < max_attempts:
        attempt += 1
        try:
            raw = call_fn()
            return parse_llm_response(raw)
        except ExtractionError as e:
            last_error = e
            transient = _is_transient_error(e.detail)
            log.warning(
                "llm.extract(%s) attempt=%d doc_type=%s transient=%s failed: %s",
                context, attempt, doc_type, transient, e.detail,
            )
            if attempt >= max_attempts:
                break
            if transient:
                delay = min(
                    RETRY_DELAY_SECONDS * (2 ** (attempt - 1)),
                    RETRY_DELAY_MAX_SECONDS,
                )
                log.info("llm.extract backing off %.1fs", delay)
                time.sleep(delay)
            elif attempt == 1:
                time.sleep(RETRY_DELAY_SECONDS)
            else:
                break

    raise ExtractionError(
        detail=f"extraction failed after {attempt} attempt(s) for {doc_type} "
               f"({context}): {last_error.detail if last_error else 'unknown'}"
    ) from last_error


def _is_transient_error(detail: str | None) -> bool:
    """True if the error looks like a temporary network/service condition
    worth retrying with backoff (429, 503, gateway, overload, timeout).
    """
    if not detail:
        return False
    d = detail.lower()
    return any(s in d for s in (
        "429", "503", "service unavailable", "overload", "high demand",
        "gateway", "timeout", "temporarily", "rate limit",
    ))
