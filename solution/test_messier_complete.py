import tempfile
from pathlib import Path

from extract.pairing import pair_attachments
from extract.normalize import normalize
from comparator import compare_documents


# ============================================================
# TEST 1 — Generic attachment filenames
# System should inspect CONTENT, not filename only
# ============================================================

print("\nTEST 1 - Generic attachment filenames")

with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)

    si_file = root / "document1.txt"
    bl_file = root / "document2.txt"

    si_file.write_text(
        """
        SHIPPING INSTRUCTION

        Shipper: ABC TRADING
        Consignee: VITAL SOLUTIONS
        """,
        encoding="utf-8"
    )

    bl_file.write_text(
        """
        BILL OF LADING

        Shipper: ABC TRADING
        Consignee: VITAL SOLUTIONS
        """,
        encoding="utf-8"
    )

    si_path, bl_path, error = pair_attachments(
        ["document1.txt", "document2.txt"],
        temp_dir
    )

    print("SI:", si_path)
    print("BL:", bl_path)
    print("Error:", error)

    assert si_path == "document1.txt"
    assert bl_path == "document2.txt"
    assert error is None


# ============================================================
# TEST 2 — Formatting differences should NOT be mismatch
# ============================================================

print("\nTEST 2 - Formatting differences")

si = normalize({
    "shipper": "ABC TRADING CO., LTD.",
    "consignee": "VITAL SOLUTIONS PTE. LTD.",
    "notify_party": "VITAL SOLUTIONS PTE. LTD.",
    "port_of_loading": "PORT KLANG, MALAYSIA",
    "port_of_discharge": "MOMBASA, KENYA",
    "container_count": "3 x 20'GP",
    "gross_weight_kg": "23,114 KG",
})

bl = normalize({
    "shipper": "abc trading co., ltd",
    "consignee": "Vital   Solutions Pte. Ltd",
    "notify_party": "VITAL SOLUTIONS PTE. LTD",
    "port_of_loading": "  PORT KLANG, MALAYSIA ",
    "port_of_discharge": "MOMBASA, KENYA",
    "container_count": "3",
    "gross_weight_kg": "23114",
})

result = compare_documents(si, bl)

print(result)

assert result["status"] == "OK"


# ============================================================
# TEST 3 — Real discrepancy hidden inside messy formatting
# ============================================================

print("\nTEST 3 - Real discrepancy")

bl_mismatch = bl.copy()
bl_mismatch["container_count"] = 4

result = compare_documents(si, bl_mismatch)

print(result)

assert result["status"] == "MISMATCH"
assert "container_count" in result["defect_fields"]


# ============================================================
# TEST 4 — Missing information should go to human review
# ============================================================

print("\nTEST 4 - Missing required value")

bl_missing = bl.copy()
bl_missing["gross_weight_kg"] = None

result = compare_documents(si, bl_missing)

print(result)

assert result["status"] == "NEEDS_REVIEW"
assert "gross_weight_kg" in result["missing_fields"]

# ============================================================
# TEST 5 — Ambiguous attachments
# ============================================================

print("\nTEST 5 - Ambiguous attachments")

with tempfile.TemporaryDirectory() as temp_dir:
    root = Path(temp_dir)

    (root / "doc1.txt").write_text(
        "SHIPPING INSTRUCTION\nShipper: ABC",
        encoding="utf-8"
    )

    (root / "doc2.txt").write_text(
        "SHIPPING INSTRUCTION\nShipper: XYZ",
        encoding="utf-8"
    )

    si_path, bl_path, error = pair_attachments(
        ["doc1.txt", "doc2.txt"],
        temp_dir
    )

    print("Error:", error)

    assert error == "pairing_failed"


# ============================================================
# TEST 6 — Same weight expressed in MT vs KG
# ============================================================

print("\nTEST 6 - MT vs KG")

si_weight = normalize({
    "shipper": "ABC",
    "consignee": "XYZ",
    "notify_party": "XYZ",
    "port_of_loading": "PORT KLANG",
    "port_of_discharge": "MOMBASA",
    "container_count": "2",
    "gross_weight_kg": "23.114 MT",
})

bl_weight = normalize({
    "shipper": "ABC",
    "consignee": "XYZ",
    "notify_party": "XYZ",
    "port_of_loading": "PORT KLANG",
    "port_of_discharge": "MOMBASA",
    "container_count": "2",
    "gross_weight_kg": "23,114 KG",
})

result = compare_documents(si_weight, bl_weight)

print(result)

assert result["status"] == "OK"


# ============================================================
# TEST 7 — Real company mismatch must not be hidden
# ============================================================

print("\nTEST 7 - Real company mismatch")

bl_company = si.copy()
bl_company["consignee"] = "COMPLETELY DIFFERENT COMPANY"

result = compare_documents(si, bl_company)

print(result)

assert result["status"] == "MISMATCH"
assert "consignee" in result["defect_fields"]


# ============================================================
# TEST 8 — Missing notify party
# ============================================================

print("\nTEST 8 - Missing notify party")

bl_notify = bl.copy()
bl_notify["notify_party"] = None

result = compare_documents(si, bl_notify)

print(result)

assert result["status"] == "NEEDS_REVIEW"
assert "notify_party" in result["missing_fields"]

print("\nALL MESSIER INPUT TESTS PASSED")