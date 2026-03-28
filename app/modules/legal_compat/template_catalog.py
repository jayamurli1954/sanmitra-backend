from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_FILE = Path(__file__).resolve().parent / "data" / "legacy_legal_templates.json"

# Minimal fallback if catalog file is missing/corrupt.
_FALLBACK_TEMPLATE_LIBRARY: list[dict[str, Any]] = [
    {
        "template_id": "legal_notice_payment_default",
        "name": "Legal Notice for Payment Default",
        "description": "Notice for delayed or defaulted payment under contract/invoice terms.",
        "category": "legal_notice",
        "is_premium": False,
        "tags": ["payment", "notice", "recovery"],
        "act": ["Indian Contract Act, 1872"],
        "court": [],
        "fields": [
            {"id": "sender_name", "label": "Sender Name", "required": True, "type": "text", "placeholder": "Advocate/Firm Name"},
            {"id": "recipient_name", "label": "Recipient Name", "required": True, "type": "text", "placeholder": "Defaulter Name"},
            {"id": "amount_due", "label": "Amount Due", "required": True, "type": "number", "placeholder": "50000"},
            {"id": "invoice_ref", "label": "Invoice/Reference", "required": False, "type": "text", "placeholder": "INV-2026-001"},
            {"id": "date", "label": "Date", "required": True, "type": "date", "placeholder": ""},
        ],
        "body": [
            "LEGAL NOTICE",
            "",
            "Date: {{date}}",
            "",
            "To,",
            "{{recipient_name}}",
            "",
            "Subject: Demand Notice for payment default",
            "",
            "Under instructions from my client {{sender_name}}, you are hereby called upon to pay INR {{amount_due}} against reference {{invoice_ref}} within 15 days of receipt of this notice.",
            "Failing compliance, my client will initiate appropriate legal proceedings at your risk as to costs and consequences.",
            "",
            "Authorized Signatory",
            "{{sender_name}}",
        ],
    }
]


def _normalize_template(item: dict[str, Any]) -> dict[str, Any]:
    template = dict(item)
    template_id = str(template.get("template_id") or "").strip()
    if not template_id:
        return {}

    fields = template.get("fields") if isinstance(template.get("fields"), list) else []
    normalized_fields: list[dict[str, Any]] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "").strip()
        if not field_id:
            continue
        normalized = {
            "id": field_id,
            "label": str(field.get("label") or field_id),
            "required": bool(field.get("required", False)),
            "type": str(field.get("type") or "text"),
            "placeholder": str(field.get("placeholder") or ""),
        }
        if isinstance(field.get("options"), list) and field.get("options"):
            normalized["options"] = [str(opt) for opt in field["options"]]
        if field.get("help_text"):
            normalized["help_text"] = str(field["help_text"])
        normalized_fields.append(normalized)

    body = template.get("body")
    if isinstance(body, str):
        body_lines = body.splitlines()
    elif isinstance(body, list):
        body_lines = [str(line) for line in body]
    else:
        body_lines = []

    template["template_id"] = template_id
    template["name"] = str(template.get("name") or template_id)
    template["description"] = str(template.get("description") or "")
    template["category"] = str(template.get("category") or "general")
    template["is_premium"] = bool(template.get("is_premium", False))
    template["tags"] = [str(tag) for tag in (template.get("tags") or [])]
    template["act"] = [str(act) for act in (template.get("act") or [])]
    template["court"] = [str(court) for court in (template.get("court") or [])]
    template["fields"] = normalized_fields
    template["body"] = body_lines
    return template


@lru_cache(maxsize=1)
def get_template_library() -> list[dict[str, Any]]:
    if _DATA_FILE.exists():
        try:
            data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                normalized = [_normalize_template(item) for item in data if isinstance(item, dict)]
                normalized = [item for item in normalized if item]
                if normalized:
                    return normalized
        except Exception:
            pass

    return [dict(item) for item in _FALLBACK_TEMPLATE_LIBRARY]
