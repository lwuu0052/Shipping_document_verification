from review_cli import review_case_cli


review_case = {
    "email_id": "email_001",
    "status": "PENDING_REVIEW",
    "review_reason": "missing_value",
    "subject": "Test shipping document",
    "sender": "customer@example.com",
    "detail": "Gross weight could not be extracted.",
    "missing_fields": ["gross_weight_kg"],
    "differences": {},
    "defect_fields": [],
    "si_data": {
        "gross_weight_kg": None,
    },
    "bl_data": {
        "gross_weight_kg": 22000,
    },
}


result = review_case_cli(review_case)

print("\n" + "=" * 60)
print("REVIEW RESULT")
print("=" * 60)
print(result)