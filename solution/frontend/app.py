"""Minimal backend for the inbox_frontend dashboard.

Wires the pipeline (``main.classify_email`` / ``main.extract`` /
``main.compare_documents``, plus main.py's own submission-shaping helpers)
to the HTTP API the dashboard's inbox.js expects. Intentionally a slim
subset of the original HarborCheck project inbox_frontend was borrowed
from: no admin-token auth, no SQLite persistence, no offline/cloud mode
switch, no human-review/audit trail.

Two data sources feed ``self.reports``:
  - ``solution/submission.json`` (main.py's own batch output, read-only —
    never written back to) seeds every email with its category/status/
    defect verdict at startup, so a Cloud Run cold start doesn't hand a
    judge an empty inbox.
  - Live "Verify" clicks call the pipeline directly for just that email
    and get the richer per-field SI/BL table on top of the same verdict;
    that richer result is cached to ``.dashboard_cache.json`` (gitignored)
    so a warm instance survives a quick restart.
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs

SOLUTION_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = SOLUTION_ROOT.parent / "sdoc-hackathon-bundle"
# main.py's own batch output — {email_id: {category, status, review_reason,
# has_defect, defect_fields}}. Read-only baseline; regenerate with
# `python main.py` (see main.py's own --help for --workers etc).
SUBMISSION_PATH = SOLUTION_ROOT / "submission.json"
# Our own cache of live-verified, full-detail reports. Not the shared
# submission.json — never written there, to avoid stepping on a teammate's
# own run of `python main.py`.
CACHE_PATH = Path(__file__).resolve().parent / ".dashboard_cache.json"
# When set, /api/run is disabled — protects API budget on a public
# deployment where anyone with the link could otherwise trigger real
# Gemini/OpenAI calls. Local dev leaves this unset.
READ_ONLY = os.environ.get("READ_ONLY", "").lower() in ("1", "true", "yes")

sys.path.insert(0, str(SOLUTION_ROOT))

import main as pipeline  # noqa: E402
import constants  # noqa: E402


def _json_response(start_response, payload, status="200 OK"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    start_response(status, [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


def _csv_response(start_response, rows: list[dict], fieldnames: list[str], filename: str):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    body = buf.getvalue().encode("utf-8-sig")  # BOM so Excel opens UTF-8 cleanly
    start_response("200 OK", [
        ("Content-Type", "text/csv; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Content-Disposition", f'attachment; filename="{filename}"'),
    ])
    return [body]


def _differences_text(report: dict) -> str:
    """Human-readable explanation of what differs, mirroring
    comparator.compare_documents()'s own ``differences`` dict
    ({field: {"si": ..., "bl": ...}}) — built from the live-verified
    ``rows`` when available, since submission.json's baseline only stores
    field *names*, not the actual SI/BL values that differed.
    """
    rows = report.get("rows")
    if rows:
        parts = [
            f"{row['field']}: SI='{row['si']}' vs BL='{row['bl']}'"
            for row in rows if row.get("result") == "MISMATCH"
        ]
        if parts:
            return "; ".join(parts)
    defect_fields = report.get("defect_fields") or []
    if defect_fields:
        return f"{', '.join(defect_fields)} (re-verify this email to see the actual SI/BL values)"
    return ""


def _report_from_submission_entry(sub: dict) -> dict:
    """A submission.json row (no SI/BL detail) -> the dashboard's report shape."""
    return {
        "category": sub.get("category", "GENERAL"),
        "status": sub.get("status", "OK"),
        "mode": "precomputed",
        "classification_reason": "From a previously completed batch run.",
        "review_reason": sub.get("review_reason"),
        "has_defect": bool(sub.get("has_defect", False)),
        "defect_fields": sub.get("defect_fields", []),
        "human_reviewed": False,
    }


