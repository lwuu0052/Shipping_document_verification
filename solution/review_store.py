import json
from pathlib import Path


REVIEW_FILE = Path("review_cases.json")


def load_review_cases():
    """Load all saved human review cases."""
    if not REVIEW_FILE.exists():
        return []

    with REVIEW_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_review_case(review_case):
    """Save or update a human review case."""
    cases = load_review_cases()

    email_id = review_case.get("email_id")

    # Replace an existing case for the same email
    for index, case in enumerate(cases):
        if case.get("email_id") == email_id:
            cases[index] = review_case
            break
    else:
        cases.append(review_case)

    with REVIEW_FILE.open("w", encoding="utf-8") as file:
        json.dump(cases, file, indent=2)

    return review_case