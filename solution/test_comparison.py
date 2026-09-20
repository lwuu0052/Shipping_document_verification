import json
from comparator import compare_documents


si = {
    "shipper": "ABC SDN BHD",
    "consignee": "XYZ LTD",
    "notify_party": "XYZ LTD",
    "port_of_loading": "Port Klang",
    "port_of_discharge": "Singapore",
    "container_count": 3,
    "gross_weight_kg": 22000,
}

bl = {
    "shipper": "ABC SDN BHD",
    "consignee": "OTHER COMPANY",
    "notify_party": "XYZ LTD",
    "port_of_loading": "Port Klang",
    "port_of_discharge": "Singapore",
    "container_count": 3,
    "gross_weight_kg": None,
}

result = compare_documents(si, bl)

with open("comparison_result.json", "w", encoding="utf-8") as file:
    json.dump(result, file, indent=4)

print("Saved to comparison_result.json")