def _live_verify(email: dict) -> dict:
    """Run one email through the pipeline, returning the dashboard's report
    shape with the full per-field SI/BL table when the compare stage ran.

    Mirrors main.process_email()'s own control flow (reusing its helpers
    directly) instead of calling process_email() itself, so classify/
    extract/compare each run exactly once per email — process_email()
    only returns the final submission shape, not the SI/BL detail this
    dashboard's comparison table needs.
    """
    email_id = email.get("email_id") or email.get("id") or "<unknown>"

    try:
        cls = pipeline.classify_email(email)
    except Exception:
        cls = {"email_id": email_id, "category": "BL_COMPARISON", "should_process": True}
    category = cls.get("category", "GENERAL")

    if category != "BL_COMPARISON" or not cls.get("should_process"):
        sub = pipeline._submission_for_non_bl(category)
        return {**_report_from_submission_entry(sub), "mode": "rule",
                "classification_reason": "Not a document comparison request."}

    attachments = email.get("attachments") or []
    if not attachments:
        body_lower = (email.get("body") or "").lower()
        cues = ("attachments appear to have been dropped", "attachment appears to have been dropped",
                "attachments missing", "attachment missing", "missing attachment",
                "still missing", "not attached")
        if not any(cue in body_lower for cue in cues):
            sub = pipeline._submission_for_non_bl(category)
            return {**_report_from_submission_entry(sub), "mode": "rule",
                    "classification_reason": "Comparison request with no attachments expected."}

    try:
        ext = pipeline.extract(email, cls, attachment_root=str(pipeline.BUNDLE_DIR))
    except Exception as exc:
        ext = {"email_id": email_id, "parse_status": "failed",
               "reason": "extraction_error", "detail": str(exc), "si": None, "bl": None}

    if ext is None:
        sub = pipeline._submission_for_non_bl(category)
        return {**_report_from_submission_entry(sub), "mode": "llm",
                "classification_reason": "Document comparison request."}

    parse_status = ext.get("parse_status")
    if parse_status == "failed" or ext.get("si") is None or ext.get("bl") is None:
        if ext.get("reason") == "missing_attachment" and pipeline._is_bl_request_email(email):
            sub = {"category": category, "status": "OK", "review_reason": None,
                   "defect_fields": [], "has_defect": False}
        else:
            sub = pipeline._submission_for_extract_failure(ext)
        return {**_report_from_submission_entry(sub), "mode": "llm",
                "classification_reason": "Document comparison request.",
                "review_detail": ext.get("detail")}

    si_cmp = pipeline._adapt_for_comparator(ext["si"])
    bl_cmp = pipeline._adapt_for_comparator(ext["bl"])
    try:
        cmp_result = pipeline.compare_documents(si_cmp, bl_cmp)
    except Exception:
        cmp_result = {"status": "NEEDS_REVIEW", "review_reason": "unreadable",
                      "has_defect": False, "defect_fields": [], "missing_fields": []}

    sub = pipeline._submission_for_comparison(category, cmp_result)
    si_raw = (ext.get("si") or {}).get("raw") or {}
    bl_raw = (ext.get("bl") or {}).get("raw") or {}
    rows = [{
        "field": field,
        "si": si_cmp.get(field),
        "bl": bl_cmp.get(field),
        "si_evidence": si_raw.get(field),
        "bl_evidence": bl_raw.get(field),
        "result": ("MISMATCH" if field in cmp_result.get("defect_fields", [])
                    else "REVIEW" if field in cmp_result.get("missing_fields", [])
                    else "MATCH"),
    } for field in constants.COMPARISON_FIELDS]

    report = _report_from_submission_entry(sub)
    report.update(
        mode="llm",
        classification_reason="Document comparison request.",
        rows=rows,
        documents={"SI": {"fields": si_cmp}, "BL": {"fields": bl_cmp}},
    )
    return report


