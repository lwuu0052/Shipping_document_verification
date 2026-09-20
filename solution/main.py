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

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "NEEDS_REVIEW",
            "reason": extraction.get("reason"),
            "detail": extraction.get("detail"),
        }

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

    else:
        print("SI and BL match.")

    return {
        "email_id": email_id,
        "category": classification["category"],
        "status": comparison["status"],
        "comparison": comparison,
    }


def main():
    bundle_path = "../sdoc-hackathon-bundle"

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