import json
from pathlib import Path

from human_review import resolve_review_case
from review_store import load_review_cases, save_review_case

RESULTS_FILE = Path("submission.json")


def update_report(review_case):
    """Update submission.json after human review."""

    if not RESULTS_FILE.exists():
        print("Warning: submission.json not found.")
        return

    with RESULTS_FILE.open("r", encoding="utf-8") as file:
        results = json.load(file)

    email_id = review_case.get("email_id")

    if email_id not in results:
        print(f"Warning: {email_id} not found in submission.json.")
        return

    result = results[email_id]

    result["human_review"] = {
        "resolution": review_case.get("resolution"),
        "corrections": review_case.get("corrections", {}),
    }

    if review_case["status"] == "RESOLVED":
        result["status"] = "REVIEWED"

    with RESULTS_FILE.open("w", encoding="utf-8") as file:
        json.dump(results, file, indent=2)

    print("Report updated.")

def review_pending_cases():
    cases = load_review_cases()

    pending_cases = [
        case for case in cases
        if case.get("status") == "PENDING_REVIEW"
    ]

    if not pending_cases:
        print("No pending review cases.")
        return

    print(f"\nPending review cases: {len(pending_cases)}")

    for review_case in pending_cases:
        print("\n" + "=" * 60)
        print(f"Email ID: {review_case.get('email_id')}")
        print(f"Subject: {review_case.get('subject')}")
        print(f"Sender: {review_case.get('sender')}")
        print(f"Reason: {review_case.get('review_reason')}")
        print(f"Detail: {review_case.get('detail')}")
        print(f"Missing fields: {review_case.get('missing_fields')}")
        print(f"Differences: {review_case.get('differences')}")

        print("\nSource evidence:")
        print(f"SI data: {review_case.get('si_data')}")
        print(f"BL data: {review_case.get('bl_data')}")
        print(f"Defect fields: {review_case.get('defect_fields')}")

        print("\nHuman action:")
        print("1. Confirm")
        print("2. Correct")
        print("3. Retry")

        choice = input("Choose 1, 2, or 3: ").strip()

        if choice == "1":
            review_case = resolve_review_case(
                review_case,
                action="confirm",
            )

        elif choice == "2":
            field = input("Field to correct: ").strip()
            value = input("Correct value: ").strip()

            corrections = {
                field: value
            }

            review_case = resolve_review_case(
                review_case,
                action="correct",
                corrections=corrections,
            )

        elif choice == "3":
            review_case = resolve_review_case(
                review_case,
                action="retry",
            )

        else:
            print("Invalid choice. Review skipped.")
            continue

        save_review_case(review_case)

        if review_case["status"] == "RESOLVED":
            update_report(review_case)

        print(
            f"Saved: {review_case['status']} "
            f"({review_case['resolution']})"
        )


if __name__ == "__main__":
    review_pending_cases()