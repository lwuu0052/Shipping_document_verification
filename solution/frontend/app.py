"""Minimal backend for the inbox_frontend dashboard.

Wires the existing pipeline (``main.process_email``, which itself chains
classify -> extract -> compare) to the HTTP API the dashboard's inbox.js
expects. This is intentionally a slim subset of the original HarborCheck
project that inbox_frontend was borrowed from: no admin-token auth, no
SQLite persistence, no offline/cloud mode switch, no human-review/audit
trail. Reports live in memory only and are lost when the server restarts.
"""
from __future__ import annotations

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

sys.path.insert(0, str(SOLUTION_ROOT))

import main as pipeline  # noqa: E402  (exposes process_email, Inbox)
import constants  # noqa: E402

# extraction/pairing failure reason codes (extract/schema.py) -> the
# review_reason enum the hackathon submission format expects.
_REVIEW_REASON_MAP = {
    "missing_attachment": "missing_attachment",
    "pairing_failed": "wrong_doc_type",
    "unsupported_format": "unreadable",
    "parse_error": "unreadable",
    "extraction_error": "unreadable",
    "field_null": "missing_value",
}


def _json_response(start_response, payload, status="200 OK"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    start_response(status, [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
    ])
    return [body]


def _to_report(result: dict) -> dict:
    """Turn main.process_email()'s result into the dashboard's report shape."""
    category = result["category"]
    status = result["status"]

    if status == "SKIPPED":
        return {
            "category": category, "status": "OK", "mode": "rule",
            "classification_reason": "Not a document comparison request.",
            "review_reason": None, "has_defect": False, "defect_fields": [],
            "human_reviewed": False,
        }

    if "comparison" not in result:
        # Extraction-level failure: missing/unreadable/unpairable attachments.
        reason = _REVIEW_REASON_MAP.get(result.get("reason"), "missing_value")
        return {
            "category": category, "status": "NEEDS_REVIEW", "mode": "llm",
            "classification_reason": "Document comparison request.",
            "review_reason": reason, "review_detail": result.get("detail"),
            "has_defect": False, "defect_fields": [], "human_reviewed": False,
        }

    comparison = result["comparison"]
    si_data = result.get("si") or {}
    bl_data = result.get("bl") or {}
    rows = [{
        "field": field,
        "si": si_data.get(field),
        "bl": bl_data.get(field),
        "si_evidence": (si_data.get("raw") or {}).get(field),
        "bl_evidence": (bl_data.get("raw") or {}).get(field),
        "result": ("MISMATCH" if field in comparison["defect_fields"]
                    else "REVIEW" if field in comparison["missing_fields"]
                    else "MATCH"),
    } for field in constants.COMPARISON_FIELDS]

    return {
        "category": category, "status": comparison["status"], "mode": "llm",
        "classification_reason": "Document comparison request.",
        "review_reason": comparison.get("review_reason"),
        "has_defect": comparison.get("has_defect", False),
        "defect_fields": comparison.get("defect_fields", []),
        "rows": rows,
        "documents": {"SI": {"fields": si_data}, "BL": {"fields": bl_data}},
        "human_reviewed": False,
    }


class Application:
    """WSGI app exposing the trimmed inbox API for inbox_frontend."""

    def __init__(self, bundle_path: str | None = None):
        self.inbox = pipeline.Inbox(bundle_path or str(BUNDLE_ROOT))
        self.emails = list(self.inbox)
        self.index = {e["email_id"]: e for e in self.emails}
        self.reports: dict[str, dict] = {}
        self.job = {"running": False, "completed": 0, "total": 0, "error": None}
        self._lock = threading.Lock()

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
                    report = _to_report(pipeline.process_email(email))
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
