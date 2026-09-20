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
  "booking_no":          string | null,   // booking ref / OC number, e.g. "5RSG-00133"
  "bl_no":                string | null,   // bill of lading number, e.g. "MEDUUD104332"
  "consignee":            string | null,   // consignee name exactly as printed
  "vessel":               string | null,   // vessel name
  "voyage":               string | voyage number as printed
  "port_of_loading":      string | null,   // load port, keep UN/LOCODE if present
  "port_of_discharge":    string | null,   // discharge port, keep UN/LOCODE if present
  "container_no":          string[],       // list of container numbers in source order; [] if none
  "description_of_goods":  string | null,   // commodity description
  "package_count":        string | null,   // numeric package count as it appears, e.g. "880 CARTONS"
  "gross_weight_kg":      string | null    // gross weight with unit as it appears, e.g. "21,577 KG"
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
6. `container_no` is always a JSON array. If the document lists one
   container, return an array of length 1. If none is shown, return [].
7. For `package_count` and `gross_weight_kg`, return the source string
   verbatim (e.g. "880 CARTONS", "21,577 KG", "138.000 MT") — the
   downstream normalizer will parse numbers and convert units. Returning
   a pre-converted number here loses information about how it was written.

SCHEMA:

{schema}

Return JSON only.""".format(schema=_FIELD_SCHEMA)


def user_prompt(doc_text: str, doc_type: str) -> str:
    """Build the user-side message for one extraction call.

    ``doc_type`` is 'SI' or 'BL' — passed in so we can tailor the hint
    about what the model is looking at, without combining two documents.
    """
    doc_type = doc_type.upper()
    if doc_type == "SI":
        kind_hint = (
            "This is a SHIPPING INSTRUCTION (SI). Look for headers such as "
            "'Shipping Instruction', 'Shipper/Exporter', 'OC No.', "
            "'Booking Ref', 'Voy. No.'."
        )
    elif doc_type == "BL":
        kind_hint = (
            "This is a BILL OF LADING (BL), likely a draft. Look for headers "
            "such as 'Bill of Lading', 'B/L No.', 'Shipped on Board', "
            "'Consignee', 'Notify'."
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
