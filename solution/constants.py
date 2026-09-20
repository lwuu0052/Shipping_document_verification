"""
constants.py 

Contains categories, target comparison fields, synonym mappings,
and verification statuses.
"""

# 1. EMAIL CATEGORIES
CATEGORY_BL_COMPARISON = "BL_COMPARISON"
CATEGORY_SI_REQUEST = "SI_REQUEST"
CATEGORY_INVOICE_QUERY = "INVOICE_QUERY"
CATEGORY_GENERAL = "GENERAL"
CATEGORY_SPAM = "SPAM"

COMPARISON_FIELDS = [
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
]

STATUS_OK = "OK"
STATUS_MISMATCH = "MISMATCH"
STATUS_NEEDS_REVIEW = "NEEDS_REVIEW"