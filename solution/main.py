<<<<<<< HEAD
# ============================================================
# main.py
# Main pipeline:
#
# Email
#   ↓
# Classify
#   ↓
# Extract SI + BL
#   ↓
# Compare
#   ↓
# Final result
# ============================================================

import os
import json

from loader import Inbox
from classify import classify_email
from comparator import compare_documents

# extractor.py uses relative imports such as:
# from .llm_client import ...
# Therefore extractor should normally be inside an extractor package.
from extract.extractor import extract


# ============================================================
# Process ONE email
# ============================================================

def process_email(email, attachment_root):

    # Get email ID
    email_id = email.get("email_id") or email.get("id") or "unknown"

    print("\n" + "=" * 60)
    print(f"Processing email: {email_id}")
    print("=" * 60)

    # --------------------------------------------------------
    # STEP 1: CLASSIFY EMAIL
    # --------------------------------------------------------

    classification = classify_email(email)

    print(f"Category       : {classification['category']}")
    print(f"Should process : {classification['should_process']}")

    # If email is not an SI/BL comparison request,
    # no extraction or comparison is needed.
    if not classification["should_process"]:

        print("Result         : SKIPPED")
=======
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
>>>>>>> 57264dbd5220ccdcff474f493c6e317a6d8bcc19

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "SKIPPED"
        }

<<<<<<< HEAD
    # --------------------------------------------------------
    # STEP 2: EXTRACT SI AND BL
    # --------------------------------------------------------

    extraction = extract(
        email,
        classification,
        attachment_root=attachment_root
    )

    # Safety check
    if extraction is None:

        print("Result         : SKIPPED")

=======
    # ---------------------------------------------------------
    # STEP 2: EXTRACT SI AND BL
    # ---------------------------------------------------------
    extraction = extract(email, classification)

    # extract() may return None
    if extraction is None:
>>>>>>> 57264dbd5220ccdcff474f493c6e317a6d8bcc19
        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "SKIPPED"
        }

<<<<<<< HEAD
    print(f"Parse status   : {extraction.get('parse_status')}")

    # --------------------------------------------------------
    # If extraction failed
    # --------------------------------------------------------

    if extraction.get("parse_status") == "failed":

        print("Result         : NEEDS_REVIEW")
        print(f"Reason         : {extraction.get('reason')}")
        print(f"Detail         : {extraction.get('detail')}")
=======
    # Extraction failed
    if extraction["parse_status"] == "failed":

        print("Extraction failed.")
        print(f"Reason: {extraction.get('reason')}")
>>>>>>> 57264dbd5220ccdcff474f493c6e317a6d8bcc19

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "NEEDS_REVIEW",
            "reason": extraction.get("reason"),
            "detail": extraction.get("detail")
        }

<<<<<<< HEAD
    # --------------------------------------------------------
    # STEP 3: GET SI AND BL DATA
    # --------------------------------------------------------

    si_data = extraction.get("si")
    bl_data = extraction.get("bl")

    # Safety check
    if not si_data or not bl_data:

        print("Result         : NEEDS_REVIEW")
        print("Reason         : SI or BL data is missing")

        return {
            "email_id": email_id,
            "category": classification["category"],
            "status": "NEEDS_REVIEW",
            "reason": "SI or BL data is missing"
        }

    print("SI extraction  : OK")
    print("BL extraction  : OK")

    # --------------------------------------------------------
    # STEP 4: COMPARE SI VS BL
    # --------------------------------------------------------

    comparison = compare_documents(
        si_data,
        bl_data
    )

    status = comparison["status"]

    print(f"Comparison     : {status}")

    # --------------------------------------------------------
    # Print comparison details
    # --------------------------------------------------------

    if status == "OK":

        print("Result         : SI and BL match")

    elif status == "MISMATCH":

        print("Result         : Mismatch detected")

        print("\nMismatch fields:")

        for field in comparison["defect_fields"]:

            difference = comparison["differences"].get(field, {})

            print(f"  {field}")
            print(f"    SI : {difference.get('si')}")
            print(f"    BL : {difference.get('bl')}")

    elif status == "NEEDS_REVIEW":

        print("Result         : Human review required")

        print(
            "Missing fields :",
            comparison.get("missing_fields", [])
        )

    # --------------------------------------------------------
    # FINAL RESULT FOR THIS EMAIL
    # --------------------------------------------------------

    return {
        "email_id": email_id,
        "category": classification["category"],
        "status": status,
=======
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
>>>>>>> 57264dbd5220ccdcff474f493c6e317a6d8bcc19
        "comparison": comparison
    }


<<<<<<< HEAD
# ============================================================
# Main program
# ============================================================

def main():

    print("\n")
    print("=" * 60)
    print("        SI / BL DOCUMENT CHECKING SYSTEM")
    print("=" * 60)

    # --------------------------------------------------------
    # Find hackathon bundle folder
    # --------------------------------------------------------

    current_folder = os.path.dirname(
        os.path.abspath(__file__)
    )

    # First try:
    # project/sdoc-hackathon-bundle
    bundle_path = os.path.join(
        current_folder,
        "sdoc-hackathon-bundle"
    )

    # If not there, try:
    # project/src/main.py
    # project/sdoc-hackathon-bundle
    if not os.path.exists(bundle_path):

        bundle_path = os.path.abspath(
            os.path.join(
                current_folder,
                "..",
                "sdoc-hackathon-bundle"
            )
        )

    print(f"\nBundle path: {bundle_path}")

    # --------------------------------------------------------
    # Load emails
    # --------------------------------------------------------

    try:

        inbox = Inbox(bundle_path)

        emails = list(inbox)

    except Exception as error:

        print("\nERROR: Unable to load inbox.")
        print(error)

        return

    print(f"Total emails: {len(emails)}")

    # Store all results
    results = []

    # --------------------------------------------------------
    # Process every email
    # --------------------------------------------------------

    for email in emails:

        try:

            result = process_email(
                email,
                bundle_path
            )

            results.append(result)

        except Exception as error:

            # One broken email should not stop the whole program

            email_id = (
                email.get("email_id")
                or email.get("id")
                or "unknown"
            )

            print(f"\nERROR processing {email_id}")
            print(error)

            results.append({
                "email_id": email_id,
                "category": "UNKNOWN",
                "status": "ERROR",
                "error": str(error)
            })

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    print("\n")
    print("=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)

    for result in results:

        print(
            f"{result['email_id']:<15}"
            f" | {result['category']:<20}"
            f" | {result['status']}"
        )

    # ========================================================
    # SAVE RESULTS TO JSON
    # ========================================================

    output_file = os.path.join(
        current_folder,
        "results.json"
    )

    try:

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                results,
                file,
                indent=4,
                ensure_ascii=False,
                default=str
            )

        print("\n" + "=" * 60)
        print(f"Results saved to: {output_file}")
        print("=" * 60)

    except Exception as error:

        print("\nCould not save results.json")
        print(error)


# ============================================================
# Run program
# ============================================================

if __name__ == "__main__":
    main()
=======
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
>>>>>>> 57264dbd5220ccdcff474f493c6e317a6d8bcc19
