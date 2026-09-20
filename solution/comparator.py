"""
Receives extracted SI and BL data, normalizes the values, compares
the 7 required shipment fields, and return either OK , MISMATCH , 
or NEEDS_REVIEW. Moreover it also lists mismatched and missing field.
"""


# The 7 fields required by the hackathon
FIELDS = [
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
]


def normalize_text(value):
    """
    Clean text so capitalization and extra spaces do not cause false mismatches.
    """
    

    if value is None:
        return None

    value = str(value).strip()

    if value == "":
        return None

    # Ignore capitalization and repeated spaces
    return " ".join(value.casefold().split())


def normalize_number(value):
    """
    Convert numeric values like '22,000' and 22000 into the same format
    """

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    if value == "":
        return None

    # Remove commas
    value = value.replace(",", "")

    try:
        return float(value)
    except ValueError:
        return None


def normalize_value(field, value):
    """
    Use text or numeric normalization depending on the field.
    """

    if field in ["container_count", "gross_weight_kg"]:
        return normalize_number(value)

    return normalize_text(value)


def compare_documents(si, bl):
    """
    Compare the 7 required SI and BL fields.

    Returns:
    - OK: all fields match
    - MISMATCH: one or more fields differ
    - NEEDS_REVIEW: a required value is missing/unusable
    """

    defect_fields = []
    missing_fields = []
    differences = {}

    for field in FIELDS:

        # Get original values
        si_original = si.get(field)
        bl_original = bl.get(field)

        # Clean values before comparing
        si_value = normalize_value(field, si_original)
        bl_value = normalize_value(field, bl_original)

        # If we cannot get a value from either document,
        # we cannot safely make a comparison.
        if si_value is None or bl_value is None:
            missing_fields.append(field)
            continue

        # Compare
        if si_value != bl_value:

            defect_fields.append(field)

            # Keep values so they can be displayed later
            differences[field] = {
                "si": si_original,
                "bl": bl_original,
            }

    # Missing information -> human should review
    if missing_fields:
        return {
            "status": "NEEDS_REVIEW",
            "review_reason": "missing_value",
            "has_defect": bool(defect_fields),
            "defect_fields": defect_fields,
            "missing_fields": missing_fields,
            "differences": differences,
        }

    # At least one real mismatch
    if defect_fields:
        return {
            "status": "MISMATCH",
            "review_reason": None,
            "has_defect": True,
            "defect_fields": defect_fields,
            "missing_fields": [],
            "differences": differences,
        }

    # Everything matches
    return {
        "status": "OK",
        "review_reason": None,
        "has_defect": False,
        "defect_fields": [],
        "missing_fields": [],
        "differences": {},
    }