class Application:
    """WSGI app exposing the trimmed inbox API for inbox_frontend."""

    def __init__(self, bundle_path: str | None = None):
        self.inbox = pipeline.Inbox(bundle_path or str(BUNDLE_ROOT))
        self.emails = list(self.inbox)
        self.index = {e["email_id"]: e for e in self.emails}
        self.reports: dict[str, dict] = {}
        self.job = {"running": False, "completed": 0, "total": 0, "error": None}
        self._lock = threading.Lock()
        self._load_submission_baseline()
        self._load_cache()

    def _load_submission_baseline(self) -> None:
        if not SUBMISSION_PATH.exists():
            return
        try:
            submission = json.loads(SUBMISSION_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for email_id, sub in submission.items():
            try:
                self.reports[email_id] = _report_from_submission_entry(sub)
            except Exception:
                continue

    def _load_cache(self) -> None:
        if not CACHE_PATH.exists():
            return
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for email_id, report in cached.items():
            if email_id in self.index:
                self.reports[email_id] = report

    def _persist_cache(self) -> None:
        """Best-effort write-back so a warm instance survives a quick restart.

        Not a substitute for the submission.json baseline — Cloud Run's disk
        doesn't survive a cold start, only what's baked into the image does.
        """
        try:
            CACHE_PATH.write_text(json.dumps(self.reports, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Background processing
    # ------------------------------------------------------------------
    def _run(self, email_ids: list[str]) -> None:
        with self._lock:
            self.job.update(running=True, completed=0, total=len(email_ids), error=None)
        try:
            for email_id in email_ids:
                email = self.index.get(email_id)
                if email is None:
                    continue
                try:
                    report = _live_verify(email)
                except Exception as exc:
                    report = {
                        "category": "GENERAL", "status": "NEEDS_REVIEW", "mode": "error",
                        "review_reason": "unreadable", "has_defect": False,
                        "defect_fields": [], "human_reviewed": False, "error": str(exc),
                    }
                with self._lock:
                    self.reports[email_id] = report
                    self.job["completed"] += 1
        except Exception as exc:  # pragma: no cover - defensive
            with self._lock:
                self.job["error"] = str(exc)
        finally:
            with self._lock:
                self.job["running"] = False
            self._persist_cache()

    # ------------------------------------------------------------------
    # WSGI routing
    # ------------------------------------------------------------------
    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/")

        if path == "/health":
            return _json_response(start_response, {"status": "ok", "emails": len(self.emails)})
        if path == "/api/state" and method == "GET":
            return self._state(start_response)
        if path.startswith("/api/email/") and method == "GET":
            return self._email(path[len("/api/email/"):], start_response)
        if path == "/api/attachment" and method == "GET":
            return self._attachment(environ, start_response)
        if path == "/api/run" and method == "POST":
            return self._run_endpoint(environ, start_response)
        if path == "/api/export" and method == "GET":
            return self._export(start_response)
        if path == "/api/export.csv" and method == "GET":
            return self._export_csv(environ, start_response)
        if path == "/api/check-score" and method == "POST":
            return self._check_score(environ, start_response)

        return _json_response(start_response, {"error": "not found"}, "404 Not Found")

    def _state(self, start_response):
        with self._lock:
            job = dict(self.job)
        emails = []
        for e in self.emails:
            report = self.reports.get(e["email_id"])
            emails.append({
                "email_id": e["email_id"],
                "subject": e.get("subject", ""),
                "category": (report or {}).get("category", "UNPROCESSED"),
                "status": (report or {}).get("status", "PENDING"),
                "human_reviewed": (report or {}).get("human_reviewed", False),
                "error": (report or {}).get("error"),
            })
        payload = {
            "emails": emails,
            "job": job,
            "cloud_configured": bool(os.getenv("GEMINI_API_KEY")) and bool(os.getenv("OPENAI_API_KEY")),
            "read_only": READ_ONLY,
            "categories": ["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"],
            "fields": list(constants.COMPARISON_FIELDS),
        }
        return _json_response(start_response, payload)

    def _email(self, email_id, start_response):
        email = self.index.get(email_id)
        if email is None:
            return _json_response(start_response, {"error": "unknown email_id"}, "404 Not Found")
        payload = {
            "email": {
                "email_id": email_id,
                "subject": email.get("subject", ""),
                "body": email.get("body", ""),
                "from": email.get("from", ""),
                "attachments": email.get("attachments", []),
            },
            "report": self.reports.get(email_id),
        }
        return _json_response(start_response, payload)

    def _attachment(self, environ, start_response):
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        email_id = (qs.get("email_id") or [None])[0]
        try:
            index = int((qs.get("index") or ["0"])[0])
        except ValueError:
            index = -1
        email = self.index.get(email_id) if email_id else None
        attachments = email.get("attachments", []) if email else []
        if email is None or not (0 <= index < len(attachments)):
            return _json_response(start_response, {"error": "unknown attachment"}, "404 Not Found")
        rel_path = attachments[index]
        data = self.inbox.read_bytes(rel_path)
        filename = rel_path.rsplit("/", 1)[-1]
        start_response("200 OK", [
            ("Content-Type", "application/octet-stream"),
            ("Content-Length", str(len(data))),
            ("Content-Disposition", f'attachment; filename="{filename}"'),
        ])
        return [data]

    def _run_endpoint(self, environ, start_response):
        if READ_ONLY:
            return _json_response(
                start_response,
                {"error": "this deployment is read-only; results were pre-computed and verification is disabled here"},
                "403 Forbidden",
            )
        try:
            size = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            size = 0
        raw = environ["wsgi.input"].read(size) if size else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        email_ids = payload.get("email_ids") or [e["email_id"] for e in self.emails]

        with self._lock:
            if self.job["running"]:
                return _json_response(start_response, {"error": "a run is already in progress"}, "409 Conflict")

        threading.Thread(target=self._run, args=(email_ids,), daemon=True).start()
        return _json_response(start_response, {"started": True, "count": len(email_ids)})

    def _build_submission(self) -> dict:
        submission = {}
        for e in self.emails:
            email_id = e["email_id"]
            report = self.reports.get(email_id)
            if report is None:
                submission[email_id] = {
                    "category": "GENERAL", "status": "OK",
                    "review_reason": None, "has_defect": False, "defect_fields": [],
                }
            else:
                submission[email_id] = {
                    "category": report["category"],
                    "status": report["status"],
                    "review_reason": report.get("review_reason"),
                    "has_defect": report.get("has_defect", False),
                    "defect_fields": report.get("defect_fields", []),
                }
        return submission

    def _export(self, start_response):
        return _json_response(start_response, self._build_submission())

    def _export_csv(self, environ, start_response):
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        only_id = (qs.get("email_id") or [None])[0]

        fieldnames = ["email_id", "subject", "category", "status",
                      "review_reason", "has_defect", "defect_fields", "differences"]
        rows = []
        for e in self.emails:
            email_id = e["email_id"]
            if only_id and email_id != only_id:
                continue
            report = self.reports.get(email_id) or {
                "category": "GENERAL", "status": "OK", "review_reason": None,
                "has_defect": False, "defect_fields": [],
            }
            rows.append({
                "email_id": email_id,
                "subject": e.get("subject", ""),
                "category": report.get("category", "GENERAL"),
                "status": report.get("status", "OK"),
                "review_reason": report.get("review_reason") or "",
                "has_defect": report.get("has_defect", False),
                "defect_fields": ", ".join(report.get("defect_fields") or []),
                "differences": _differences_text(report),
            })

        if only_id and not rows:
            return _json_response(start_response, {"error": "unknown email_id"}, "404 Not Found")

        filename = f"{only_id}.csv" if only_id else "submission.csv"
        return _csv_response(start_response, rows, fieldnames, filename)

    def _check_score(self, environ, start_response):
        try:
            size = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            size = 0
        raw = environ["wsgi.input"].read(size) if size else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        eval_url = str(payload.get("eval_url") or "http://localhost:8080").rstrip("/")

        body = json.dumps(self._build_submission()).encode("utf-8")
        req = urllib.request.Request(
            eval_url + "/submit", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            return _json_response(
                start_response,
                {"error": f"could not reach self-eval server at {eval_url}: {exc}"},
                "502 Bad Gateway",
            )
        except Exception as exc:
            return _json_response(start_response, {"error": str(exc)}, "502 Bad Gateway")
        return _json_response(start_response, result)
