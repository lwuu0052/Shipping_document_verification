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
MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "2048"))
RETRY_DELAY_SECONDS = float(os.environ.get("LLM_RETRY_DELAY", "1.5"))

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
    """Validate the parsed object and normalize trivial shape issues.

    - Must be a JSON object (dict). Anything else -> error.
    - Filter to known fields; ignore extras the model invented.
    - Force ``container_no`` to a list. Anything that isn't a list gets
      wrapped (scalar -> [scalar]) or replaced with [] (None / missing).
    """
    if not isinstance(parsed, dict):
        raise ExtractionError(
            detail=f"expected a JSON object, got {type(parsed).__name__}")

    out = empty_fields()
    out.update({k: parsed.get(k) for k in FIELDS if k in parsed})

    # Container must be a list[str]. Coerce gently.
    cn = out.get("container_no")
    if cn is None:
        out["container_no"] = []
    elif isinstance(cn, str):
        out["container_no"] = [cn]
    elif isinstance(cn, list):
        out["container_no"] = [str(x) for x in cn if x not in (None, "")]
    else:
        out["container_no"] = []
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
    """Invoke the project LLM via :func:`llm_factory.get_llm`.

    Per the agreed contract, ``get_llm()`` returns a ``genai.Client`` for the
    Gemini provider. We call its native ``client.models.generate_content``
    with the schema-locked prompt and force a JSON-object response
    (``response_mime_type``) so the model can't wrap output in markdown
    fences. The fence-stripping in :func:`parse_llm_response` stays as a
    defensive fallback.

    This module deliberately does NOT touch a provider SDK directly — adding
    a provider or changing the key only touches :mod:`llm_factory` / ``.env``.
    """
    try:
        from llm_factory import get_llm
    except ImportError as e:
        raise ExtractionError(
            detail="llm_factory not importable; check PYTHONPATH / cwd") from e

    try:
        client = get_llm()
    except ValueError as e:
        raise ExtractionError(detail=f"LLM factory init failed: {e}") from e
    except Exception as e:
        raise ExtractionError(
            detail=f"LLM factory init failed (unexpected): {e}") from e

    log.info("llm.call doc_type=%s provider=%s model=%s",
             doc_type, os.environ.get("LLM_PROVIDER", "?"), MODEL_NAME)

    try:
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=user_prompt(doc_text, doc_type),
            config={
                "system_instruction": SYSTEM_PROMPT,
                "max_output_tokens": MAX_TOKENS,
                "response_mime_type": "application/json",
                "temperature": 0.0,  # extraction must be deterministic
            },
        )
    except Exception as e:
        raise ExtractionError(detail=f"LLM call failed: {e}") from e

    # `resp.text` raises on multi-part / empty content; walk the
    # candidates/parts manually as a fallback.
    try:
        raw = resp.text
    except Exception:
        parts: list[str] = []
        for cand in getattr(resp, "candidates", []) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    parts.append(text)
        raw = "".join(parts).strip()

    if not raw:
        raise ExtractionError(detail="LLM returned empty content")

    # Log token usage (Section 7.5 — recommended for cost estimation).
    usage = getattr(resp, "usage_metadata", None)
    if usage is not None:
        log.info(
            "llm.tokens doc_type=%s model=%s in=%s out=%s",
            doc_type, MODEL_NAME,
            getattr(usage, "prompt_token_count", "?"),
            getattr(usage, "candidates_token_count", "?"),
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

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            raw = _dispatch_call(doc_text, doc_type)
            return parse_llm_response(raw)
        except ExtractionError as e:
            last_error = e
            log.warning(
                "llm.extract attempt=%d doc_type=%s failed: %s",
                attempt, doc_type, e.detail,
            )
            if attempt == 1:
                time.sleep(RETRY_DELAY_SECONDS)

    raise ExtractionError(
        detail=f"extraction failed after retry for {doc_type}: "
               f"{last_error.detail if last_error else 'unknown'}"
    ) from last_error
