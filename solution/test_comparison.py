from comparison import compare_documents

si = {
    "shipper": "APRIL FAR EAST (M) SDN BHD",
    "consignee": "MOORIM SP CO., LTD",
    "notify_party": "UAB NOVAKOPA",
    "port_of_loading": "PORT KLANG (WESTPORT), MALAYSIA (MYPKG)",
    "port_of_discharge": "CALLAO, PERU (PECLL)",
    "container_count": 1,
    "gross_weight_kg": 21577,
}

bl = {
    "shipper": "APRIL FAR EAST (M) SDN BHD",
    "consignee": "MOORIM SP CO., LTD",
    "notify_party": "UAB NOVAKOPA",
    "port_of_loading": "PORT KLANG (WESTPORT), MALAYSIA (MYPKG)",
    "port_of_discharge": "CALLAO, PERU (PECLL)",
    "container_count": 1,
    "gross_weight_kg": 21577,
}

result = compare_documents(si, bl)

print(result)
