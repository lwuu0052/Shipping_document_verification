import json
from main import process_email

with open(
    "../sdoc-hackathon-bundle/inbox/email_001.json",
    "r",
    encoding="utf-8"
) as f:
    email = json.load(f)

result = process_email(email)

print(result)