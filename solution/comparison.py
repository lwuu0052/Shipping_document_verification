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
    Make text easier to compare.

    Example:
    " PORT KLANG " -> "port klang"
    "Port Klang"   -> "port klang"
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
    Make numbers easier to compare.

    Examples:
    "22,000"    -> 22000
    "22000"     -> 22000
    22000       -> 22000
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
    Decide how each field should be normalized.
    """

    if field in ["container_count", "gross_weight_kg"]:
        return normalize_number(value)

    return normalize_text(value)


def compare_documents(si, bl):
    """
    Compare extracted SI data against extracted BL data.

    Returns:
        OK
        MISMATCH
        NEEDS_REVIEW
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
            "has_defect": False,
            "defect_fields": [],
            "missing_fields": missing_fields,
            "differences": {},
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