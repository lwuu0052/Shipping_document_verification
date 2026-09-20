# main.py
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
    """
    Process one email:
    1. Classify
    2. Extract SI + BL
    3. Compare SI vs BL
    """

    email_id = email.get("email_id") or email.get("id")

    print("\n" + "=" * 60)
    print(f"Processing: {email_id}")
    print("=" * 60)

    # ---------------------------------------------------------
    # STEP 1: CLASSIFY EMAIL
    # ---------------------------------------------------------
    classification = classify_email(email)

    print(f"Category: {classification['category']}")

    # If this email does not need SI/BL comparison, stop here
    if not classification["should_process"]:
        print("No document comparison required.")

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "SKIPPED"
        }

    # ---------------------------------------------------------
    # STEP 2: EXTRACT SI AND BL
    # ---------------------------------------------------------
    extraction = extract(email, classification)

    # extract() may return None
    if extraction is None:
        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "SKIPPED"
        }

    # Extraction failed
    if extraction["parse_status"] == "failed":

        print("Extraction failed.")
        print(f"Reason: {extraction.get('reason')}")

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "NEEDS_REVIEW",
            "reason": extraction.get("reason"),
            "detail": extraction.get("detail")
        }

    # ---------------------------------------------------------
    # STEP 3: GET EXTRACTED DATA
    # ---------------------------------------------------------
    si_data = extraction["si"]
    bl_data = extraction["bl"]

    print("SI and BL extracted successfully.")

    # ---------------------------------------------------------
    # STEP 4: COMPARE SI VS BL
    # ---------------------------------------------------------
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

    # ---------------------------------------------------------
    # FINAL RESULT
    # ---------------------------------------------------------
    return {
        "email_id": email_id,
        "category": classification["category"],
        "status": comparison["status"],
        "comparison": comparison
    }


def main():
  # Folder containing the hackathon email data
  bundle_path = "../sdoc-hackathon-bundle"

  inbox = Inbox(bundle_path)

  emails = list(inbox)[:15]

  print(f"Total emails to process (Limited to 15): {len(emails)}")

  results = []

  # Process the selected emails
  for email in emails:
    result = process_email(email)
    results.append(result)

  output_filename = "submission.json"
  with open(output_filename, "w", encoding="utf-8") as f:
       json.dump(results, f, ensure_ascii=True, indent=2)

  print(f"\n[Success] Results for first 15 emails saved to {output_filename}")

  # ---------------------------------------------------------
  # FINAL SUMMARY
  # ---------------------------------------------------------
  print("\n")
  print("=" * 60)
  print("FINAL SUMMARY (First 15 Emails)")
  print("=" * 60)

  for result in results:
    print(f"{result['email_id']} | {result['category']} | {result['status']}")


if __name__ == "__main__":
  main()