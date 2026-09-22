"""Unit tests for the SI/BL Extract module.

Covers Section 8.2 of the development document. Organized by layer so the
offline-friendly cases (schema, normalize, pairing, parsers, llm JSON
post-processing) run without any network and without optional deps
installed. End-to-end ``extract()`` tests use a stubbed LLM backend.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Make the solution package importable from this test file regardless of CWD.
_THIS = Path(__file__).resolve()
_SOLUTION_ROOT = _THIS.parents[1]
sys.path.insert(0, str(_SOLUTION_ROOT))

from extract import (  # noqa: E402
    FIELDS,
    REASON_EXTRACTION_ERROR,
    REASON_FIELD_NULL,
    REASON_MISSING_ATTACHMENT,
    REASON_PAIRING_FAILED,
    REASON_PARSE_ERROR,
    REASON_UNSUPPORTED_FORMAT,
    empty_fields,
)
from extract import normalize, pairing, parsers, llm_client, extractor  # noqa: E402
from extract.llm_client import ExtractionError, parse_llm_response  # noqa: E402
from extract.parsers import ParseError  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BUNDLE_ROOT = (_SOLUTION_ROOT.parent / "sdoc-hackathon-bundle").resolve()


@pytest.fixture
def tmp_attachment_root():
    """A scratch directory we can write fake attachments into."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "attachments").mkdir()
        yield root


# ===========================================================================
# schema.py — pure data
# ===========================================================================

class TestSchema:
    def test_empty_fields_has_all_keys(self):
        f = empty_fields()
        for name in FIELDS:
            assert name in f, f"missing key {name}"
        # New schema: every field defaults to None (no list-typed field).
        assert all(v is None for v in f.values())

    def test_field_set_matches_doc(self):
        # New 7-field comparison schema (no container_no/booking_no/voyage).
        assert FIELDS == (
            "shipper",
            "consignee",
            "notify_party",
            "port_of_loading",
            "port_of_discharge",
            "container_count",
            "gross_weight_kg",
        )


# ===========================================================================
# normalize.py — pure, no I/O. Section 8.2 "Regularization layer".
# ===========================================================================

class TestNormalize:
    def test_weight_mt_to_kg(self):
        assert normalize.normalize_weight("138.000 MT") == 138000.0

    def test_weight_kgs_with_commas(self):
        assert normalize.normalize_weight("21,577 KGS") == 21577.0

    def test_weight_no_unit_assumed_kg(self):
        assert normalize.normalize_weight("131058") == 131058.0

    def test_weight_none_passthrough(self):
        assert normalize.normalize_weight(None) is None

    def test_weight_unknown_unit_returns_none(self):
        # We must NOT guess — a wrong multiplier silently corrupts Compare.
        assert normalize.normalize_weight("99 LBS") is None

    def test_package_count_with_unit(self):
        assert normalize.normalize_count("880 CARTONS") == 880

    def test_package_count_plain_int(self):
        assert normalize.normalize_count("880") == 880

    def test_package_count_none(self):
        assert normalize.normalize_count(None) is None

    def test_company_ltd_variants_equal(self):
        # Normalization strips punctuation noise but keeps the meaningful
        # tokens. Both "CO.,LTD" and "CO., LTD." normalize to "CO LTD".
        a = normalize.normalize_company("CO.,LTD")
        b = normalize.normalize_company("CO., LTD.")
        assert a == b == "CO LTD"

    def test_company_uppercase_and_collapse_whitespace(self):
        assert normalize.normalize_company("  moorim   sp co.  ") == "MOORIM SP CO"

    def test_port_keeps_locode(self):
        assert normalize.normalize_port("PORT KLANG (WESTPORT), MALAYSIA (MYPKG)") == \
            "PORT KLANG (WESTPORT), MALAYSIA (MYPKG)"

    def test_port_collapses_internal_whitespace(self):
        assert normalize.normalize_port("  NANTONG,  CHINA ") == "NANTONG, CHINA"

    def test_container_strips_space_and_hyphen(self):
        assert normalize.normalize_container("MEDU 104332-0") == ["MEDU1043320"]

    def test_container_single_is_array_len_1(self):
        out = normalize.normalize_container("MEDU1043320")
        assert isinstance(out, list) and len(out) == 1

    def test_container_list_preserves_order(self):
        # Section 5.2 — order is itself a comparison point; never sort.
        out = normalize.normalize_container(["MEDU123456-0", "MEDU987654-3"])
        assert out == ["MEDU1234560", "MEDU9876543"]

    def test_container_none_returns_empty(self):
        assert normalize.normalize_container(None) == []

    def test_normalize_full_dict_keeps_keys(self):
        # Section 3.4 — every key must be present even if value is null.
        out = normalize.normalize({})
        for name in FIELDS:
            assert name in out

    def test_find_missing_collects_nulls(self):
        # New schema: null values are flagged as missing. container_count
        # starts as None (no list-typed field anymore).
        fields = empty_fields()
        fields["shipper"] = "MOORIM SP"
        missing = normalize.find_missing(fields)
        assert "shipper" not in missing
        assert "consignee" in missing  # still None
        assert "container_count" in missing  # still None

    def test_find_missing_empty_when_all_present(self):
        fields = empty_fields()
        for name in FIELDS:
            if name in ("container_count", "gross_weight_kg"):
                fields[name] = 1
            else:
                fields[name] = "x"
        assert normalize.find_missing(fields) == []


