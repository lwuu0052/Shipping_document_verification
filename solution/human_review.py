"""
Human-in-the-loop review module.

Creates a structured review case when the automated pipeline
cannot safely complete a document comparison.

The reviewer receives the reason for escalation together with
the available SI, BL, comparison, and email context.
"""


# REVIEW_REASONS = {
#     "missing_attachment",
#     "pairing_failed",
#     "unsupported_format",
#     "parse_error",
#     "extraction_error",
#     "field_null",
#     "missing_value",
#     "processing_error",
# }

REVIEW_REASONS = {
    "wrong_doc_type",
    "missing_attachment",
    "unreadable",
    "missing_value",
}

REASON_MAPPING = {
    "wrong_doc_type": "wrong_doc_type",
    "missing_attachment": "missing_attachment",

    "unsupported_format": "unreadable",
    "parse_error": "unreadable",
    "extraction_error": "unreadable",

    "field_null": "missing_value",
    "missing_value": "missing_value",

    "pairing_failed": "missing_attachment",
    "processing_error": "unreadable",
}

def normalize_review_reason(reason):
    """Convert an internal failure reason to an official review reason."""
    return REASON_MAPPING.get(reason, "unreadable")

def needs_review(result):
    """
    Return True if a pipeline result requires human intervention.
    """
    return result.get("status") == "NEEDS_REVIEW"


def create_review_case(
    email,
    result,
    extraction=None,
    comparison=None,
):
    """
    Create a review case containing the relevant context.

    Parameters:
        email:
            Original email.

        result:
            Final pipeline result.

        extraction:
            Extractor output, if available.

        comparison:
            Comparator output, if available.

    Returns:
        Review-case dictionary, or None if human review
        is not required.
    """

    if not needs_review(result):
        return None

    # reason = result.get("review_reason") or result.get("reason")
    raw_reason = result.get("review_reason") or result.get("reason")
    reason = normalize_review_reason(raw_reason)

    review_case = {
        "email_id": (
            email.get("email_id")
            or email.get("id")
        ),
        "status": "PENDING_REVIEW",
        "review_reason": reason,

        # Email context
        "subject": email.get("subject"),
        "sender": email.get("from"),

        # Explanation of the problem
        "detail": result.get("detail"),

        # Document context
        "si_data": None,
        "bl_data": None,

        # Comparison context
        "missing_fields": result.get("missing_fields", []),
        "differences": result.get("differences", {}),
        "defect_fields": [],
    }

    # Include extraction evidence when available.
    if extraction:
        review_case["si_data"] = extraction.get("si")
        review_case["bl_data"] = extraction.get("bl")

        if extraction.get("detail"):
            review_case["detail"] = extraction.get("detail")

        if extraction.get("missing_fields"):
            review_case["missing_fields"] = extraction.get(
                "missing_fields"
            )

    # Include comparison evidence when available.
    if comparison:
        review_case["missing_fields"] = comparison.get(
            "missing_fields", []
        )

        review_case["differences"] = comparison.get(
            "differences", {}
        )

        review_case["defect_fields"] = comparison.get(
            "defect_fields", []
        )

    return review_case


def resolve_review_case(
    review_case,
    action,
    corrections=None,
):
    """
    Record the human reviewer's decision.

    Supported actions:

        confirm
            Human confirms the available result.

        correct
            Human supplies corrected field values.

        retry
            Human requests automated processing again.
    """

    if action == "confirm":
        review_case["status"] = "RESOLVED"
        review_case["resolution"] = "HUMAN_CONFIRMED"

    elif action == "correct":

        if not corrections:
            raise ValueError(
                "Corrections are required when action='correct'"
            )

        review_case["status"] = "RESOLVED"
        review_case["resolution"] = "HUMAN_CORRECTED"
        review_case["corrections"] = corrections

    elif action == "retry":
        review_case["status"] = "RETRY_REQUESTED"
        review_case["resolution"] = "RETRY"

    else:
        raise ValueError(
            "Action must be 'confirm', 'correct', or 'retry'"
        )

    return review_case

def handle_review_submission(review_case, submission):
    """Apply a human review submission to a pending review case."""

    action = submission.get("action")

    if action == "confirm":
        return resolve_review_case(
            review_case,
            action="confirm",
        )

    if action == "correct":
        return resolve_review_case(
            review_case,
            action="correct",
            corrections=submission.get("corrections"),
        )

    if action == "retry":
        return resolve_review_case(
            review_case,
            action="retry",
        )

    raise ValueError("Invalid review action")