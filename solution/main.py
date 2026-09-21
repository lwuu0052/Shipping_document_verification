"""Pipeline entry point with Docker server support.

Three modes:

  Local   (default):  python3 main.py --bundle ../sdoc-hackathon-bundle
  HTTP:               python3 main.py --http             # http://localhost:8080
  HTTP + auto-score:  python3 main.py --http --submit    # run + score via /submit

Other flags:
  --limit N           only process the first N emails (default 0 = all)
  --out PATH          submission.json path (default ./submission.json)
  --bundle PATH       local bundle path (default ../sdoc-hackathon-bundle)
  --server URL        HTTP server URL (default http://localhost:8080)
  --save-review       persist human_review review cases (optional)

The submission.json shape matches sample_submission.json exactly:
    { email_id: {category, status, review_reason, defect_fields, has_defect} }
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from classify import classify_email
from extract.extractor import extract
from comparator import compare_documents

# Make the sibling ``sdoc-hackathon-bundle`` importable so ``from loader
# import Inbox`` works regardless of CWD.
BUNDLE_DIR = Path(__file__).resolve().parent.parent / "sdoc-hackathon-bundle"
sys.path.insert(0, str(BUNDLE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader import Inbox  # noqa: E402

# Optional human_review integration — guarded so a missing module never
# aborts the pipeline. Review-case persistence is opt-in via --save-review.
try:
    from human_review import create_review_case, normalize_review_reason
    from review_store import save_review_case
    _HUMAN_REVIEW_AVAILABLE = True
except ImportError:
    _HUMAN_REVIEW_AVAILABLE = False
    normalize_review_reason = lambda r: (  # noqa: E731
        "missing_attachment" if r == "missing_attachment" else
        "wrong_doc_type" if r == "pairing_failed" else
        "unreadable"
    )

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pipeline")

# Submission statuses (README L9-10).
STATUS_OK = "OK"
STATUS_MISMATCH = "MISMATCH"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"

# README L12-13 review_reason vocabulary.
REVIEW_WRONG_DOC_TYPE = "wrong_doc_type"
REVIEW_MISSING_ATTACHMENT = "missing_attachment"
REVIEW_UNREADABLE = "unreadable"
REVIEW_MISSING_VALUE = "missing_value"

# Extract reason -> submission review_reason mapping.
_EXTRACT_REASON_TO_REVIEW: dict[str, str] = {
    "pairing_failed": REVIEW_WRONG_DOC_TYPE,
    "unsupported_format": REVIEW_UNREADABLE,
    "parse_error": REVIEW_UNREADABLE,
    "extraction_error": REVIEW_UNREADABLE,
    "field_null": REVIEW_MISSING_VALUE,
    "missing_attachment": REVIEW_MISSING_ATTACHMENT,
    "scanned_pdf": REVIEW_UNREADABLE,  # only reached if multimodal also failed
}


class HttpAttachmentAdapter:
    """Adapter that mirrors the local-bundle layout over HTTP.

    extract.extract() expects an ``attachment_root`` directory containing
    ``inbox/`` and ``attachments/``. When we read from the docker server
    there is no such directory — attachments live behind HTTP endpoints.

    This adapter pre-downloads every attachment referenced by an email to
    a temp directory that mirrors the bundle layout, then passes that
    directory as ``attachment_root``. Downloads are cached per-email so
    re-extraction (e.g. during dev) doesn't re-fetch.
    """

    def __init__(self, inbox: Inbox, cache_dir: Path):
        self.inbox = inbox
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def materialize(self, email: dict) -> str:
        """Download this email's attachments and return the local root path.

        Files are written directly under <cache>/<email_id>/ (not in a
        subdirectory) because email['attachments'] are rewritten by
        :meth:`rewrite_attachments` to filename-only — extract() then
        looks them up at Path(root)/<filename>.
        """
        email_id = email.get("email_id") or email.get("id") or "unknown"
        email_cache = self.cache_dir / email_id
        email_cache.mkdir(parents=True, exist_ok=True)

        for att_path in email.get("attachments") or []:
            # Server returns paths like "attachments/email_001_SI.txt".
            # We write to <email_cache>/<filename> so Path(root)/<filename>
            # resolves correctly after rewrite_attachments() strips the
            # "attachments/" prefix.
            filename = Path(att_path).name
            target = email_cache / filename
            if not target.exists():
                data = self.inbox.read_bytes(att_path)
                target.write_bytes(data)
                log.info("downloaded %s (%d bytes)", att_path, len(data))

        return str(email_cache)

    def rewrite_attachments(self, email: dict) -> dict:
        """Return a copy of email with attachment paths rewritten to filename-only.

        extract() reads email['attachments'] for pairing. The pairer uses
        just the filename, so we strip the 'attachments/' prefix — the local
        layout still has them under <cache>/<email_id>/, but pairing cares
        about the name only.
        """
        e = dict(email)
        e["attachments"] = [Path(a).name for a in (email.get("attachments") or [])]
        return e


def _is_bl_request_email(email: dict) -> bool:
    """Check if this is a 'please send the draft BL' request email.

    These emails are classified as BL_COMPARISON but are actually requests
    for someone to send a BL — they have no attachments and no comparison
    is expected. Ground truth treats them as OK, not NEEDS_REVIEW.
    """
    body = (email.get("body") or "").lower()
    # "please assist to send the draft BL" / "please send the draft BL"
    if re.search(r"please\s+(?:assist\s+to\s+)?send\s+the\s+draft\s+bl", body):
        return True
    # "please find attached the list of outstanding BL" — listing request, not comparison
    if "outstanding bl" in body or "list of outstanding" in body:
        return True
    return False


def _submission_for_non_bl(category: str) -> dict:
    return {"category": category, "status": STATUS_OK, "review_reason": None,
            "defect_fields": [], "has_defect": False}


def _submission_for_extract_failure(ext: dict) -> dict:
    reason = ext.get("reason")
    review_reason = _EXTRACT_REASON_TO_REVIEW.get(reason, REVIEW_UNREADABLE)
    # Prefer normalize_review_reason from human_review if available — it's
    # the curated mapping. Fall back to our table.
    if _HUMAN_REVIEW_AVAILABLE and reason:
        review_reason = normalize_review_reason(reason)
    return {"category": "BL_COMPARISON", "status": STATUS_NEEDS_REVIEW,
            "review_reason": review_reason, "defect_fields": [],
            "has_defect": False}


def _submission_for_comparison(category: str, cmp_result: dict) -> dict:
    status = cmp_result.get("status", STATUS_NEEDS_REVIEW)
    out = {
        "category": category,
        "status": status,
        "review_reason": cmp_result.get("review_reason"),
        "defect_fields": cmp_result.get("defect_fields") or [],
        "has_defect": bool(cmp_result.get("has_defect", False)),
    }
    # If compare returned NEEDS_REVIEW with missing_fields but no review_reason,
    # map it to missing_value so it's never null on a NEEDS_REVIEW.
    if status == STATUS_NEEDS_REVIEW and not out["review_reason"]:
        out["review_reason"] = REVIEW_MISSING_VALUE
    # OK/MISMATCH must have null review_reason per the v2 schema.
    if status in (STATUS_OK, STATUS_MISMATCH):
        out["review_reason"] = None
    return out


def _adapt_for_comparator(fields: dict | None) -> dict:
    """Map extract output to comparator input."""
    if fields is None:
        return {}
    f = {k: v for k, v in fields.items() if k != "raw"}
    return f


def process_email(email: dict, bundle_path: str | None = None,
                  adapter: "HttpAttachmentAdapter | None" = None,
                  save_review: bool = False) -> dict:
    """Run one email through the full pipeline. Returns the submission entry.

    If ``adapter`` is provided (HTTP mode), attachments are downloaded to
    a local cache and the email is rewritten with filename-only paths so
    the extractor finds them under <cache>/<email_id>/.

    Never raises: every stage's failure is converted to a NEEDS_REVIEW
    submission entry so the batch always completes.
    """
    email_id = email.get("email_id") or email.get("id") or "<unknown>"

    # ---- Stage 1: Classify ----
    try:
        cls = classify_email(email)
    except Exception as e:
        log.exception("classify failed email_id=%s", email_id)
        cls = {"email_id": email_id, "category": "BL_COMPARISON",
               "should_process": True}

    category = cls.get("category", "GENERAL")

    if category != "BL_COMPARISON" or not cls.get("should_process"):
        log.info("skip email_id=%s category=%s", email_id, category)
        return _submission_for_non_bl(category)

    # ---- Stage 2: Extract ----
    # In HTTP mode, materialize attachments to a local cache directory
    # and rewrite the email so extract() finds the files.
    if adapter is not None:
        local_root = adapter.materialize(email)
        email_for_extract = adapter.rewrite_attachments(email)
    else:
        local_root = bundle_path or str(BUNDLE_DIR)
        email_for_extract = email

    try:
        ext = extract(email_for_extract, cls, attachment_root=local_root)
    except Exception as e:
        log.exception("extract raised email_id=%s", email_id)
        ext = {"email_id": email_id, "parse_status": "failed",
               "reason": "extraction_error", "detail": str(e),
               "si": None, "bl": None}

    if ext is None:
        return _submission_for_non_bl(category)

    parse_status = ext.get("parse_status")
    if parse_status == "failed" or ext.get("si") is None or ext.get("bl") is None:
        # If extract failed due to missing_attachment but this is actually a
        # "please send the draft BL" request email (no attachments expected),
        # treat it as OK — ground truth considers these OK, not NEEDS_REVIEW.
        if ext.get("reason") == "missing_attachment" and _is_bl_request_email(email):
            log.info("request_email_ok email_id=%s (BL request, no attachments expected)",
                     email_id)
            return {"category": category, "status": STATUS_OK,
                    "review_reason": None, "defect_fields": [],
                    "has_defect": False}
        log.info("extract_failed email_id=%s reason=%s",
                 email_id, ext.get("reason"))
        sub = _submission_for_extract_failure(ext)
        # Optionally persist a human-review case for escalation.
        if save_review and _HUMAN_REVIEW_AVAILABLE:
            try:
                review_case = create_review_case(email=email, result=sub,
                                                  extraction=ext)
                save_review_case(review_case)
            except Exception:
                log.exception("review_case persistence failed email_id=%s",
                              email_id)
        return sub

    # ---- Stage 3: Compare ----
    si_cmp = _adapt_for_comparator(ext["si"])
    bl_cmp = _adapt_for_comparator(ext["bl"])
    try:
        cmp_result = compare_documents(si_cmp, bl_cmp)
    except Exception as e:
        log.exception("compare failed email_id=%s", email_id)
        cmp_result = {"status": STATUS_NEEDS_REVIEW,
                      "review_reason": REVIEW_UNREADABLE,
                      "has_defect": False, "defect_fields": []}

    sub = _submission_for_comparison(category, cmp_result)
    log.info("done email_id=%s status=%s has_defect=%s defects=%s review=%s",
             email_id, sub["status"], sub["has_defect"],
             sub["defect_fields"], sub["review_reason"])

    # Optionally persist a review case when compare escalates.
    if save_review and _HUMAN_REVIEW_AVAILABLE \
            and sub["status"] == STATUS_NEEDS_REVIEW:
        try:
            review_case = create_review_case(
                email=email, result=sub,
                extraction=ext, comparison=cmp_result,
            )
            save_review_case(review_case)
        except Exception:
            log.exception("review_case persistence failed email_id=%s",
                          email_id)

    return sub


def _load_emails(source: str, limit: int = 0) -> tuple[list[dict], Inbox]:
    """Load emails from local path or HTTP URL. Returns (emails, inbox)."""
    inbox = Inbox(source)
    emails = list(inbox)
    if limit and limit > 0:
        emails = emails[:limit]
    log.info("loaded %d emails from %s (limit=%s)",
             len(emails), source, limit if limit else "all")
    return emails, inbox


def run(source: str, limit: int = 0, output_path: str = "submission.json",
        submit: bool = False, save_review: bool = False) -> dict:
    """Process all emails and write submission.json. Returns the submission.

    If ``submit`` is True and ``source`` is an HTTP URL, also POST the
    submission to the server's /submit endpoint and print the score.
    """
    emails, inbox = _load_emails(source, limit)

    # In HTTP mode we need an adapter to materialize attachments locally.
    adapter = None
    if inbox.is_http:
        cache_dir = Path(__file__).resolve().parent / ".http_cache"
        adapter = HttpAttachmentAdapter(inbox, cache_dir)
        log.info("HTTP mode — attachments cached to %s", cache_dir)

    submission: dict[str, dict] = {}
    start = time.time()
    for i, email in enumerate(emails, 1):
        eid = email.get("email_id") or email.get("id") or f"email_{i:03d}"
        log.info("[%d/%d] processing %s", i, len(emails), eid)
        try:
            sub_entry = process_email(email, bundle_path=source,
                                      adapter=adapter,
                                      save_review=save_review)
            submission[eid] = sub_entry
        except Exception as e:
            log.exception("unhandled failure email_id=%s", eid)
            submission[eid] = {
                "category": "BL_COMPARISON",
                "status": STATUS_NEEDS_REVIEW,
                "review_reason": REVIEW_UNREADABLE,
                "defect_fields": [],
                "has_defect": False,
            }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(submission, f, indent=2, ensure_ascii=False)
    log.info("wrote submission for %d emails to %s (%.1fs)",
             len(submission), output_path, time.time() - start)

    if submit and source.startswith(("http://", "https://")):
        log.info("submitting to %s/submit ...", source.rstrip("/"))
        try:
            result = inbox.submit(submission)
            print("\n" + "=" * 60)
            print("  SCORE FROM DOCKER SERVER")
            print("=" * 60)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as e:
            log.exception("submit failed")
            print(f"Submit failed: {e}")

    return submission


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--http", action="store_true",
                     help="use HTTP server (default http://localhost:8080)")
    mode.add_argument("--bundle", default="../sdoc-hackathon-bundle",
                     help="local bundle path (default)")
    p.add_argument("--server", default="http://localhost:8080",
                   help="HTTP server URL (only used with --http)")
    p.add_argument("--limit", type=int, default=0,
                   help="only process first N emails (0 = all)")
    p.add_argument("--out", default="submission.json",
                   help="output path for submission.json")
    p.add_argument("--submit", action="store_true",
                   help="POST submission to server /submit and print score "
                        "(only with --http)")
    p.add_argument("--save-review", action="store_true",
                   help="persist human-review cases (requires human_review)")
    args = p.parse_args()

    if args.http:
        source = args.server
    else:
        source = args.bundle

    run(source=source, limit=args.limit, output_path=args.out,
        submit=args.submit, save_review=args.save_review)


if __name__ == "__main__":
    main()
