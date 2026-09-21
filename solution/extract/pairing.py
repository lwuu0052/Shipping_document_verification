"""Step 1 — Attachment pairing.

Given the list of attachment paths from an email, decide which one is the
Shipping Instruction (SI) and which is the Bill of Lading (BL).

Strategy (Section 4 / Step 1):

  1. Filename match (case-insensitive): ``_SI`` / ``SI.`` → SI;
     ``_BL`` / ``BL.`` / ``DRAFT`` → BL.
  2. If filename gives no signal, open the file and sniff the content:
     ``SHIPPING INSTRUCTION`` / ``SI NO`` → SI;
     ``B/L NO`` / ``BILL OF LADING`` / ``SHIPPED ON BOARD`` → BL.
  3. If still ambiguous or count is wrong, raise :class:`PairingError`
     with the appropriate reason code.

Do not rely on filename alone — real emails arrive with names like
``scan_0012.pdf`` and ``doc1.xlsx``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .schema import REASON_MISSING_ATTACHMENT, REASON_PAIRING_FAILED


class PairingError(Exception):
    """Raised when attachments cannot be reduced to one SI + one BL.

    Carry ``reason`` so the orchestrator can map it to the right failure code.
    """

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


# Filename keyword rules. Match against the basename, case-insensitive.
# `_BL` and `BL.` are BL markers; `DRAFT` is also BL because the SI/BL sample
# file is literally named `email_001_BL.txt` and titled "BILL OF LADING (DRAFT)".
_SI_NAME_TOKENS = (re.compile(r"_si\b", re.I), re.compile(r"\bsi\.", re.I),
                   re.compile(r"\bshipping_?instruction\b", re.I))
_BL_NAME_TOKENS = (re.compile(r"_bl\b", re.I), re.compile(r"\bbl\.", re.I),
                   re.compile(r"\bdraft\b", re.I), re.compile(r"\bbol\b", re.I),
                   re.compile(r"\bb_?l\b", re.I))

# Content sniff tokens — only used when the filename is silent.
_SI_CONTENT_TOKENS = (re.compile(r"shipping\s+instruction", re.I),
                      re.compile(r"\bsi\s*no\b", re.I),
                      re.compile(r"\bs/?i\s*no\.?\b", re.I))
_BL_CONTENT_TOKENS = (re.compile(r"bill\s+of\s+lading", re.I),
                      re.compile(r"shipped\s+on\s+board", re.I),
                      re.compile(r"b/?l\s*no", re.I))


def _classify_by_name(filename: str) -> str | None:
    """Return 'SI', 'BL', or None based on filename tokens."""
    base = Path(filename).name.lower()
    if any(p.search(base) for p in _SI_NAME_TOKENS):
        return "SI"
    if any(p.search(base) for p in _BL_NAME_TOKENS):
        return "BL"
    return None


def _classify_by_content(text: str) -> str | None:
    """Return 'SI', 'BL', or None by sniffing the document body.

    Examines only the first ~4 KB — enough for headers, cheap to read.
    """
    sample = text[:4096]
    if any(p.search(sample) for p in _SI_CONTENT_TOKENS):
        return "SI"
    if any(p.search(sample) for p in _BL_CONTENT_TOKENS):
        return "BL"
    return None


def _read_text_safe(path: Path) -> str:
    """Best-effort text read for content sniffing only.

    The real parsing happens in :mod:`extract.parsers`; here we only need a
    few KB to look at the header. Encoding fallback mirrors parsers.py:
    utf-8 → gbk → latin-1.
    """
    raw = path.read_bytes()[:8192]
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


def pair_attachments(paths: list[str], root: str) -> tuple[str | None, str | None, str | None]:
    """Pair attachment paths into (si_path, bl_path, error_reason).

    Success → ``(si_path, bl_path, None)``.
    Failure → ``(None, None, reason)`` where ``reason`` is one of
    :data:`REASON_MISSING_ATTACHMENT` or :data:`REASON_PAIRING_FAILED`.

    Note: this function does **not** raise. Callers that prefer exceptions
    can wrap the result. We keep it exception-free so the orchestrator's
    ``try/except`` only covers I/O and LLM failures, not control flow.
    """
    # Step 1.0 — empty list is the special "no attachments at all" case.
    if not paths:
        return None, None, REASON_MISSING_ATTACHMENT

    classifications: list[tuple[str, str | None]] = []
    for rel_path in paths:
        kind = _classify_by_name(rel_path)
        if kind is None:
            # Fall back to content sniffing. Read from disk under `root`.
            abs_path = Path(root) / rel_path
            try:
                text = _read_text_safe(abs_path)
                kind = _classify_by_content(text)
            except OSError:
                # Can't read for sniffing — leave None; if every file is
                # unreadable here, parsers.py will report parse_error later.
                kind = None
        classifications.append((rel_path, kind))

    si_paths = [p for p, k in classifications if k == "SI"]
    bl_paths = [p for p, k in classifications if k == "BL"]
    unknown = [p for p, k in classifications if k is None]

    # Exactly one SI and one BL — done.
    if len(si_paths) == 1 and len(bl_paths) == 1:
        return si_paths[0], bl_paths[0], None

    # If we have one side (SI or BL) but the other is missing, surface as
    # missing_attachment — ground truth labels these "missing_attachment"
    # rather than "unreadable"/"pairing_failed".
    if len(si_paths) >= 1 and len(bl_paths) == 0:
        return None, None, REASON_MISSING_ATTACHMENT
    if len(bl_paths) >= 1 and len(si_paths) == 0:
        return None, None, REASON_MISSING_ATTACHMENT

    # Anything else: counts don't add up. Reason depends on shape:
    #   - both sides empty / same-type duplicates → pairing_failed
    #   - more than 2 unknown → pairing_failed
    return None, None, REASON_PAIRING_FAILED
