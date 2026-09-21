from functools import lru_cache
import warnings
from typing import Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv
warnings.filterwarnings("ignore")
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from constants import (
    CATEGORY_BL_COMPARISON,
    CATEGORY_SI_REQUEST,
    CATEGORY_INVOICE_QUERY,
    CATEGORY_GENERAL,
    CATEGORY_SPAM,
)

load_dotenv()

# ============================================================
# Schema specification for LLM structured output
# ============================================================
class EmailClassificationResult(BaseModel):
    category: Literal[
        "document_comparison_request",
        "new_si_request",
        "invoice_query",
        "general_message",
        "spam"
    ] = Field(description="The primary intent category of the email.")
    confidence: float = Field(description="Confidence score between 0.0 and 1.0.")
    reasoning: str = Field(description="Brief explanation of why this category was chosen based on email context.")


# ============================================================
# Prompt for classifying emails
# ============================================================
CLASSIFICATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are an AI email triage assistant for a shipping and logistics company. "
        "Analyze the email subject, body, and attachment filenames to determine the sender's primary intent.\n\n"
        "Categories:\n"
        "- document_comparison_request: Sender wants to compare, cross-check, or verify Shipping Instructions (SI) against a Bill of Lading (BL).\n"
        "- new_si_request: Sender is submitting or requesting to issue a new Shipping Instruction.\n"
        "- invoice_query: Inquiries regarding billing, freight charges, payment status, or invoices.\n"
        "- spam: Unsolicited sales, phishing, marketing, or irrelevant spam messages.\n"
        "- general_message: General inquiries, greetings, or logistics questions that do not fit the other categories."
    ),
    (
        "human",
        "Email Subject: {subject}\n"
        "Email Body:\n{body}\n"
        "Attachment Names: {attachments}\n"
    )
])

@lru_cache(maxsize=1)
def get_classification_chain():
    """Initialize the API client only when classification is requested."""
    base_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return CLASSIFICATION_PROMPT | base_llm.with_structured_output(EmailClassificationResult)


# ============================================================
# Attachment Detection with Edge Case Handling
# ============================================================
# Get the subject ,body and attachment names from the email
def parse_email_metadata(email: dict):
    subject = email.get("subject") or ""
    body = email.get("body") or ""
    raw_attachments = email.get("attachments") or []
    attachment_names = [str(att) for att in raw_attachments]
    return subject, body, attachment_names


# ============================================================
# Decision & category classification (Main function)
# ============================================================
def classify_email(email: dict):
    subject, body, names = parse_email_metadata(email)

    # Step 1: Use LLM to classify the email
    raw_output = get_classification_chain().invoke({
        "subject": subject,
        "body": body,
        "attachments": ", ".join(names) if names else "None"
    })
    
    if isinstance(raw_output, EmailClassificationResult):
        predicted_category = raw_output.category
    elif isinstance(raw_output, dict):
        predicted_category = raw_output.get("category", "general_message")
    else:
        data = raw_output.model_dump()
        predicted_category = data.get("category", "general_message")

    # Step 2: Map to official category constants
    category_map = {
        "document_comparison_request": CATEGORY_BL_COMPARISON,
        "new_si_request": CATEGORY_SI_REQUEST,
        "invoice_query": CATEGORY_INVOICE_QUERY,
        "general_message": CATEGORY_GENERAL,
        "spam": CATEGORY_SPAM,
    }
    final_category = category_map.get(predicted_category, CATEGORY_GENERAL)
    return {
        "email_id": email.get("id") or email.get("email_id"),
        "category": final_category,
        "should_process": (final_category == CATEGORY_BL_COMPARISON),
    }


# if __name__ == "__main__":
#     from functools import lru_cache
#     import sys

#     sys.path.insert(
#       0,
#       os.path.abspath(
#           os.path.join(os.path.dirname(__file__), "..", "sdoc-hackathon-bundle")
#       ),
#     )

#     from loader import Inbox

#     bundle_path = os.path.abspath(
#       os.path.join(os.path.dirname(__file__), "..", "sdoc-hackathon-bundle")
#     )
#     inbox = Inbox(bundle_path)
#     print(f"Total emails: {len(inbox.emails())}\n")


#     for email in list(inbox)[:5]:
#       res = classify_email(email)
#       print(
#         f"[{res['email_id']}] Category: {res['category']} | Process:"
#         f" {res['should_process']}"
#     )