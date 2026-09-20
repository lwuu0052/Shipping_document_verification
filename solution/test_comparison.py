from comparator import compare_documents


def run_test(name, si, bl, expected_status):
    result = compare_documents(si, bl)

    print("\n" + "=" * 60)
    print(name)
    print("=" * 60)
    print(result)

    assert result["status"] == expected_status


# Base SI data
si = {
    "shipper": "APRIL FINE PAPER TRADING",
    "consignee": "VITAL SOLUTIONS PTE. LTD.",
    "notify_party": "VITAL SOLUTIONS PTE. LTD.",
    "port_of_loading": "NHAVA SHEVA, INDIA",
    "port_of_discharge": "MOMBASA, KENYA",
    "container_count": 3,
    "gross_weight_kg": 23114,
}


# TEST 1: Everything exactly matches
bl1 = si.copy()

run_test(
    "TEST 1 - Exact match",
    si,
    bl1,
    "OK"
)


# TEST 2: Capitalization differences only
bl2 = si.copy()
bl2["shipper"] = "april fine paper trading"
bl2["notify_party"] = "vital solutions pte. ltd."

run_test(
    "TEST 2 - Capitalization differences",
    si,
    bl2,
    "OK"
)


# TEST 3: Extra spaces only
bl3 = si.copy()
bl3["shipper"] = "   APRIL FINE PAPER TRADING   "
bl3["consignee"] = "VITAL   SOLUTIONS PTE. LTD."

run_test(
    "TEST 3 - Extra spaces",
    si,
    bl3,
    "OK"
)


# TEST 4: Numeric values stored as strings
bl4 = si.copy()
bl4["container_count"] = "3"
bl4["gross_weight_kg"] = "23114"

run_test(
    "TEST 4 - Numbers stored as strings",
    si,
    bl4,
    "OK"
)


# TEST 5: Number with comma
bl5 = si.copy()
bl5["gross_weight_kg"] = "23,114"

run_test(
    "TEST 5 - Weight with comma",
    si,
    bl5,
    "OK"
)


# TEST 6: One real mismatch
bl6 = si.copy()
bl6["container_count"] = 4

run_test(
    "TEST 6 - One real mismatch",
    si,
    bl6,
    "MISMATCH"
)


# TEST 7: Multiple real mismatches
bl7 = si.copy()
bl7["consignee"] = "OTHER COMPANY"
bl7["container_count"] = 5
bl7["gross_weight_kg"] = 25000

run_test(
    "TEST 7 - Multiple mismatches",
    si,
    bl7,
    "MISMATCH"
)


# TEST 8: Missing value
bl8 = si.copy()
bl8["gross_weight_kg"] = None

run_test(
    "TEST 8 - Missing value",
    si,
    bl8,
    "NEEDS_REVIEW"
)


# TEST 9: Empty string
bl9 = si.copy()
bl9["notify_party"] = ""

run_test(
    "TEST 9 - Empty string",
    si,
    bl9,
    "NEEDS_REVIEW"
)


# TEST 10: Mismatch + missing value
bl10 = si.copy()
bl10["consignee"] = "OTHER COMPANY"
bl10["gross_weight_kg"] = None

result = compare_documents(si, bl10)

print("\n" + "=" * 60)
print("TEST 10 - Mismatch and missing value")
print("=" * 60)
print(result)

assert result["status"] == "NEEDS_REVIEW"
assert result["has_defect"] is True
assert "consignee" in result["defect_fields"]
assert "gross_weight_kg" in result["missing_fields"]


# TEST 11: Several formatting differences together
bl11 = {
    "shipper": "  april fine paper trading ",
    "consignee": "VITAL   SOLUTIONS PTE. LTD.",
    "notify_party": "vital solutions pte. ltd.",
    "port_of_loading": "NHAVA SHEVA, INDIA",
    "port_of_discharge": "MOMBASA, KENYA",
    "container_count": "3",
    "gross_weight_kg": "23,114",
}

run_test(
    "TEST 11 - Messy formatting but same values",
    si,
    bl11,
    "OK"
)


# TEST 12: Messy formatting + real mismatch
bl12 = bl11.copy()
bl12["container_count"] = "4"

run_test(
    "TEST 12 - Messy formatting with real mismatch",
    si,
    bl12,
    "MISMATCH"
)


print("\nALL TESTS PASSED")