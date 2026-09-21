from functools import lru_cache
import re
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
        "Analyze the email subject, body, and attachment filenames and determine "
        "the sender's PRIMARY intent.\n\n"

        "Categories:\n"
        "- document_comparison_request: The sender explicitly wants to compare, "
        "verify, cross-check, review, or check Shipping Instructions (SI) against "
        "a Bill of Lading (BL).\n"

        "- new_si_request: The sender explicitly asks to create, issue, submit, "
        "prepare, or process a NEW Shipping Instruction. Mere mention of an SI "
        "does NOT make the email a new_si_request.\n"

        "- invoice_query: The sender asks about invoices, billing, freight charges, "
        "payments, payment status, or related financial matters.\n"

        "- spam: Clearly unsolicited advertising, phishing, scams, mass marketing, "
        "or messages unrelated to legitimate shipping/logistics business.\n"

        "- general_message: Normal shipping/logistics communication that does not "
        "clearly fit the categories above. This includes acknowledgements, status "
        "updates, general questions, booking/shipment coordination, greetings, "
        "requests for information, or vague messages without a clear specialized "
        "action.\n\n"

        "Important decision rules:\n"
        "- Determine intent from the full email, not the subject alone.\n"
        "- The subject may be vague, outdated, or misleading.\n"
        "- If the subject conflicts with an explicit request in the body, "
        "prioritize the body.\n"
        "- Attachment filenames are supporting evidence only. Do not classify an "
        "email solely because a filename contains SI, BL, invoice, or another keyword.\n"
        "- A message is new_si_request ONLY when creating, issuing, submitting, "
        "preparing, or processing a new SI is clearly requested.\n"
        "- Simply discussing, mentioning, forwarding, acknowledging, or asking "
        "about an SI is not enough for new_si_request.\n"
        "- A message is spam ONLY when there is clear evidence that it is "
        "unsolicited, promotional, phishing, scam, or irrelevant.\n"
        "- A strange, short, vague, or poorly written legitimate business email "
        "should NOT automatically be classified as spam.\n"
        "- If an email is normal shipping/logistics communication and no other "
        "category clearly applies, classify it as general_message.\n"
        "- When uncertain between general_message and another category, choose "
        "general_message unless the specialized intent is explicit.\n"
        "- Classify as document_comparison_request when the sender asks to compare, "
        "verify, cross-check, review, check, confirm, or identify corrections in a "
        "draft BL / Bill of Lading using shipping instructions or shipment details.\n"
        "- The email does NOT need to explicitly mention both 'SI' and 'BL'. "
        "For example, 'please check the draft BL', 'please review the draft BL', "
        "or 'please confirm the draft BL details' are document_comparison_request "
        "when the intent is to validate the BL for correctness.\n"
        "- Mere mention, forwarding, or status discussion of a BL without asking "
        "for validation is still general_message.\n\n"

        "Examples:\n"
        "- 'Please advise shipment status.' -> general_message\n"
        "- 'Noted with thanks.' -> general_message\n"
        "- 'Please confirm the booking details.' -> general_message\n"
        "- 'Can you send an update on this shipment?' -> general_message\n"
        "- 'Please prepare a new SI for this booking.' -> new_si_request\n"
        "- 'Attached is our SI for processing.' -> new_si_request\n"
        "- 'Please compare the SI with the draft BL.' -> document_comparison_request\n"
        "- 'Please advise why this invoice amount is incorrect.' -> invoice_query\n"
        "- 'Buy our marketing package today!' -> spam"
        "- 'Please check the draft BL and advise corrections.' -> document_comparison_request\n"
        "- 'Please review the attached Bill of Lading for correctness.' -> document_comparison_request\n"
        "- 'Please confirm the draft BL details.' -> document_comparison_request\n"
        "- 'Draft BL received, thank you.' -> general_message\n"
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




def _rule_category(email: dict) -> str | None:
    """High-confidence rules for the recurring SDOC inbox patterns.

    Rules run before the LLM. Unknown / ambiguous emails fall back to the LLM.
    """
    subject, body, _ = parse_email_metadata(email)
    sender = (email.get("from") or "").lower()
    subj = subject.lower()
    body_l = body.lower()
    text = subj + "\n" + body_l

    spam_domains = (
        "prize-claims.info", "parcel-track.co", "webmail-verify.co",
        "logistics-deals.biz", "crypto-invest.net", "secure-mailbox.org",
    )
    spam_terms = (
        "claim your $1,000", "unpaid customs fee of $2.99",
        "mailbox has exceeded", "90% off",
        "bank officer with an urgent business proposal",
        "won a brand new iphone", "undelivered messages",
        "bitcoin investment opportunity", "hot singles",
        "avoid suspension", "one weird trick",
    )
    if any(d in sender for d in spam_domains) or any(x in text for x in spam_terms):
        return CATEGORY_SPAM

    # Internal broadcasts / bots can mention SI or BL without asking for a
    # new SI or a document comparison.
    general_senders = {
        "documentation@aprilasia.com", "operations@aprilasia.com",
        "rpa.bot@aprilasia.com", "hr@aprilasia.com",
        "noreply@aprilasia.com",
    }
    general_terms = (
        "update summary", "daily berthing report", "_reminder_paper",
        "submit si & aed", "_rpa_", "hss sd billing process completed",
        "list of outstanding bl", "pending bl release",
        "welcoming the new year", "time off request", "miss connection",
        "delivery planning", "no action required",
    )
    if sender in general_senders or any(x in text for x in general_terms):
        return CATEGORY_GENERAL

    # Comparison must be an explicit validation action. This deliberately does
    # not fire on SI-request wording such as "revert with draft BL once available".
    comparison_terms = (
        "to confirm docs", "request bl draft",
        "please assist to send the draft bl",
        "attached are the si and draft bl",
        "attached the shipping instruction and the draft bill of lading",
        "attached si and draft bl", "check the draft bl against",
        "compare the si and draft bl", "compare the si with",
        "verify the bl matches", "kindly confirm the bl is in order",
        "please check the details and confirm", "revert with any discrepancy",
        "draft bl no.",
    )
    if any(x in text for x in comparison_terms):
        return CATEGORY_BL_COMPARISON
    if ("draft bl" in text or "bill of lading" in text) and re.search(
        r"\b(compare|check|checking|verify|confirm|review|cross-check|cross check)\b",
        body_l,
    ):
        return CATEGORY_BL_COMPARISON

    si_terms = (
        "request si", "cust si", "si needed",
        "please find shipping instruction for",
        "attached is our si for processing", "prepare a new si",
        "create a new si", "issue a new si",
    )
    if any(x in text for x in si_terms) or re.search(r"(^|\bre[_ :\-]*)si\s*-", subj):
        return CATEGORY_SI_REQUEST

    invoice_terms = (
        "billing", "cancel invoice", "invoice ", "local charges",
        "local charge", "d & d", "d&d", "detention charges",
        "total freight", "missing gr", "release payment",
        "payment status", "thc",
    )
    if any(x in text for x in invoice_terms):
        return CATEGORY_INVOICE_QUERY

    return None


# ============================================================
# Decision & category classification (Main function)
# ============================================================
def classify_email(email: dict):
    subject, body, names = parse_email_metadata(email)

    # Step 1: deterministic high-confidence rules.
    rule_category = _rule_category(email)
    if rule_category is not None:
        return {
            "email_id": email.get("id") or email.get("email_id"),
            "category": rule_category,
            "should_process": (rule_category == CATEGORY_BL_COMPARISON),
        }

    # Step 2: LLM fallback for unfamiliar / ambiguous mail.
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