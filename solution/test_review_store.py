from review_store import load_review_cases, save_review_case, REVIEW_FILE


# Start with a clean test file
if REVIEW_FILE.exists():
    REVIEW_FILE.unlink()


# 1. Save a pending review
review_case = {
    "email_id": "email_test_001",
    "status": "PENDING_REVIEW",
    "review_reason": "missing_value",
    "missing_fields": ["gross_weight_kg"],
}

save_review_case(review_case)


# 2. Load it back
cases = load_review_cases()

assert len(cases) == 1
assert cases[0]["email_id"] == "email_test_001"
assert cases[0]["status"] == "PENDING_REVIEW"


# 3. Simulate human resolving it
review_case["status"] = "RESOLVED"
review_case["resolution"] = "HUMAN_CONFIRMED"

save_review_case(review_case)


# 4. Load again and make sure it was UPDATED,
#    rather than creating a duplicate
cases = load_review_cases()

assert len(cases) == 1
assert cases[0]["status"] == "RESOLVED"
assert cases[0]["resolution"] == "HUMAN_CONFIRMED"


# Clean up test data
REVIEW_FILE.unlink()

print("ALL REVIEW STORE TESTS PASSED")