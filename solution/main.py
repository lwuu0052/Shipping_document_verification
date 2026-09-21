import os
import sys
import json

from classify import classify_email
from extract.extractor import extract
from comparator import compare_documents

sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "sdoc-hackathon-bundle")
    ),
)
from human_review import create_review_case, normalize_review_reason
from review_store import save_review_case

from loader import Inbox


def process_email(email):
    email_id = email.get("email_id") or email.get("id")

    print("\n" + "=" * 60)
    print(f"Processing: {email_id}")
    print("=" * 60)

    # 1. Classify
    classification = classify_email(email)

    print(f"Category: {classification['category']}")

    # Non-comparison email
    if not classification["should_process"]:
        print("No document comparison required.")

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "SKIPPED",
        }

    # 2. Extract
    extraction = extract(email, classification)

    if extraction is None:
        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "SKIPPED",
        }

    # Extraction failure
    if extraction["parse_status"] == "failed":
        print("Extraction failed.")
        print(f"Reason: {extraction.get('reason')}")

        # result = {
        #     "email_id": email_id,
        #     "category": classification["category"],
        #     "status": "NEEDS_REVIEW",
        #     "reason": extraction.get("reason"),
        #     "review_reason": extraction.get("reason"),
        #     "detail": extraction.get("detail"),
        # }

        raw_reason = extraction.get("reason")

        result = {
            "email_id": email_id,
            "category": classification["category"],
            "status": "NEEDS_REVIEW",

            # Keep technical/internal reason for debugging
            "reason": raw_reason,

            # Official challenge reason
            "review_reason": normalize_review_reason(raw_reason),

            "detail": extraction.get("detail"),
        }

        review_case = create_review_case(
            email=email,
            result=result,
            extraction=extraction,
        )

        save_review_case(review_case)

        result["review_case"] = review_case

        return result


    # 3. Get extracted SI / BL
    si_data = extraction["si"]
    bl_data = extraction["bl"]

    print("SI and BL extracted successfully.")

    # 4. Compare
    comparison = compare_documents(si_data, bl_data)

    print(f"Comparison result: {comparison['status']}")

    if comparison["status"] == "MISMATCH":
        print("Mismatch fields:")
        for field in comparison["defect_fields"]:
            print(f"  - {field}")

    elif comparison["status"] == "NEEDS_REVIEW":
        print("Human review required.")
        print(f"Missing fields: {comparison['missing_fields']}")

        review_result = {
            "status": "NEEDS_REVIEW",
            "review_reason": "missing_value",
            "missing_fields": comparison.get("missing_fields", []),
            "differences": comparison.get("differences", {}),
        }

        review_case = create_review_case(
            email=email,
            result=review_result,
            extraction=extraction,
            comparison=comparison,
        )

        print("Review case created:")
        print(review_case)

    else:
        print("SI and BL match.")

    result = {
        "email_id": email_id,
        "category": classification["category"],
        "status": comparison["status"],
        "comparison": comparison,
        "review_reason": comparison["review_reason"],
        "missing_fields": comparison["missing_fields"],
        "differences": comparison["differences"],
    }

    # Attach human-review information when needed
    if comparison["status"] == "NEEDS_REVIEW":
        result["review_case"] = review_case

    return result


def main():
    bundle_path = str(Path(__file__).resolve().parent.parent / "sdoc-hackathon-bundle")

    inbox = Inbox(bundle_path)
    emails = list(inbox)

    print(f"Total emails: {len(emails)}")

    results = []

    for email in emails:
        try:
            result = process_email(email)
            results.append(result)
        except Exception as error:
            email_id = email.get("email_id") or email.get("id") or "unknown"

            print(f"\nERROR processing {email_id}")
            print(error)

            results.append({
                "email_id": email_id,
                "category": "UNKNOWN",
                "status": "ERROR",
                "error": str(error),
            })

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\nResults saved to results.json")


if __name__ == "__main__":
    main()