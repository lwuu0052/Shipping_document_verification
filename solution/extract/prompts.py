"""Prompt templates for the SI/BL extraction LLM (Step 3).

Critical design points (Section 4 / Step 3):

  - SI and BL use the **same** field set, but they're called separately so
    the model cannot cross-contaminate values. A single combined prompt
    would let the model "fix" a smudged number on BL by copying it from SI,
    silently hiding the exact mismatch Compare exists to catch.

  - The model is instructed to return **only** JSON — no preamble, no
    markdown fence. We still strip a fence defensively in
    :mod:`extract.llm_client` because models frequently add one anyway.

  - Unknown fields must be filled with ``null``. Guessing, inferring, or
    copying values across documents is explicitly forbidden.
"""

from __future__ import annotations

from .schema import FIELDS

# A schema description embedded verbatim in the prompt. Kept in sync with
# schema.FIELDS by construction; do not hand-edit.
_FIELD_SCHEMA = """{
  "shipper":             string | null,
  "consignee":           string | null,
  "notify_party":        string | null,
  "port_of_loading":     string | null,
  "port_of_discharge":   string | null,
  "container_count":     string | null,
  "gross_weight_kg":     string | null
}"""


SYSTEM_PROMPT = """You are a shipping-document field extractor for an automated
SI-vs-BL comparison pipeline. You will receive the text of exactly one document
(either a Shipping Instruction or a Bill of Lading) and must return a single
JSON object with a fixed schema.

HARD RULES — violating any of these breaks the pipeline:

1. Output MUST be a single JSON object and NOTHING else. No prose, no
   markdown code fence, no explanation, no leading/trailing text.
2. Every field in the schema below MUST be present in your output. If a
   value cannot be found in the document, set that field to null. Do not
   omit the key.
3. NEVER guess, infer, or fill in a value that is not actually written in
   the document. A null is always preferable to a fabricated value — a
   fabricated value hides the exact kind of discrepancy the downstream
   comparison step exists to find.
4. Do NOT copy values across documents. You only ever see one document at
   a time; do not invoke "common defaults" or remembered values from
   similar-looking documents you may have processed before.
5. Copy strings verbatim from the document, including any UN/LOCODE and
   the original unit (kg / mt / ton). Do not convert units, do not
   translate, do not normalize casing — normalization happens downstream.
6. For `container_count`, return the value as written, for example:
   "1 x 40'HC" or "3 x 20'GP".
7. For `gross_weight_kg`, return the source string verbatim,
   for example "21,577 KG" or "138.000 MT".

SCHEMA:

{schema}

Return JSON only.""".format(schema=_FIELD_SCHEMA)


def user_prompt(doc_text: str, doc_type: str) -> str:
    """Build the user-side message for one extraction call.

    ``doc_type`` is 'SI' or 'BL' — passed in so we can tailor the hint
    about what the model is looking at, without combining two documents.
    """
    if doc_type == "SI":
        kind_hint = (
            "This is a SHIPPING INSTRUCTION (SI). Look for headers such as "
            "'Shipper', 'Consignee', 'Notify Party', 'Port of Loading', "
            "'Port of Discharge', 'No. of Containers', 'Gross Weight'."
        )

    elif doc_type == "BL":
        kind_hint = (
            "This is a BILL OF LADING (BL). Look for headers such as "
            "'Shipper', 'Consignee', 'Notify Party', 'Port of Loading', "
            "'Port of Discharge', 'Container Count', 'Gross Weight'."
        )
    else:
        kind_hint = (
            "Document type unknown; identify the fields best you can from "
            "the headers present."
        )

    return (
        f"{kind_hint}\n\n"
        f"DOCUMENT TEXT (verbatim, may include formatting artifacts):\n"
        f"----- BEGIN DOCUMENT -----\n{doc_text}\n----- END DOCUMENT -----\n\n"
        f"Return the JSON object now. Remember: only JSON, every field "
        f"present, null where missing, no guesses."
    )