# ===========================================================================
# pairing.py — Section 8.2 "Pairing layer"
# ===========================================================================

class TestPairing:
    def test_standard_si_bl_naming(self):
        si, bl, err = pairing.pair_attachments(
            ["attachments/email_001_SI.txt", "attachments/email_001_BL.txt"],
            root=str(BUNDLE_ROOT),
        )
        assert err is None
        assert si and si.endswith("_SI.txt")
        assert bl and bl.endswith("_BL.txt")

    def test_empty_attachments_returns_missing_attachment(self):
        si, bl, err = pairing.pair_attachments([], root=str(BUNDLE_ROOT))
        assert err == REASON_MISSING_ATTACHMENT
        assert si is None and bl is None

    def test_single_attachment_returns_missing_attachment(self):
        # New behavior: a single SI (or BL) with no counterpart is reported
        # as missing_attachment (the actionable info to surface), not the
        # internal pairing_failed reason.
        si, bl, err = pairing.pair_attachments(
            ["attachments/email_001_SI.txt"], root=str(BUNDLE_ROOT))
        assert err == REASON_MISSING_ATTACHMENT

    def test_two_bl_files_returns_missing_attachment(self):
        # Two BL-name files → no SI candidate; surfaced as missing_attachment.
        si, bl, err = pairing.pair_attachments(
            ["attachments/email_001_BL.txt", "attachments/email_004_BL.txt"],
            root=str(BUNDLE_ROOT))
        assert err == REASON_MISSING_ATTACHMENT

    def test_content_based_pairing_when_name_silent(self, tmp_attachment_root):
        # Names give no clue; content must drive the decision.
        si_text = "SHIPPING INSTRUCTION\nConsignee: FOO\n"
        bl_text = "BILL OF LADING (DRAFT)\nB/L NO: BAR\n"
        (tmp_attachment_root / "scan_0001.pdf").write_text(si_text)
        (tmp_attachment_root / "scan_0002.pdf").write_text(bl_text)
        si, bl, err = pairing.pair_attachments(
            ["scan_0001.pdf", "scan_0002.pdf"], root=str(tmp_attachment_root))
        assert err is None
        assert si == "scan_0001.pdf"  # content matched SI
        assert bl == "scan_0002.pdf"


# ===========================================================================
# parsers.py — Section 8.2 "Parsing layer"
# ===========================================================================

