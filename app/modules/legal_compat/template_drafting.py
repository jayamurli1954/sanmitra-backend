from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_REQUIRED_NOTICE_KEYS = [
    "sender_name",
    "recipient_name",
    "amount_due",
    "invoice_ref",
]


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_empty(value: Any) -> bool:
    return _to_text(value) == ""


def _normalize_fields(fields: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (fields or {}).items():
        normalized[str(key)] = _to_text(value)

    if not normalized.get("date"):
        normalized["date"] = datetime.now().date().isoformat()
    return normalized


def _looks_notice_template(template: dict[str, Any]) -> bool:
    category = _to_text(template.get("category")).lower()
    name = _to_text(template.get("name")).lower()
    template_id = _to_text(template.get("template_id")).lower()

    return (
        category in {"legal_notice", "legal_notices"}
        or "notice" in name
        or "notice" in template_id
    )


def _replace_placeholders(text: str, fields: dict[str, str]) -> str:
    rendered = text
    for key, value in fields.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def _extract_unresolved_placeholders(text: str) -> list[str]:
    unresolved: set[str] = set()
    for match in re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", text):
        unresolved.add(match)
    for match in re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", text):
        unresolved.add(match)
    return sorted(unresolved)


def _template_missing_required_fields(template: dict[str, Any], fields: dict[str, str]) -> list[dict[str, str]]:
    missing: list[dict[str, str]] = []
    for field in template.get("fields") or []:
        field_id = _to_text(field.get("id"))
        if not field_id:
            continue
        if bool(field.get("required", False)) and _is_empty(fields.get(field_id)):
            missing.append(
                {
                    "id": field_id,
                    "label": _to_text(field.get("label")) or field_id,
                }
            )
    return missing


def _first_non_empty(fields: dict[str, str], keys: list[str], fallback: str = "") -> str:
    for key in keys:
        value = _to_text(fields.get(key))
        if value:
            return value
    return fallback


def _safe_int(value: str, default: int) -> int:
    text = _to_text(value)
    if not text:
        return default
    digits = re.sub(r"[^0-9]", "", text)
    if not digits:
        return default
    try:
        return int(digits)
    except Exception:
        return default


def _amount_to_words_indian(n: int) -> str:
    if n == 0:
        return "Zero"

    ones = [
        "",
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
        "Six",
        "Seven",
        "Eight",
        "Nine",
    ]
    teens = [
        "Ten",
        "Eleven",
        "Twelve",
        "Thirteen",
        "Fourteen",
        "Fifteen",
        "Sixteen",
        "Seventeen",
        "Eighteen",
        "Nineteen",
    ]
    tens = [
        "",
        "",
        "Twenty",
        "Thirty",
        "Forty",
        "Fifty",
        "Sixty",
        "Seventy",
        "Eighty",
        "Ninety",
    ]

    def two_digits(x: int) -> str:
        if x < 10:
            return ones[x]
        if 10 <= x < 20:
            return teens[x - 10]
        t = x // 10
        o = x % 10
        return f"{tens[t]} {ones[o]}".strip()

    def three_digits(x: int) -> str:
        h = x // 100
        r = x % 100
        if h and r:
            return f"{ones[h]} Hundred {two_digits(r)}"
        if h:
            return f"{ones[h]} Hundred"
        return two_digits(r)

    crore = n // 10000000
    n %= 10000000
    lakh = n // 100000
    n %= 100000
    thousand = n // 1000
    n %= 1000
    hundred_part = n

    parts: list[str] = []
    if crore:
        parts.append(f"{two_digits(crore)} Crore")
    if lakh:
        parts.append(f"{two_digits(lakh)} Lakh")
    if thousand:
        parts.append(f"{two_digits(thousand)} Thousand")
    if hundred_part:
        parts.append(three_digits(hundred_part))

    return " ".join(part.strip() for part in parts if part.strip()).strip()


def _amount_display(fields: dict[str, str]) -> tuple[str, str]:
    raw = _first_non_empty(fields, ["amount_due", "amount", "claim_amount", "invoice_amount"], "")
    amount_value = _safe_int(raw, 0)
    if amount_value <= 0:
        return (raw or "[Amount]", "[Amount in words]")
    return (f"{amount_value:,}", _amount_to_words_indian(amount_value) + " only")


def _notice_legal_basis(template: dict[str, Any]) -> str:
    template_id = _to_text(template.get("template_id")).lower()
    name = _to_text(template.get("name")).lower()
    combined = f"{template_id} {name}"

    if "138" in combined or "cheque" in combined or "ni" in combined:
        return (
            "You are liable under Section 138 read with other applicable provisions of the Negotiable Instruments Act, 1881, "
            "subject to statutory timelines and compliance requirements."
        )
    if "gst" in combined:
        return (
            "Your non-payment/default attracts consequences under the contractual arrangement and applicable provisions of the CGST/SGST framework, "
            "including tax and compliance implications."
        )
    if "rent" in combined or "lease" in combined:
        return (
            "Your default constitutes breach of tenancy/lease obligations and entitles my client to proceed under applicable rent and civil law remedies."
        )
    return (
        "Your default constitutes breach of binding contractual and payment obligations, entitling my client to initiate appropriate civil and other lawful proceedings."
    )


def _render_firm_notice(template: dict[str, Any], fields: dict[str, str]) -> str:
    sender = _first_non_empty(fields, ["sender_name", "client_name", "complainant_name"], "[Sender Name]")
    sender_address = _first_non_empty(fields, ["sender_address", "client_address"], "")
    recipient = _first_non_empty(fields, ["recipient_name", "opposite_party", "accused_name"], "[Recipient Name]")
    recipient_address = _first_non_empty(fields, ["recipient_address", "opposite_party_address", "accused_address"], "")

    invoice_ref = _first_non_empty(fields, ["invoice_ref", "invoice_no", "reference_no", "cheque_no"], "[Reference]")
    invoice_date = _first_non_empty(fields, ["invoice_date", "cheque_date", "transaction_date"], "")
    due_date = _first_non_empty(fields, ["due_date", "payment_due_date"], "")
    date_text = _first_non_empty(fields, ["date"], datetime.now().date().isoformat())
    notice_days = _safe_int(_first_non_empty(fields, ["notice_period", "notice_period_days"], "15"), 15)
    jurisdiction = _first_non_empty(fields, ["jurisdiction", "court_city"], "[Jurisdiction]")
    facts = _first_non_empty(fields, ["facts", "description", "transaction_details", "default_details"], "")
    interest_rate = _first_non_empty(fields, ["interest_rate", "interest"], "")

    amount_figures, amount_words = _amount_display(fields)
    legal_basis = _notice_legal_basis(template)

    amount_line = f"INR {amount_figures} (Rupees {amount_words})"
    if amount_words.startswith("["):
        amount_line = f"INR {amount_figures}"

    interest_line = ""
    if interest_rate:
        interest_line = (
            f"6. My client shall also be entitled to claim contractual/statutory interest at {interest_rate}% per annum and all legal costs incurred for recovery."
        )

    facts_block = facts or (
        f"My client raised invoice/reference {invoice_ref}" + (f" dated {invoice_date}" if invoice_date else "") +
        " for goods/services duly provided. Despite repeated follow-ups, you have failed to clear the outstanding amount."
    )

    lines = [
        "WITHOUT PREJUDICE",
        "",
        "LEGAL NOTICE",
        "",
        f"Date: {date_text}",
        "",
        "To,",
        recipient,
    ]
    if recipient_address:
        lines.append(recipient_address)
    lines.extend(
        [
            "",
            f"Subject: Final demand notice for payment default against reference {invoice_ref}",
            "",
            f"Under instructions from and on behalf of my client {sender}" + (f", {sender_address}" if sender_address else "") + ", this notice is issued as under:",
            "",
            "1. " + facts_block,
            f"2. The outstanding amount legally due and payable by you is {amount_line}.",
        ]
    )
    if due_date:
        lines.append(f"3. The amount became due on {due_date} and remains unpaid till date.")
        lines.append("4. " + legal_basis)
    else:
        lines.append("3. " + legal_basis)
        lines.append("4. Your continued default is deliberate and prejudicial to my client's legal and commercial rights.")

    lines.extend(
        [
            f"5. You are hereby finally called upon to pay the above amount within {notice_days} days from receipt of this notice, failing which my client shall initiate appropriate civil/criminal and other lawful proceedings against you, entirely at your risk as to costs and consequences.",
        ]
    )
    if interest_line:
        lines.append(interest_line)

    lines.extend(
        [
            f"7. TAKE NOTICE that jurisdiction for all proceedings shall be at {jurisdiction}, without prejudice to any other forum available in law.",
            "8. This notice is issued without prejudice to all other rights, claims, and remedies available to my client in law and equity.",
            "",
            "You are advised to treat this as most urgent.",
            "",
            "Authorized Signatory",
            sender,
        ]
    )

    return "\n".join(lines).strip()


def _render_base_template(template: dict[str, Any], fields: dict[str, str]) -> str:
    body_lines = template.get("body") or []
    rendered = _replace_placeholders("\n".join(str(line) for line in body_lines), fields)
    return rendered.strip()


def _recommended_clauses(template: dict[str, Any], fields: dict[str, str]) -> list[str]:
    if not _looks_notice_template(template):
        return []

    recommendations: list[str] = []
    if _is_empty(fields.get("interest_rate")) and _is_empty(fields.get("interest")):
        recommendations.append("Interest clause with rate and accrual period")
    if _is_empty(fields.get("jurisdiction")):
        recommendations.append("Exclusive jurisdiction clause")
    if _is_empty(fields.get("mode_of_service")):
        recommendations.append("Service/dispatch proof clause (RPAD/Email/Courier)")
    recommendations.append("Without prejudice and reservation of rights clause")
    recommendations.append("Legal costs and consequences clause")
    return recommendations


def _follow_up_questions(
    template: dict[str, Any],
    fields: dict[str, str],
    missing_required: list[dict[str, str]],
    unresolved: list[str],
) -> list[str]:
    questions: list[str] = []

    for item in missing_required:
        questions.append(f"Please provide {item['label']} for a complete and enforceable draft.")

    for key in unresolved[:8]:
        questions.append(f"Please share the value for '{key}' to complete all template clauses.")

    if _looks_notice_template(template):
        if _is_empty(fields.get("recipient_address")):
            questions.append("What is the full service address of the notice recipient?")
        if _is_empty(fields.get("due_date")):
            questions.append("What was the exact due date/payment deadline under the invoice or agreement?")
        if _is_empty(fields.get("jurisdiction")):
            questions.append("Which city/forum should be mentioned as jurisdiction?")
        if _is_empty(fields.get("transaction_details")) and _is_empty(fields.get("facts")) and _is_empty(fields.get("description")):
            questions.append("Please provide 3-5 concise facts (transaction, default, reminders) to strengthen legal firmness.")
        if _is_empty(fields.get("supporting_documents")):
            questions.append("Which documents are available (invoice, delivery proof, emails, ledger, reminders)?")

    seen: set[str] = set()
    unique_questions: list[str] = []
    for q in questions:
        token = q.lower().strip()
        if token in seen:
            continue
        seen.add(token)
        unique_questions.append(q)

    return unique_questions[:12]


def render_template_document(*, template: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_fields(fields)
    missing_required = _template_missing_required_fields(template, normalized)

    if _looks_notice_template(template):
        rendered = _render_firm_notice(template, normalized)
    else:
        rendered = _render_base_template(template, normalized)

    unresolved = _extract_unresolved_placeholders(rendered)
    follow_up_questions = _follow_up_questions(template, normalized, missing_required, unresolved)

    # Notice drafts are considered filing-ready only when core fields are present and no unresolved placeholders remain.
    core_missing = []
    if _looks_notice_template(template):
        for key in _REQUIRED_NOTICE_KEYS:
            if _is_empty(normalized.get(key)):
                core_missing.append(key)

    needs_more_information = bool(missing_required or unresolved or core_missing)
    draft_status = "needs_more_information" if needs_more_information else "ready"

    firmness_score = 88 if _looks_notice_template(template) else 80
    if needs_more_information:
        firmness_score = max(65, firmness_score - 14)

    return {
        "rendered_document": rendered,
        "draft_status": draft_status,
        "missing_required_fields": missing_required,
        "unresolved_placeholders": unresolved,
        "follow_up_questions": follow_up_questions,
        "recommended_clauses": _recommended_clauses(template, normalized),
        "firmness_score": firmness_score,
    }



def _normalize_parties(parties: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if not isinstance(parties, dict):
        return out
    for key, value in parties.items():
        out[str(key).strip().lower()] = _to_text(value)
    return out


def _find_party(parties: dict[str, str], candidates: list[str], fallback: str = "") -> str:
    for key in candidates:
        value = _to_text(parties.get(key.lower()))
        if value:
            return value
    return fallback


def _build_non_notice_draft(
    *,
    document_type: str,
    facts: str,
    parties: dict[str, str],
    grounds: list[str],
    prayer: str,
) -> str:
    sender = _find_party(parties, ["sender", "client", "complainant", "petitioner"], "[Client Name]")
    recipient = _find_party(parties, ["recipient", "opposite_party", "respondent", "accused"], "[Opposite Party Name]")
    sender_address = _find_party(parties, ["sender_address", "client_address", "petitioner_address"])
    recipient_address = _find_party(parties, ["recipient_address", "opposite_party_address", "respondent_address"])
    date_text = datetime.now().date().isoformat()
    doc_label = (document_type or "document").strip().title()

    lines = [
        "WITHOUT PREJUDICE",
        "",
        f"DRAFT {doc_label.upper()}",
        "",
        f"Date: {date_text}",
        "",
        "1. Parties:",
        f"- Client/Applicant: {sender}" + (f", {sender_address}" if sender_address else ""),
        f"- Opposite Party/Respondent: {recipient}" + (f", {recipient_address}" if recipient_address else ""),
        "",
        "2. Factual Matrix:",
        facts or "[Please provide full factual chronology with dates and documentary references.]",
        "",
        "3. Legal Grounds:",
    ]
    if grounds:
        for idx, item in enumerate(grounds, start=1):
            lines.append(f"{idx}. {item}")
    else:
        lines.append("1. [Please provide statutory and contractual grounds.]")

    lines.extend(
        [
            "",
            "4. Relief/Prayer:",
            prayer or "[Please provide specific and quantifiable relief sought.]",
            "",
            "5. Reservation of Rights:",
            "This draft is prepared without prejudice to all rights, claims, and remedies available in law and equity.",
        ]
    )
    return "\n".join(lines).strip()


def _follow_up_for_document(
    *,
    document_type: str,
    facts: str,
    parties: dict[str, str],
    grounds: list[str],
    prayer: str,
    notice_specific: dict[str, str],
) -> list[str]:
    questions: list[str] = []

    sender = _find_party(parties, ["sender", "client", "complainant", "petitioner"])
    recipient = _find_party(parties, ["recipient", "opposite_party", "respondent", "accused"])
    recipient_address = _find_party(parties, ["recipient_address", "opposite_party_address", "respondent_address"])

    if not sender:
        questions.append("Who is the exact client/issuer name to be shown in the draft?")
    if not recipient:
        questions.append("Who is the exact notice recipient/opposite party?")
    if not recipient_address:
        questions.append("What is the complete service address of the recipient for enforceable notice dispatch?")

    if not facts or len(facts.strip()) < 120:
        questions.append("Please provide a full factual chronology with dates, invoices/communications, and default events.")
    if not grounds or len(grounds) < 2:
        questions.append("Please provide at least 2-3 specific legal grounds/statutory provisions supporting your claim.")
    if not prayer or len(prayer.strip()) < 30:
        questions.append("Please specify exact relief sought (amount, timeline, interest, and costs) in measurable terms.")

    if "notice" in document_type:
        if _is_empty(notice_specific.get("amount_due")):
            questions.append("What is the exact outstanding amount (in INR) to be demanded?")
        if _is_empty(notice_specific.get("invoice_ref")):
            questions.append("Please provide invoice/reference number and date for a legally strong demand notice.")
        if _is_empty(notice_specific.get("due_date")):
            questions.append("What was the contractual/statutory due date for payment?")
        if _is_empty(notice_specific.get("jurisdiction")):
            questions.append("Which city/forum should be specified for jurisdiction?")

    seen: set[str] = set()
    out: list[str] = []
    for item in questions:
        token = item.lower().strip()
        if token in seen:
            continue
        seen.add(token)
        out.append(item)
    return out[:12]


def render_guided_document_draft(
    *,
    document_type: str,
    facts: str,
    parties: Any,
    legal_grounds: list[str],
    prayer: str,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    doc_type = _to_text(document_type).lower() or "legal notice"
    facts_text = _to_text(facts)
    prayer_text = _to_text(prayer)
    parties_map = _normalize_parties(parties)
    grounds = [_to_text(g) for g in (legal_grounds or []) if _to_text(g)]
    extras = {str(k): _to_text(v) for k, v in (extra_fields or {}).items()}

    if "notice" in doc_type:
        notice_fields = {
            "sender_name": _find_party(parties_map, ["sender", "client", "complainant"], ""),
            "sender_address": _find_party(parties_map, ["sender_address", "client_address"], ""),
            "recipient_name": _find_party(parties_map, ["recipient", "opposite_party", "accused"], ""),
            "recipient_address": _find_party(parties_map, ["recipient_address", "opposite_party_address", "accused_address"], ""),
            "amount_due": _to_text(extras.get("amount_due")),
            "invoice_ref": _to_text(extras.get("invoice_ref")),
            "invoice_date": _to_text(extras.get("invoice_date")),
            "due_date": _to_text(extras.get("due_date")),
            "notice_period": _to_text(extras.get("notice_period")) or "15",
            "interest_rate": _to_text(extras.get("interest_rate")),
            "jurisdiction": _to_text(extras.get("jurisdiction")),
            "transaction_details": facts_text,
            "date": _to_text(extras.get("date")) or datetime.now().date().isoformat(),
        }
        notice_template = {
            "category": "legal_notices",
            "name": "Guided Legal Notice Draft",
            "template_id": "guided_legal_notice",
            "fields": [
                {"id": "sender_name", "label": "Sender Name", "required": True},
                {"id": "recipient_name", "label": "Recipient Name", "required": True},
                {"id": "amount_due", "label": "Amount Due", "required": True},
                {"id": "invoice_ref", "label": "Invoice/Reference", "required": True},
            ],
        }
        notice_draft = render_template_document(template=notice_template, fields=notice_fields)
        rendered = notice_draft["rendered_document"]
        if grounds:
            rendered += "\n\nLegal Grounds Asserted:\n" + "\n".join(f"- {g}" for g in grounds)
        if prayer_text:
            rendered += "\n\nRelief Sought:\n" + prayer_text
        follow_up = _follow_up_for_document(
            document_type=doc_type,
            facts=facts_text,
            parties=parties_map,
            grounds=grounds,
            prayer=prayer_text,
            notice_specific=notice_fields,
        )
        needs_more = notice_draft["draft_status"] != "ready" or bool(follow_up)
        return {
            "drafted_document": rendered.strip(),
            "draft_status": "needs_more_information" if needs_more else "ready",
            "follow_up_questions": follow_up or notice_draft["follow_up_questions"],
            "recommended_clauses": notice_draft["recommended_clauses"],
            "firmness_score": notice_draft["firmness_score"],
        }

    rendered = _build_non_notice_draft(
        document_type=doc_type,
        facts=facts_text,
        parties=parties_map,
        grounds=grounds,
        prayer=prayer_text,
    )
    follow_up = _follow_up_for_document(
        document_type=doc_type,
        facts=facts_text,
        parties=parties_map,
        grounds=grounds,
        prayer=prayer_text,
        notice_specific={},
    )
    needs_more = bool(follow_up)
    recommendations = [
        "Attach chronology table with date-wise events and evidence references.",
        "Add jurisdiction and limitation paragraph before finalization.",
        "Cross-check relief language against documentary record.",
    ]
    return {
        "drafted_document": rendered,
        "draft_status": "needs_more_information" if needs_more else "ready",
        "follow_up_questions": follow_up,
        "recommended_clauses": recommendations,
        "firmness_score": 72 if needs_more else 84,
    }
