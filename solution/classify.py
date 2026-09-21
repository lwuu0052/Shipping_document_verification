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
        "Analyze the email subject, body, and attachment filenames to determine "
        "the sender's primary intent.\n\n"

        "Categories:\n\n"

        "- document_comparison_request: Sender wants to compare, cross-check, or "
        "verify Shipping Instructions (SI) against a Bill of Lading (BL). "
        "This ALSO includes emails that REQUEST a draft BL to be sent for checking "
        "(e.g. 'please assist to send the draft BL for <booking> for checking', "
        "'please compare the SI and draft BL') even when no attachments are present "
        "— the intent is still about BL verification/review.\n"
        "  CRITICAL: An email that SUBMITS a Shipping Instruction in the body "
        "(e.g. 'Please find Shipping instruction for <booking>') is new_si_request, "
        "NOT document_comparison_request — even though it mentions shipping docs.\n"
        "  CRITICAL: If the body contains a complete SI submission pattern like "
        "'Please find Shipping instruction for <booking>' followed by structured "
        "fields (POL/POD/Shipper/Consignee/etc.) with NO attachments — this is "
        "new_si_request, regardless of subject keywords like 'SI' or 'CUST SI'.\n\n"

        "- new_si_request: Sender is FORMALLY submitting a new Shipping Instruction, "
        "or providing complete cargo/container details to issue one.\n"
        "  CRITICAL: An email body starting with 'Please find Shipping instruction "
        "for <booking>' and listing POL/POD/Shipper/Consignee fields (with or "
        "without attachments) is ALWAYS new_si_request — do NOT reclassify as "
        "document_comparison_request based on subject keywords or priority rules.\n"
        "  CRITICAL: Asking WHEN an SI will be ready, chasing status, general cargo "
        "updates, or casual logistics questions are general_message, NOT new_si_request.\n\n"

        "- invoice_query: Inquiries regarding billing, freight charges, debit/credit "
        "notes, payment status, or invoices.\n\n"

        "- spam: Malicious phishing, suspicious links, or non-logistics commercial "
        "ads (SEO services, casino, loans).\n"
        "  CRITICAL: Vessel schedule updates, port delay advisories, holiday notices, "
        "and system-generated logistics newsletters are general_message, NOT spam.\n\n"

        "- general_message: Catch-all. Status updates, vessel schedules, container "
        "tracking, ETA/ETD queries, automated notices, greetings, follow-ups, or "
        "anything ambiguous.\n\n"

        "ATTACHMENT RULES (filenames override body wording):\n"
        "- Both a *_SI.* and a *_BL.* file are attached -> document_comparison_request\n"
        "- Only a *_SI.* file, and the sender is submitting it -> new_si_request\n"
        "- No attachments but body mentions 'draft BL' or asks to compare/verify "
        "BL -> still document_comparison_request (the request itself is BL-related)\n\n"

        "PRIORITY RULE:\n"
        "If an email matches multiple categories, pick the highest in this hierarchy:\n"
        "document_comparison_request > new_si_request > invoice_query > spam > general_message\n"
        "  OVERRIDE: If the email body explicitly submits an SI (e.g. 'Please find "
        "Shipping instruction for <booking>' followed by POL/POD/Shipper/Consignee "
        "fields), it is ALWAYS new_si_request — this overrides the priority rule above.\n\n"

        "TIE-BREAKER:\n"
        "When torn between document_comparison_request and general_message, choose "
        "document_comparison_request. A false positive is cheap (the comparison simply "
        "finds no defect), but a false negative means a document error is never caught."
    ),
    (
        "human",
        "Email Subject: {subject}\n"
        "Email Body:\n{body}\n"
        "Attachment Names: {attachments}\n"
    ),
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
    body = (email.get("body") or "")[:3000]
    raw_attachments = email.get("attachments") or []
    attachment_names = [str(att) for att in raw_attachments]
    return subject, body, attachment_names


# ============================================================
# Decision & category classification (Main function)
# ============================================================
def classify_email(email: dict):
  email_id = email.get("id") or email.get("email_id")
  subject, body, names = parse_email_metadata(email)

  try:
    raw_output = get_classification_chain().invoke({
        "subject": subject,
        "body": body,
        "attachments": ", ".join(names) if names else "None",
    })

    if isinstance(raw_output, EmailClassificationResult):
      predicted_category = raw_output.category
    elif isinstance(raw_output, dict):
      predicted_category = raw_output.get("category", CATEGORY_GENERAL)
    else:
      data = raw_output.model_dump()
      predicted_category = data.get("category", CATEGORY_GENERAL)

  except Exception:
    return {
        "email_id": email_id,
        "category": CATEGORY_GENERAL,
        "should_process": False,
    }

  CATEGORY_MAP = {
      "document_comparison_request": CATEGORY_BL_COMPARISON,
      "new_si_request":              CATEGORY_SI_REQUEST,
      "invoice_query":               CATEGORY_INVOICE_QUERY,
      "general_message":             CATEGORY_GENERAL,
      "spam":                        CATEGORY_SPAM,}
  final_category = CATEGORY_MAP.get(predicted_category, CATEGORY_GENERAL)

  return {
      "email_id": email_id,
      "category": final_category,
      "should_process": (final_category == CATEGORY_BL_COMPARISON),
  }