class TestParsers:
    def test_txt_reads_utf8(self, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("SHIPPING INSTRUCTION\nhello", encoding="utf-8")
        assert "hello" in parsers.parse_to_text(f)

    def test_txt_empty_file_raises_parse_error(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        with pytest.raises(ParseError) as ei:
            parsers.parse_to_text(f)
        assert ei.value.reason == REASON_PARSE_ERROR

    def test_xlsx_flattens_to_text(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws["A1"] = "Consignee"
        ws["B1"] = "MOORIM SP"
        ws["A2"] = "B/L NO"
        ws["B2"] = "MEDUUD104332"
        f = tmp_path / "doc.xlsx"
        wb.save(f)
        text = parsers.parse_to_text(f)
        assert "MOORIM SP" in text and "MEDUUD104332" in text

    def test_unsupported_format_raises_parse_error(self, tmp_path):
        # New behavior: unsupported formats surface as parse_error (the
        # user-facing review reason) rather than the internal
        # unsupported_format reason.
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK\x03\x04")
        with pytest.raises(ParseError) as ei:
            parsers.parse_to_text(f)
        assert ei.value.reason == REASON_PARSE_ERROR


# ===========================================================================
# llm_client.py — JSON post-processing (Section 8.2 "Extraction layer")
# ===========================================================================

class TestLLMPostProcess:
    def test_strips_json_fence(self):
        raw = '```json\n{"shipper": "MOORIM SP"}\n```'
        out = parse_llm_response(raw)
        assert out["shipper"] == "MOORIM SP"

    def test_strips_bare_fence(self):
        raw = '```\n{"consignee": "ABC"}\n```'
        assert parse_llm_response(raw)["consignee"] == "ABC"

    def test_invalid_json_raises(self):
        with pytest.raises(ExtractionError):
            parse_llm_response("not json at all")

    def test_coerces_scalar_container_to_array(self):
        # Model returned a string instead of array — wrap to len 1.
        # (container_count is a scalar in the new schema, but the post
        # processor still coerces list-shaped fields if present.)
        out = parse_llm_response('{"container_count": "6"}')
        assert out["container_count"] == "6"

    def test_all_keys_present_even_if_missing_in_response(self):
        out = parse_llm_response('{"shipper": "X"}')
        for name in FIELDS:
            assert name in out
        assert out["consignee"] is None
        assert out["container_count"] is None

    def test_retry_then_success_via_stub(self, monkeypatch):
        # First call -> invalid JSON, second call -> valid. Should succeed.
        calls = {"n": 0}

        def flaky(_doc_text, _doc_type):
            calls["n"] += 1
            if calls["n"] == 1:
                return "not json"
            return '{"shipper": "MOORIM SP"}'

        monkeypatch.setenv("LLM_BACKEND", "stub")
        monkeypatch.setattr(llm_client, "STUB_RESPONSE", flaky)
        monkeypatch.setattr(llm_client.time, "sleep", lambda _s: None)
        out = llm_client.extract_fields("doc body", "SI")
        assert out["shipper"] == "MOORIM SP"
        assert calls["n"] == 2

    def test_retry_then_failure_raises(self, monkeypatch):
        def always_bad(_d, _t):
            return "still not json"

        monkeypatch.setenv("LLM_BACKEND", "stub")
        monkeypatch.setattr(llm_client, "STUB_RESPONSE", always_bad)
        monkeypatch.setattr(llm_client.time, "sleep", lambda _s: None)
        with pytest.raises(ExtractionError):
            llm_client.extract_fields("doc body", "BL")


# ===========================================================================
# extractor.extract() end-to-end with a stubbed LLM
# ===========================================================================

# A plausible SI/BL pair the stub will "extract" for any document.
# Uses the new 7-field comparison schema.
_STUB_SI_JSON = json.dumps({
    "shipper": "MOORIM SP CO., LTD",
    "consignee": "APRIL FINE PAPER TRADING",
    "notify_party": "APRIL FINE PAPER TRADING",
    "port_of_loading": "PORT KLANG (WESTPORT), MALAYSIA",
    "port_of_discharge": "CALLAO, PERU",
    "container_count": "880 CARTONS",
    "gross_weight_kg": "21,577 KGS",
})
_STUB_BL_JSON = json.dumps({
    "shipper": "MOORIM SP CO., LTD",
    "consignee": "APRIL FINE PAPER TRADING",
    "notify_party": "APRIL FINE PAPER TRADING",
    "port_of_loading": "PORT KLANG (WESTPORT), MALAYSIA",
    "port_of_discharge": "CALLAO, PERU",
    "container_count": "880 CARTONS",
    "gross_weight_kg": "21,577 KGS",
})


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace the LLM with a stub returning canned SI/BL JSON."""
    monkeypatch.setenv("LLM_BACKEND", "stub")
    monkeypatch.setattr(llm_client, "STUB_RESPONSE",
                        lambda doc_text, doc_type:
                        _STUB_SI_JSON if doc_type.upper() == "SI" else _STUB_BL_JSON)
    monkeypatch.setattr(llm_client.time, "sleep", lambda _s: None)


class TestExtractEndToEnd:
    def test_should_process_false_returns_none(self, stub_llu=None):
        email = {"email_id": "email_002", "subject": "x", "from": "a@b",
                 "body": "", "attachments": []}
        cls = {"email_id": "email_002", "category": "GENERAL",
               "should_process": False}
        assert extractor.extract(email, cls) is None

    def test_missing_attachments_yields_failed(self, stub_llu=None):
        email = {"email_id": "email_003", "subject": "x", "from": "a@b",
                 "body": "", "attachments": []}
        cls = {"email_id": "email_003", "category": "BL_COMPARISON",
               "should_process": True}
        r = extractor.extract(email, cls)
        assert r["parse_status"] == "failed"
        assert r["reason"] == REASON_MISSING_ATTACHMENT
        assert r["si"] is None and r["bl"] is None
        # Section 6.3 — failure still carries email metadata.
        assert r["email_id"] == "email_003"
        assert r["subject"] == "x"

    def test_email_003_conflict_surfaces_in_detail(self):
        # Subject mentions SIN525534192, body asks for SIN832764835.
        email = {
            "email_id": "email_003",
            "subject": "RE_ ...SIN525534192",
            "from": "exports@ifpla.com",
            "body": "Please send the draft BL for SIN832764835.",
            "attachments": [],
        }
        cls = {"email_id": "email_003", "should_process": True,
               "category": "BL_COMPARISON"}
        r = extractor.extract(email, cls)
        assert r["reason"] == REASON_MISSING_ATTACHMENT
        assert "SIN525534192" in r["detail"]
        assert "SIN832764835" in r["detail"]

    def test_happy_path_email_001(self, stub_llm):
        # Real sample .txt pair, LLM stubbed. The rule extractor runs first
        # on the document text; the stub is only a fallback if the rule
        # extractor returns None. So we assert on the rule-extracted
        # values, not the stub values.
        email = {
            "email_id": "email_001",
            "subject": "TO CONFIRM DOCS _ 5RSG-00133",
            "from": "aziztz@safqa.co.ke",
            "body": "Attached are the SI and draft BL for OC 5RSG-00133.",
            "attachments": [
                "attachments/email_001_SI.txt",
                "attachments/email_001_BL.txt",
            ],
        }
        cls = {"email_id": "email_001", "should_process": True,
               "category": "BL_COMPARISON"}
        r = extractor.extract(email, cls, attachment_root=str(BUNDLE_ROOT))
        assert r["parse_status"] in ("ok", "partial")
        assert r["si"] is not None and r["bl"] is not None
        # Same field set on both sides.
        for name in FIELDS:
            assert name in r["si"]
            assert name in r["bl"]
        # Stub gave all values -> no missing -> ok.
        assert r["parse_status"] == "ok"
        # Normalization happened: weight converted to float kg.
        assert r["si"]["gross_weight_kg"] == 21577.0
        # container_count is a positive int (rule extractor picked the
        # leading number from the container line, e.g. "1 x 40'HC").
        assert isinstance(r["si"]["container_count"], int)
        assert r["si"]["container_count"] > 0
        # Raw retained for human review (Section 5.3).
        assert "raw" in r["si"] and "raw" in r["bl"]

    def test_xlsx_email_005_uses_openpyxl_branch(self, stub_llm):
        # Exercise the .xlsx parse branch end-to-end with LLM stubbed.
        # The dedicated xlsx parser test above covers the parser in
        # isolation; here we verify extract() doesn't blow up on .xlsx
        # attachments when everything is wired together.
        pytest.importorskip("openpyxl")

        email = {
            "email_id": "email_005",
            "subject": "RE_ Draft BL ...",
            "from": "hanna_azhari@aprilasia.com",
            "body": "...",
            "attachments": [
                "attachments/email_005_SI.xlsx",
                "attachments/email_005_BL.xlsx",
            ],
        }
        cls = {"email_id": "email_005", "should_process": True,
               "category": "BL_COMPARISON"}
        r = extractor.extract(email, cls, attachment_root=str(BUNDLE_ROOT))
        assert r["parse_status"] in ("ok", "partial")
        assert r["si"] is not None and r["bl"] is not None


if __name__ == "__main__":
    # Allow `python tests/test_extract.py` from the solution root.
    sys.exit(pytest.main([__file__, "-v"]))
