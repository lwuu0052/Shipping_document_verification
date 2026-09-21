"""Step 2 — Format-aware attachment readers.

Dispatch by file extension and return plain text suitable for the LLM
extractor in Step 3. Each reader either succeeds or raises
:class:`ParseError` with a one-line human-readable detail.

Supported (Section 4 / Step 2):

  .txt           → read with utf-8 → gbk → latin-1 fallback
  .xlsx / .xls   → openpyxl, flatten every sheet row-by-row preserving order
  .pdf           → pdfplumber; empty text => scanned doc => parse_error
  other          → ParseError(unsupported_format)

Heavy imports (openpyxl, pdfplumber) are done lazily so a missing optional
dependency only blows up when actually exercised — tests that don't touch
.xlsx / .pdf don't need the libs installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .schema import (
    REASON_PARSE_ERROR,
    REASON_SCANNED_PDF,
    REASON_UNSUPPORTED_FORMAT,
)


class ParseError(Exception):
    """Raised when a file cannot be turned into text.

    Carry ``reason`` so the orchestrator can distinguish unsupported_format
    from parse_error without re-parsing the exception message.
    """

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class ScannedPdfError(ParseError):
    """Raised when a PDF has no text layer (scanned image).

    The orchestrator catches this specifically and falls back to the
    multimodal LLM extractor (Section 5.4 / OCR fallback). If that also
    fails, the result is downgraded to a plain ``parse_error``.

    Carries ``path`` so the multimodal extractor can re-open the file and
    render pages to images without re-discovering the path.
    """

    def __init__(self, path, detail: str | None = None):
        super().__init__(
            REASON_SCANNED_PDF,
            detail or f"{Path(path).name} yielded no text (likely a scan)",
        )
        self.path = str(path)


# ---------------------------------------------------------------------------
# Individual readers
# ---------------------------------------------------------------------------

def _read_txt(path: Path) -> str:
    """Read a plain-text attachment.

    Encoding fallback per Section 5.2 / Step 2: utf-8 → gbk → latin-1.
    We accept any bytes that decode under one of these. A truly empty file
    (0 bytes) is treated as a parse error — there is nothing to extract.
    """
    raw = path.read_bytes()
    if not raw:
        raise ParseError(REASON_PARSE_ERROR,
                         f"empty file: {path.name}")
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 always decodes, so this is unreachable in practice. Kept for
    # defensiveness against a future code path that changes the fallback list.
    raise ParseError(REASON_PARSE_ERROR,
                     f"could not decode {path.name} as utf-8/gbk/latin-1")


def _read_xlsx(path: Path) -> str:
    """Flatten an .xlsx/.xls workbook into a single text blob.

    Iterates sheets in workbook order, rows in sheet order, cells in row
    order. Empty cells become empty strings; each row is one line; a blank
    line separates sheets. This is enough for the LLM extractor to read
    SI/BL fields regardless of which sheet they happen to live on.
    """
    try:
        import openpyxl  # lazy import
    except ImportError as e:  # pragma: no cover - env dependent
        raise ParseError(REASON_PARSE_ERROR,
                         "openpyxl is required to read .xlsx attachments") from e

    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        raise ParseError(REASON_PARSE_ERROR,
                         f"failed to open workbook {path.name}: {e}") from e

    chunks: list[str] = []
    total_rows = 0
    try:
        for sheet in wb.worksheets:
            sheet_title = sheet.title or "(unnamed sheet)"
            chunks.append(f"=== SHEET: {sheet_title} ===")
            row_count = 0
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                # Skip fully-empty rows to keep the blob compact; the LLM
                # is fine without them and they bloat token usage.
                if any(cell.strip() for cell in cells):
                    chunks.append("\t".join(cells))
                    row_count += 1
                    total_rows += 1
            if row_count == 0:
                chunks.append("(empty sheet)")
            chunks.append("")  # blank line between sheets
    finally:
        # read_only workbooks hold the file open until close().
        try:
            wb.close()
        except Exception:
            pass

    text = "\n".join(chunks).strip()
    if total_rows == 0:
        # Workbook with no data rows at all — treat as a scanned/empty file.
        raise ParseError(REASON_PARSE_ERROR,
                         f"workbook {path.name} has no readable content")
    return text

def _read_xls(path: Path) -> str:
    """Flatten a legacy .xls workbook into plain text."""
    try:
        import xlrd
    except ImportError as e:
        raise ParseError(
            REASON_PARSE_ERROR,
            "xlrd is required to read .xls attachments",
        ) from e

    try:
        wb = xlrd.open_workbook(path)
    except Exception as e:
        raise ParseError(
            REASON_PARSE_ERROR,
            f"failed to open workbook {path.name}: {e}",
        ) from e

    chunks: list[str] = []
    total_rows = 0

    for sheet in wb.sheets():
        chunks.append(f"=== SHEET: {sheet.name} ===")

        for row_idx in range(sheet.nrows):
            cells = [
                str(sheet.cell_value(row_idx, col_idx)).strip()
                for col_idx in range(sheet.ncols)
            ]

            if any(cells):
                chunks.append("\t".join(cells))
                total_rows += 1

        chunks.append("")

    if total_rows == 0:
        raise ParseError(
            REASON_PARSE_ERROR,
            f"workbook {path.name} has no readable content",
        )

    return "\n".join(chunks).strip()

def _read_docx(path: Path) -> str:
    """Extract text and table contents from a Word .docx file."""
    try:
        from docx import Document
    except ImportError as e:
        raise ParseError(
            REASON_PARSE_ERROR,
            "python-docx is required to read .docx attachments",
        ) from e

    try:
        doc = Document(path)
    except Exception as e:
        raise ParseError(
            REASON_PARSE_ERROR,
            f"failed to open Word document {path.name}: {e}",
        ) from e

    chunks: list[str] = []

    # Normal paragraphs
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text:
            chunks.append(text)

    # Tables
    for table in doc.tables:
        for row in table.rows:
            cells = [
                cell.text.strip().replace("\n", " | ")
                for cell in row.cells
            ]
            if any(cells):
                chunks.append("\t".join(cells))

    text = "\n".join(chunks).strip()

    if not text:
        raise ParseError(
            REASON_PARSE_ERROR,
            f"Word document {path.name} has no readable content",
        )

    return text


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF.

    Prefer pypdf because it preserves form blocks cleanly and can recover
    some PDFs with imperfect xref pointers. Fall back to pdfplumber. Empty
    text means an image-only/scanned PDF and is escalated.
    """
    errors: list[str] = []

    try:
        from pypdf import PdfReader
        try:
            reader = PdfReader(str(path), strict=False)
            text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            if text:
                return text
        except Exception as e:
            errors.append(f"pypdf: {e}")
    except ImportError:
        errors.append("pypdf not installed")

    try:
        import pdfplumber
        try:
            with pdfplumber.open(path) as pdf:
                text = "\n".join((page.extract_text() or "") for page in pdf.pages).strip()
            if text:
                return text
        except Exception as e:
            errors.append(f"pdfplumber: {e}")
    except ImportError:
        errors.append("pdfplumber not installed")

    # If a parser opened the file but there was no text layer, treat it as a
    # scan. Corrupt/truncated PDFs instead become a normal parse_error.
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path), strict=False)
        if reader.pages and not any((page.extract_text() or "").strip() for page in reader.pages):
            raise ScannedPdfError(path)
    except ScannedPdfError:
        raise
    except Exception:
        pass

    detail = "; ".join(errors) if errors else "no PDF parser available"
    raise ParseError(REASON_PARSE_ERROR, f"failed to read pdf {path.name}: {detail}")


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_DISPATCH: dict[str, Callable[[Path], str]] = {
    ".txt": _read_txt,
    ".xlsx": _read_xlsx,
    ".xls": _read_xls,
    ".docx": _read_docx,
    ".pdf": _read_pdf,
}


def parse_to_text(path: str | Path) -> str:
    """Read an attachment and return its text.

    Dispatches on extension. Raises :class:`ParseError` with reason
    ``unsupported_format`` for unknown extensions and ``parse_error`` for
    files that exist but cannot be turned into usable text.
    """
    p = Path(path)
    ext = p.suffix.lower()
    reader = _DISPATCH.get(ext)
    if reader is None:
        raise ParseError(REASON_UNSUPPORTED_FORMAT,
                         f"unsupported extension '{ext}' for {p.name}")
    if not p.exists():
        raise ParseError(REASON_PARSE_ERROR,
                         f"attachment not found: {p}")
    try:
        text = reader(p)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(REASON_PARSE_ERROR, f"failed to read {p.name}: {exc}") from exc
    if not text.strip():
        raise ParseError(REASON_PARSE_ERROR, f"attachment {p.name} has no readable content")
    return text
