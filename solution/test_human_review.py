from human_review import (
    needs_review,
    create_review_case,
    resolve_review_case,
)


# ============================================================
# Shared test data
# ============================================================

email = {
    "id": "email_001",
    "subject": "Please compare SI and BL",
    "from": "customer@example.com",
}

si_data = {
    "shipper": "ABC Shipping",
    "consignee": "XYZ Trading",
    "notify_party": "XYZ Trading",
    "port_of_loading": "Port Klang",
    "port_of_discharge": "Singapore",
    "container_count": 2,
    "gross_weight_kg": None,
}

bl_data = {
    "shipper": "ABC Shipping",
    "consignee": "XYZ Trading",
    "notify_party": "XYZ Trading",
    "port_of_loading": "Port Klang",
    "port_of_discharge": "Singapore",
    "container_count": 2,
    "gross_weight_kg": 22000,
}


# ============================================================
# Test 1: OK does not require human review
# ============================================================

ok_result = {
    "status": "OK",
}

assert needs_review(ok_result) is False


# ============================================================
# Test 2: MISMATCH does not require human review
# ============================================================

mismatch_result = {
    "status": "MISMATCH",
    "differences": {
        "container_count": {
            "si": 2,
            "bl": 3,
        }
    },
}

assert needs_review(mismatch_result) is False


# ============================================================
# Test 3: NEEDS_REVIEW requires human review
# ============================================================

review_result = {
    "status": "NEEDS_REVIEW",
    "review_reason": "missing_value",
    "missing_fields": ["gross_weight_kg"],
    "differences": {},
}

assert needs_review(review_result) is True


# ============================================================
# Test 4: Create review case for missing value
# ============================================================

extraction_result = {
    "parse_status": "partial",
    "reason": "field_null",
    "missing_fields": ["gross_weight_kg"],
    "si": si_data,
    "bl": bl_data,
}

comparison_result = {
    "status": "NEEDS_REVIEW",
    "review_reason": "missing_value",
    "missing_fields": ["gross_weight_kg"],
    "differences": {},
    "defect_fields": [],
}

review_case = create_review_case(
    email=email,
    result=review_result,
    extraction=extraction_result,
    comparison=comparison_result,
)

assert review_case is not None
assert review_case["email_id"] == "email_001"
assert review_case["status"] == "PENDING_REVIEW"
assert review_case["review_reason"] == "missing_value"
assert "gross_weight_kg" in review_case["missing_fields"]

assert review_case["si_data"] == si_data
assert review_case["bl_data"] == bl_data


# ============================================================
# Test 5: Missing attachment review
# ============================================================

missing_attachment_result = {
    "status": "NEEDS_REVIEW",
    "review_reason": "missing_attachment",
    "detail": "Comparison request has no attachments.",
}

missing_attachment_case = create_review_case(
    email=email,
    result=missing_attachment_result,
)

assert missing_attachment_case is not None
assert missing_attachment_case["status"] == "PENDING_REVIEW"
assert (
    missing_attachment_case["review_reason"]
    == "missing_attachment"
)


# ============================================================
# Test 6: Normal result should not create a review case
# ============================================================

no_review_case = create_review_case(
    email=email,
    result=ok_result,
)

assert no_review_case is None


# ============================================================
# Test 7: Human confirms result
# ============================================================

confirm_case = review_case.copy()

confirmed = resolve_review_case(
    confirm_case,
    action="confirm",
)

assert confirmed["status"] == "RESOLVED"
assert confirmed["resolution"] == "HUMAN_CONFIRMED"


# ============================================================
# Test 8: Human corrects result
# ============================================================

correct_case = review_case.copy()

corrected = resolve_review_case(
    correct_case,
    action="correct",
    corrections={
        "gross_weight_kg": 22000,
    },
)

assert corrected["status"] == "RESOLVED"
assert corrected["resolution"] == "HUMAN_CORRECTED"
assert corrected["corrections"]["gross_weight_kg"] == 22000


# ============================================================
# Test 9: Human requests retry
# ============================================================

retry_case = review_case.copy()

retried = resolve_review_case(
    retry_case,
    action="retry",
)

assert retried["status"] == "RETRY_REQUESTED"
assert retried["resolution"] == "RETRY"


# ============================================================
# Test 10: Invalid action raises error
# ============================================================

invalid_case = review_case.copy()

try:
    resolve_review_case(
        invalid_case,
        action="invalid_action",
    )

    assert False, "Expected ValueError for invalid action"

except ValueError:
    pass


# ============================================================
# Test 11: Correction requires corrected values
# ============================================================

missing_correction_case = review_case.copy()

try:
    resolve_review_case(
        missing_correction_case,
        action="correct",
    )

    assert False, "Expected ValueError when corrections are missing"

except ValueError:
    pass

# Test internal reasons are mapped to official review reasons
mapping_tests = {
    "wrong_doc_type": "wrong_doc_type",
    "missing_attachment": "missing_attachment",
    "pairing_failed": "missing_attachment",
    "unsupported_format": "unreadable",
    "parse_error": "unreadable",
    "extraction_error": "unreadable",
    "processing_error": "unreadable",
    "field_null": "missing_value",
    "missing_value": "missing_value",
}

for raw_reason, expected_reason in mapping_tests.items():
    result = {
        "status": "NEEDS_REVIEW",
        "review_reason": raw_reason,
        "detail": f"Test case for {raw_reason}",
    }

    review_case = create_review_case(
        email={
            "email_id": "test_email",
            "subject": "Test",
            "from": "test@example.com",
        },
        result=result,
    )

    assert review_case["review_reason"] == expected_reason

print("ALL HUMAN REVIEW TESTS PASSED")