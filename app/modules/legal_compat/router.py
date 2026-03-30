from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
import re
import textwrap
from urllib.parse import quote_plus, urlparse
import xml.etree.ElementTree as ET

import httpx
from io import BytesIO
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.permissions.rbac import Role, require_roles
from app.core.tenants.context import resolve_app_key, resolve_tenant_id
from app.db.mongo import get_collection
from app.modules.legal_compat.service import build_hybrid_legal_response, list_sync_queue
from app.modules.legal_compat.template_catalog import get_template_library
from app.modules.legal_compat.template_drafting import render_guided_document_draft, render_template_document
from app.modules.legal_compat.sync_worker import run_legal_sync_once
from app.modules.legal_compat.official_form_bank import (
    OfficialFormValidationError,
    get_official_form,
    get_official_form_upload_guidelines,
    list_official_forms,
    register_official_form,
    render_official_form_pdf,
)
from app.modules.rag.schemas import RagLegalFilter, RagQueryRequest
from app.modules.rag.service import query_knowledge

router = APIRouter(tags=["legal-compat"])

_DEFAULT_TENANT_ID = "seed-tenant-1"
_DEFAULT_APP_KEY = "legalmitra"


class LegacyLegalResearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    query_type: str = Field(default="research", max_length=40)


class LegacyCaseSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    court: str | None = None
    year: int | None = None


class LegacyStatuteSearchRequest(BaseModel):
    act_name: str = Field(min_length=2, max_length=240)
    section: str | None = Field(default=None, max_length=120)


class LegacyTemplateRenderRequest(BaseModel):
    template_id: str = Field(min_length=2, max_length=120)
    fields: dict[str, Any] = Field(default_factory=dict)
    format: str = Field(default="text", max_length=20)



class OfficialFormRenderRequest(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)

def _resolve_compat_tenant_id(x_tenant_id: str | None) -> str:
    tenant_id = (x_tenant_id or "").strip()
    return tenant_id or _DEFAULT_TENANT_ID


def _resolve_compat_app_key(x_app_key: str | None) -> str:
    return resolve_app_key((x_app_key or _DEFAULT_APP_KEY).strip())


def _static_case_items() -> list[dict[str, Any]]:
    return [
        {
            "title": "Recent Major Judgments",
            "court": "Supreme Court / High Courts",
            "year": 2026,
            "summary": "Latest important judgments will appear here. Click Refresh to load updates.",
            "query": "What are the latest major judgments from Supreme Court and High Courts?",
            "url": "",
        }
    ]


def _static_news_items() -> list[dict[str, Any]]:
    now_year = datetime.now().year
    return [
        {
            "title": "Latest Legal Updates",
            "source": "SanMitra Legal Desk",
            "date": str(now_year),
            "summary": "Latest legal news and regulatory updates will appear here. Click Refresh to load updates.",
            "query": "Summarize important legal and compliance updates in India.",
            "url": "",
        }
    ]


def _safe_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.year

    text = str(value).strip()
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _safe_iso_date(value: Any) -> str:
    if value is None:
        return str(datetime.now().date())
    if isinstance(value, datetime):
        return str(value.date())

    text = str(value).strip()
    if len(text) >= 10:
        return text[:10]
    return text or str(datetime.now().date())


def _safe_source_label(doc: dict[str, Any]) -> str:
    metadata = dict(doc.get("metadata") or {})
    source = str(metadata.get("source") or "").strip()
    if source:
        return source

    uri = str(doc.get("source_uri") or "").strip()
    if uri:
        try:
            host = urlparse(uri).netloc.strip()
            if host:
                return host
        except Exception:
            pass

    return "Legal Source"


async def _collect_docs(docs_collection, query_filter: dict[str, Any], limit: int = 10) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = docs_collection.find(query_filter).sort("created_at", -1).limit(limit)
    async for doc in cursor:
        items.append(doc)
    return items



def _render_text_as_pdf(text: str) -> BytesIO:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    page_width, page_height = A4

    left_margin = 50
    right_margin = 50
    top_margin = 50
    bottom_margin = 50
    line_height = 14

    usable_width = max(200, int(page_width - left_margin - right_margin))
    approx_char_width = 6.2
    wrap_width = max(40, int(usable_width / approx_char_width))

    y = page_height - top_margin
    pdf.setFont("Helvetica", 11)

    lines = (text or "").splitlines() or [""]
    for raw_line in lines:
        wrapped_lines = textwrap.wrap(
            raw_line,
            width=wrap_width,
            break_long_words=False,
            replace_whitespace=False,
        ) if raw_line else [""]

        for line in wrapped_lines:
            if y < bottom_margin:
                pdf.showPage()
                pdf.setFont("Helvetica", 11)
                y = page_height - top_margin
            pdf.drawString(left_margin, y, line)
            y -= line_height

    pdf.save()
    buffer.seek(0)
    return buffer


def _strip_html(value: str) -> str:
    if not value:
        return ""
    no_tags = re.sub(r"<[^>]+>", " ", value)
    compact = re.sub(r"\s+", " ", no_tags).strip()
    return unescape(compact)


def _rss_item_text(item: ET.Element, tag: str) -> str:
    direct = item.findtext(tag)
    if direct:
        return direct.strip()
    wildcard = item.findtext(f"{{*}}{tag}")
    return (wildcard or "").strip()


def _parse_pub_date(value: str) -> tuple[str, int]:
    if not value:
        now = datetime.now()
        return now.date().isoformat(), now.year
    try:
        dt = parsedate_to_datetime(value)
        if dt is None:
            raise ValueError('invalid date')
        return dt.date().isoformat(), dt.year
    except Exception:
        year = _safe_year(value) or datetime.now().year
        return str(value)[:10] if value else datetime.now().date().isoformat(), year


async def _fetch_google_news_items(query: str, limit: int = 10) -> list[dict[str, Any]]:
    feed_url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
    )
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(feed_url, headers={"User-Agent": "Mozilla/5.0"})
        if response.status_code >= 400 or not response.text.strip():
            return []

        root = ET.fromstring(response.text)
        items = root.findall("./channel/item")
        out: list[dict[str, Any]] = []
        seen: set[str] = set()

        for item in items:
            title = _rss_item_text(item, "title")
            link = _rss_item_text(item, "link")
            source = _rss_item_text(item, "source")
            pub_raw = _rss_item_text(item, "pubDate")
            desc = _rss_item_text(item, "description")
            date_text, year = _parse_pub_date(pub_raw)

            title_clean = title.strip()
            if " - " in title_clean:
                left, right = title_clean.rsplit(" - ", 1)
                if len(right) <= 40:
                    title_clean = left.strip()

            if not title_clean or title_clean.lower() in seen:
                continue
            seen.add(title_clean.lower())

            out.append(
                {
                    "title": title_clean,
                    "url": link,
                    "source": source or "Google News",
                    "date": date_text,
                    "year": year,
                    "summary": _strip_html(desc)[:220] if desc else "",
                }
            )
            if len(out) >= limit:
                break

        return out
    except Exception:
        return []


async def _fetch_web_major_cases(limit: int = 10) -> list[dict[str, Any]]:
    queries = [
        "Supreme Court India latest judgment order",
        "High Court India latest judgment order",
        "India constitutional bench judgment latest",
    ]

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in queries:
        items = await _fetch_google_news_items(query, limit=8)
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue

            normalized = title.lower()
            if normalized in seen:
                continue
            seen.add(normalized)

            lower = normalized
            if not any(k in lower for k in ["judgment", "order", "verdict", "bench", "court"]):
                continue

            court = "Supreme Court" if "supreme court" in lower else "High Court"
            merged.append(
                {
                    "title": title,
                    "court": court,
                    "year": int(item.get("year") or datetime.now().year),
                    "summary": str(item.get("summary") or f"Web update from {item.get('source') or 'Google News'}")[:220],
                    "query": title,
                    "url": str(item.get("url") or ""),
                }
            )
            if len(merged) >= limit:
                return merged

    return merged


async def _fetch_web_legal_news(limit: int = 10) -> list[dict[str, Any]]:
    queries = [
        "India legal news latest law and compliance updates",
        "India legal regulatory update notification circular",
    ]

    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    for query in queries:
        items = await _fetch_google_news_items(query, limit=10)
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue

            normalized = title.lower()
            if normalized in seen:
                continue
            seen.add(normalized)

            merged.append(
                {
                    "title": title,
                    "source": str(item.get("source") or "Google News"),
                    "date": str(item.get("date") or datetime.now().date().isoformat()),
                    "summary": str(item.get("summary") or "Latest legal and compliance update")[:220],
                    "query": title,
                    "url": str(item.get("url") or ""),
                }
            )
            if len(merged) >= limit:
                return merged

    return merged


def _merge_case_items(*sources: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for items in sources:
        for item in items or []:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            court = str(item.get("court") or "Supreme Court / High Court").strip()
            year = int(item.get("year") or datetime.now().year)
            key = f"{title.lower()}|{court.lower()}|{year}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "title": title,
                    "court": court,
                    "year": year,
                    "summary": str(item.get("summary") or "Latest major judgment update")[:220],
                    "query": str(item.get("query") or title),
                    "url": str(item.get("url") or ""),
                }
            )
            if len(merged) >= limit:
                return merged
    return merged


def _merge_news_items(*sources: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for items in sources:
        for item in items or []:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            date_text = str(item.get("date") or datetime.now().date().isoformat())
            key = f"{title.lower()}|{date_text}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "title": title,
                    "source": str(item.get("source") or "Legal Source"),
                    "date": date_text,
                    "summary": str(item.get("summary") or "Latest legal and compliance update")[:220],
                    "query": str(item.get("query") or title),
                    "url": str(item.get("url") or ""),
                }
            )
            if len(merged) >= limit:
                return merged
    return merged


_STATIC_TEMPLATE_LIBRARY: list[dict[str, Any]] = get_template_library()
_OFFICIAL_TEMPLATE_PREFIX = "official_form::"


def _is_official_template_id(template_id: str) -> bool:
    return (template_id or "").startswith(_OFFICIAL_TEMPLATE_PREFIX)


def _extract_official_form_id(template_id: str) -> str:
    if not _is_official_template_id(template_id):
        return ""
    return template_id.split("::", 1)[1].strip()


def _to_field_id(label: str) -> str:
    field_id = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")
    return field_id[:64]


def _field_spec(
    field_id: str,
    label: str,
    *,
    required: bool = False,
    field_type: str = "text",
    placeholder: str = "",
    section: str = "",
    options: list[str] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": field_id,
        "label": label,
        "required": required,
        "type": field_type,
        "placeholder": placeholder,
    }
    if section:
        out["section"] = section
    if options:
        out["options"] = options
    return out


def _is_gst_reg01_form(item: dict[str, Any]) -> bool:
    combined = " ".join(
        [
            str(item.get("form_name") or ""),
            str(item.get("form_code") or ""),
            str(item.get("purpose") or ""),
            str(item.get("department") or ""),
        ]
    ).lower()
    return ("gst" in combined) and any(token in combined for token in ["reg-01", "reg 01", "registration"])


def _gst_reg01_fields() -> list[dict[str, Any]]:
    yes_no = ["Yes", "No"]
    return [
        _field_spec("legal_name", "Legal Name (as mentioned in PAN)", required=True, section="Applicant Profile"),
        _field_spec("pan_identity_number", "PAN / Identity Number", required=True, section="Applicant Profile"),
        _field_spec(
            "constitution_of_business",
            "Constitution of Business",
            required=True,
            field_type="select",
            options=["Proprietorship", "Partnership", "Company", "LLP", "Trust", "Society", "Other"],
            section="Applicant Profile",
        ),
        _field_spec(
            "category_of_registered_person",
            "Category of Registered Person",
            required=True,
            field_type="select",
            options=["Regular Taxpayer", "SEZ Unit", "SEZ Developer", "Casual Taxable Person", "Composition Dealer", "Other"],
            section="Applicant Profile",
        ),
        _field_spec("option_for_composition", "Option for Composition", field_type="select", options=yes_no, section="Applicant Profile"),
        _field_spec("composition_declaration", "Composition Declaration", field_type="textarea", section="Applicant Profile"),
        _field_spec("date_of_commencement_of_business", "Date of commencement of business", field_type="date", section="Applicant Profile"),
        _field_spec("date_liability_to_register", "Date on which liability to register arises", field_type="date", section="Applicant Profile"),
        _field_spec("jurisdiction_state", "Jurisdiction - State", section="Applicant Profile"),
        _field_spec("jurisdiction_centre", "Jurisdiction - Centre", section="Applicant Profile"),

        _field_spec("mobile_number", "Mobile Number", required=True, section="Contact & Principal Place"),
        _field_spec("email_address", "Email Address", required=True, field_type="email", section="Contact & Principal Place"),
        _field_spec("office_fax_number_std", "Office Fax Number (STD)", section="Contact & Principal Place"),
        _field_spec("principal_address", "Principal Address", required=True, field_type="textarea", section="Contact & Principal Place"),
        _field_spec("building_flat_no", "Building No./Flat No.", section="Contact & Principal Place"),
        _field_spec("floor_no", "Floor No.", section="Contact & Principal Place"),
        _field_spec("premises_road_street", "Name of Premises/Building, Road/Street", section="Contact & Principal Place"),
        _field_spec("city_town_locality_village", "City/Town/Locality/Village", section="Contact & Principal Place"),
        _field_spec("district", "District", section="Contact & Principal Place"),
        _field_spec("state_name", "State", section="Contact & Principal Place"),
        _field_spec("latitude", "Latitude", section="Contact & Principal Place"),
        _field_spec("longitude", "Longitude", section="Contact & Principal Place"),

        _field_spec("import_activity", "Import", field_type="select", options=yes_no, section="Business Activity"),
        _field_spec("export_activity", "Export", field_type="select", options=yes_no, section="Business Activity"),
        _field_spec("factory_manufacturing", "Factory / Manufacturing", field_type="select", options=yes_no, section="Business Activity"),
        _field_spec("bonded_warehouse", "Bonded Warehouse", field_type="select", options=yes_no, section="Business Activity"),
        _field_spec("leasing_business", "Leasing Business", field_type="select", options=yes_no, section="Business Activity"),
        _field_spec("eou_stp_ehtp", "EOU / STP / EHTP", field_type="select", options=yes_no, section="Business Activity"),
        _field_spec("goods_hsn_details", "Details of Goods/Services with HSN Code", field_type="textarea", section="Business Activity"),

        _field_spec("central_excise_registration_number", "Central Excise Registration Number", section="Existing Registrations & IDs"),
        _field_spec("central_sales_tax_registration_number", "Central Sales Tax Registration Number", section="Existing Registrations & IDs"),
        _field_spec("entry_tax_registration_number", "Entry Tax Registration Number", section="Existing Registrations & IDs"),
        _field_spec("entertainment_tax_registration_number", "Entertainment Tax Registration Number", section="Existing Registrations & IDs"),
        _field_spec("importer_exporter_code_number", "Importer/Exporter Code Number", section="Existing Registrations & IDs"),
        _field_spec("llp_identification_number", "LLP Identification Number / Foreign LLPIN", section="Existing Registrations & IDs"),
        _field_spec("corporate_identity_number", "Corporate Identity Number / Foreign Company Registration Number", section="Existing Registrations & IDs"),

        _field_spec("bank_name", "Bank Name", required=True, section="Bank Account"),
        _field_spec("account_number", "Account Number", required=True, section="Bank Account"),
        _field_spec("branch_address", "Branch Address", section="Bank Account"),
        _field_spec("challan_identification_number", "Challan Identification Number", section="Bank Account"),
        _field_spec("challan_date", "Challan Date", field_type="date", section="Bank Account"),
        _field_spec("challan_amount", "Challan Amount", field_type="number", section="Bank Account"),
        _field_spec("details_of_bank_accounts", "Details of Bank Accounts", field_type="textarea", section="Bank Account"),

        _field_spec("applying_as_sez_developer", "Applying as SEZ Developer", field_type="select", options=yes_no, section="SEZ / Casual Taxable"),
        _field_spec("applying_as_sez_unit", "Applying as SEZ Unit", field_type="select", options=yes_no, section="SEZ / Casual Taxable"),
        _field_spec("applying_as_casual_taxable_person", "Applying as Casual Taxable Person", field_type="select", options=yes_no, section="SEZ / Casual Taxable"),
        _field_spec("casual_period_from", "Casual Taxable Period - From", field_type="date", section="SEZ / Casual Taxable"),
        _field_spec("casual_period_to", "Casual Taxable Period - To", field_type="date", section="SEZ / Casual Taxable"),

        _field_spec("remarks", "Remarks", field_type="textarea", section="Declarations"),
    ]


def _clean_suggested_label(label: str) -> str:
    text = " ".join(str(label or "").split()).strip(" :-")
    if not text:
        return ""
    if len(text) < 3 or len(text) > 90:
        return ""

    lowered = text.lower()
    noisy_phrases = [
        "to be auto-populated",
        "tick in check box",
        "for which option is not available",
        "as mentioned in permanent account number",
        "application for registration",
        "form gst reg-01",
    ]
    if any(phrase in lowered for phrase in noisy_phrases):
        return ""

    return text


def _official_item_fields(item: dict[str, Any]) -> list[dict[str, Any]]:
    if _is_gst_reg01_form(item):
        return _gst_reg01_fields()

    labels = [
        _clean_suggested_label(str(x))
        for x in (item.get("suggested_labels") or [])
    ]
    labels = [label for label in labels if label]

    fields: list[dict[str, Any]] = []
    seen: set[str] = set()

    for label in labels[:60]:
        field_id = _to_field_id(label)
        if not field_id or field_id in seen:
            continue
        seen.add(field_id)

        field_type = "text"
        if "dd/mm/yyyy" in label.lower() or "date" in label.lower():
            field_type = "date"
        elif "number" in label.lower() or "amount" in label.lower():
            field_type = "number"

        fields.append(
            _field_spec(
                field_id,
                label,
                field_type=field_type,
                section="Detected Fields",
            )
        )

    fallback = [
        ("legal_name", "Legal Name"),
        ("pan_or_id", "PAN / Identity Number"),
        ("mobile_number", "Mobile Number"),
        ("email_address", "Email Address"),
        ("principal_address", "Principal Address"),
        ("remarks", "Remarks"),
    ]
    for field_id, label in fallback:
        if field_id in seen:
            continue
        fields.append(
            _field_spec(
                field_id,
                label,
                section="Core Details",
            )
        )

    return fields[:70]

def _official_item_to_template(item: dict[str, Any]) -> dict[str, Any]:
    form_id = str(item.get("form_id") or "")
    department = str(item.get("department") or "Official")
    purpose = str(item.get("purpose") or "")
    form_code = str(item.get("form_code") or "")
    summary_parts = [part for part in [purpose, form_code] if part]
    summary = " | ".join(summary_parts) if summary_parts else "Official form"

    return {
        "template_id": f"{_OFFICIAL_TEMPLATE_PREFIX}{form_id}",
        "name": str(item.get("form_name") or form_id),
        "description": summary,
        "category": "official_form",
        "is_premium": False,
        "tags": ["official", department],
        "act": [],
        "court": [],
        "fields": _official_item_fields(item),
        "official_form": {
            "form_id": form_id,
            "department": department,
            "purpose": purpose,
            "form_code": form_code,
            "has_embedded_fields": bool(item.get("has_embedded_fields", False)),
            "embedded_field_count": int(item.get("embedded_field_count") or 0),
            "page_count": int(item.get("page_count") or 0),
        },
    }


def _template_categories(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for template in templates:
        category = str(template.get("category") or "general")
        counts[category] = counts.get(category, 0) + 1
    return [{"name": name, "count": count} for name, count in counts.items()]


def _find_template(template_id: str) -> dict[str, Any] | None:
    for template in _STATIC_TEMPLATE_LIBRARY:
        if str(template.get("template_id")) == template_id:
            return template
    return None


def _render_template_body(body_lines: list[str], fields: dict[str, Any]) -> str:
    rendered = "\n".join(body_lines)
    normalized_fields = {str(k): str(v) for k, v in fields.items()}
    if "date" not in normalized_fields or not normalized_fields["date"]:
        normalized_fields["date"] = datetime.now().date().isoformat()

    for key, value in normalized_fields.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    return rendered


@router.get("/major-cases")
async def major_cases(
    force_web: bool = Query(default=False),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if force_web:
        web_cases = await _fetch_web_major_cases(limit=10)
        if web_cases:
            return {"cases": web_cases}

    cases: list[dict[str, Any]] = []
    try:
        docs = get_collection("rag_documents")
        base_filter = {"tenant_id": tenant_id, "app_key": app_key}
        primary_docs = await _collect_docs(
            docs,
            {
                **base_filter,
                "source_type": {"$in": ["judgment", "case"]},
            },
            limit=12,
        )
        fallback_docs = (
            await _collect_docs(
                docs,
                {
                    **base_filter,
                    "$or": [
                        {"source_type": {"$regex": "judgment|case|order|decision", "$options": "i"}},
                        {"legal_court_name": {"$regex": "supreme court|high court", "$options": "i"}},
                        {"legal_metadata.court_name": {"$regex": "supreme court|high court", "$options": "i"}},
                        {"title": {"$regex": "judgment|order|supreme court|high court|vs|v\.", "$options": "i"}},
                    ],
                },
                limit=20,
            )
            if not primary_docs
            else []
        )

        seen: set[str] = set()
        for doc in [*primary_docs, *fallback_docs]:
            legal = dict(doc.get("legal_metadata") or {})
            title = str(doc.get("title") or "Legal Case Note")
            court = str(legal.get("court_name") or doc.get("legal_court_name") or "High Court")
            year = _safe_year(legal.get("doc_date") or doc.get("legal_doc_date") or doc.get("created_at")) or datetime.now().year
            key = f"{title.lower()}|{court.lower()}|{year}"
            if key in seen:
                continue
            seen.add(key)
            cases.append(
                {
                    "title": title,
                    "court": court,
                    "year": year,
                    "summary": f"Indexed source: {str(doc.get('source_type') or 'document')}",
                    "query": title,
                    "url": str(doc.get("source_uri") or ""),
                }
            )
            if len(cases) >= 10:
                break
    except Exception:
        cases = []

    if len(cases) < 10:
        web_cases = await _fetch_web_major_cases(limit=10)
        cases = _merge_case_items(cases, web_cases, limit=10)

    if len(cases) < 3:
        cases = _merge_case_items(cases, _static_case_items(), limit=10)

    return {"cases": cases}


@router.get("/legal-news")
async def legal_news(
    force_web: bool = Query(default=False),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if force_web:
        web_news = await _fetch_web_legal_news(limit=10)
        if web_news:
            return {"news": web_news}

    news: list[dict[str, Any]] = []
    try:
        docs = get_collection("rag_documents")
        base_filter = {"tenant_id": tenant_id, "app_key": app_key}
        primary_docs = await _collect_docs(
            docs,
            {
                **base_filter,
                "source_type": {"$in": ["news", "update"]},
            },
            limit=12,
        )
        fallback_docs = (
            await _collect_docs(
                docs,
                {
                    **base_filter,
                    "$or": [
                        {"source_type": {"$regex": "news|update|notification|circular|alert|press", "$options": "i"}},
                        {"tags": {"$in": ["news", "update", "notification", "circular", "alert"]}},
                        {"title": {"$regex": "update|notification|circular|amendment|compliance|legal news", "$options": "i"}},
                    ],
                },
                limit=20,
            )
            if not primary_docs
            else []
        )

        seen: set[str] = set()
        for doc in [*primary_docs, *fallback_docs]:
            title = str(doc.get("title") or "Legal Update")
            date_text = _safe_iso_date(doc.get("created_at") or doc.get("legal_doc_date") or datetime.now())
            key = f"{title.lower()}|{date_text}"
            if key in seen:
                continue
            seen.add(key)
            news.append(
                {
                    "title": title,
                    "source": _safe_source_label(doc),
                    "date": date_text,
                    "summary": f"Indexed source: {str(doc.get('source_type') or 'document')}",
                    "query": title,
                    "url": str(doc.get("source_uri") or ""),
                }
            )
            if len(news) >= 10:
                break
    except Exception:
        news = []

    if len(news) < 10:
        web_news = await _fetch_web_legal_news(limit=10)
        news = _merge_news_items(news, web_news, limit=10)

    if len(news) < 3:
        news = _merge_news_items(news, _static_news_items(), limit=10)

    return {"news": news}


@router.post("/legal-research")
async def legal_research(
    payload: LegacyLegalResearchRequest,
    background_tasks: BackgroundTasks,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    rag_payload = RagQueryRequest(
        query=payload.query,
        top_k=5,
        max_candidates=300,
        include_context=False,
    )
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=rag_payload)
    except Exception:
        result = {
            "answer": "I do not have enough indexed content matching this question yet. Please ingest relevant documents for this topic.",
            "citations": [],
            "strategy": "rag_unavailable",
            "candidate_count": 0,
            "context": None,
        }

    return await build_hybrid_legal_response(
        tenant_id=tenant_id,
        app_key=app_key,
        query=payload.query,
        query_type=payload.query_type,
        rag_result=result,
        background_tasks=background_tasks,
    )


@router.get("/legal-sync/queue")
async def legal_sync_queue(
    status: str = Query(default="pending", max_length=40),
    limit: int = Query(default=25, ge=1, le=200),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    items = await list_sync_queue(tenant_id=tenant_id, app_key=app_key, status=status, limit=limit)
    return {"items": items, "count": len(items)}



@router.post("/legal-sync/run-once")
async def legal_sync_run_once(
    max_jobs: int = Query(default=5, ge=1, le=100),
    current_user: dict = Depends(require_roles([Role.super_admin, Role.tenant_admin, Role.operator])),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = resolve_tenant_id(current_user, x_tenant_id)
    requested_app_key = x_app_key or current_user.get("app_key") or _DEFAULT_APP_KEY
    app_key = _resolve_compat_app_key(requested_app_key)
    try:
        summary = await run_legal_sync_once(
            max_jobs=max_jobs,
            tenant_id=tenant_id,
            app_key=app_key,
            worker_id="manual-api",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Legal sync unavailable: {str(exc)}") from exc
    return summary


@router.post("/search-cases")
async def search_cases(
    payload: LegacyCaseSearchRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    legal_filters = RagLegalFilter(court_name=payload.court.lower()) if payload.court else None
    rag_payload = RagQueryRequest(
        query=payload.query,
        source_types=["judgment", "case"],
        top_k=5,
        max_candidates=250,
        legal_filters=legal_filters,
    )
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=rag_payload)
    except Exception:
        result = {"citations": []}

    cases = [
        {
            "content": f"{c['reference']}\n\n{c['snippet']}",
            "citation": c,
        }
        for c in result["citations"]
    ]
    return {"cases": cases}


@router.post("/search-statute")
async def search_statute(
    payload: LegacyStatuteSearchRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    q = f"{payload.act_name}"
    if payload.section:
        q += f" section {payload.section}"

    legal_filters = RagLegalFilter(
        act_name=payload.act_name.lower(),
        section=(payload.section or "").lower() or None,
    )
    rag_payload = RagQueryRequest(
        query=q,
        source_types=["statute", "act", "regulation"],
        legal_filters=legal_filters,
        top_k=4,
        max_candidates=200,
    )
    try:
        result = await query_knowledge(tenant_id=tenant_id, app_key=app_key, payload=rag_payload)
    except Exception:
        result = {
            "answer": "I do not have enough indexed legal content for this statute query yet.",
            "citations": [],
        }

    return {
        "content": result["answer"],
        "explanation": result["answer"],
        "citations": result["citations"],
    }


@router.post("/draft-document")
async def draft_document(payload: dict[str, Any]):
    doc_type = str(payload.get("document_type") or "legal notice")
    facts = str(payload.get("facts") or "")
    parties = payload.get("parties") or {}
    grounds = payload.get("legal_grounds") or []
    prayer = str(payload.get("prayer") or "")

    draft = render_guided_document_draft(
        document_type=doc_type,
        facts=facts,
        parties=parties,
        legal_grounds=grounds,
        prayer=prayer,
        extra_fields=payload,
    )

    return {
        "drafted_document": draft["drafted_document"],
        "draft_status": draft["draft_status"],
        "follow_up_questions": draft["follow_up_questions"],
        "recommended_clauses": draft["recommended_clauses"],
        "firmness_score": draft["firmness_score"],
    }


@router.post("/review-document")
async def review_document(
    file: UploadFile = File(...),
    query: str | None = Form(default=None),
):
    content = await file.read()
    preview = content[:3000].decode("utf-8", errors="ignore") if content else ""
    analysis = [
        f"Document: {file.filename}",
        f"Question: {query or 'General legal review'}",
        "",
        "Preliminary review (compat mode):",
        "- Verify jurisdiction, governing law, and dispute resolution clauses.",
        "- Verify obligations, timelines, penalty/termination clauses.",
        "- Verify signatures, witnesses, annexures, and date consistency.",
    ]
    if preview:
        analysis.append("")
        analysis.append("Document preview:")
        analysis.append(preview[:1200])
    return {"analysis": "\n".join(analysis)}


@router.get("/v2/templates")
async def v2_templates(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    templates = [
        {
            "template_id": t["template_id"],
            "name": t["name"],
            "description": t["description"],
            "category": t["category"],
            "is_premium": bool(t.get("is_premium", False)),
            "tags": t.get("tags", []),
            "act": t.get("act", []),
            "court": t.get("court", []),
        }
        for t in _STATIC_TEMPLATE_LIBRARY
    ]

    try:
        official_items = await list_official_forms(
            tenant_id=tenant_id,
            app_key=app_key,
            limit=120,
        )
    except Exception:
        official_items = []

    for item in official_items:
        templates.append(_official_item_to_template(item))

    return {"total": len(templates), "templates": templates, "items": templates}

@router.get("/v2/templates/categories")
async def v2_template_categories(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    categories = _template_categories(_STATIC_TEMPLATE_LIBRARY)
    counts = {str(item.get("name") or ""): int(item.get("count") or 0) for item in categories}

    try:
        official_items = await list_official_forms(tenant_id=tenant_id, app_key=app_key, limit=500)
    except Exception:
        official_items = []

    if official_items:
        counts["official_form"] = counts.get("official_form", 0) + len(official_items)

    normalized = [{"name": name, "count": count} for name, count in counts.items()]
    normalized.sort(key=lambda x: x["name"])
    return {"categories": normalized}

@router.get("/v2/templates/{template_id}")
async def v2_template_detail(
    template_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if _is_official_template_id(template_id):
        form_id = _extract_official_form_id(template_id)
        item = await get_official_form(tenant_id=tenant_id, app_key=app_key, form_id=form_id)
        if not item:
            raise HTTPException(status_code=404, detail="Template not found")
        return _official_item_to_template(item)

    template = _find_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template

@router.get("/v2/templates/{template_id}/fields")
async def v2_template_fields(
    template_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if _is_official_template_id(template_id):
        form_id = _extract_official_form_id(template_id)
        item = await get_official_form(tenant_id=tenant_id, app_key=app_key, form_id=form_id)
        if not item:
            raise HTTPException(status_code=404, detail="Template not found")
        return {"fields": _official_item_fields(item)}

    template = _find_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"fields": template.get("fields", [])}

@router.post("/v2/templates/render")
async def v2_template_render(
    payload: LegacyTemplateRenderRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if _is_official_template_id(payload.template_id):
        form_id = _extract_official_form_id(payload.template_id)
        item = await get_official_form(tenant_id=tenant_id, app_key=app_key, form_id=form_id)
        if not item:
            raise HTTPException(status_code=404, detail="Template not found")

        lines = [
            f"OFFICIAL FORM PREVIEW: {item.get('form_name')}",
            f"Department: {item.get('department')}",
            f"Purpose: {item.get('purpose')}",
            "",
            "Submitted values:",
        ]
        for key, value in (payload.fields or {}).items():
            lines.append(f"- {key}: {value}")
        if not payload.fields:
            lines.append("- (No values submitted yet)")

        return {
            "rendered_document": "\n".join(lines),
            "draft_status": "ready_for_pdf_render",
            "validation": {
                "citations": {
                    "total_citations": 0,
                    "valid_citations": 0,
                    "accuracy_score": 100,
                    "invalid_citations": [],
                },
                "foreign_law": {"has_foreign_law": False},
                "risky_language": {"has_risky_language": False},
                "firmness_score": 100,
                "missing_required_fields": [],
                "unresolved_placeholders": [],
                "follow_up_questions": [],
                "recommended_clauses": [],
                "official_form": {
                    "form_id": form_id,
                    "render_pdf_endpoint": f"/api/v1/v2/official-forms/{form_id}/render-pdf",
                },
            },
        }

    template = _find_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    draft = render_template_document(template=template, fields=payload.fields)
    rendered_document = draft["rendered_document"]

    return {
        "rendered_document": rendered_document,
        "draft_status": draft["draft_status"],
        "validation": {
            "citations": {
                "total_citations": 0,
                "valid_citations": 0,
                "accuracy_score": 100,
                "invalid_citations": [],
            },
            "foreign_law": {"has_foreign_law": False},
            "risky_language": {"has_risky_language": False},
            "firmness_score": draft["firmness_score"],
            "missing_required_fields": draft["missing_required_fields"],
            "unresolved_placeholders": draft["unresolved_placeholders"],
            "follow_up_questions": draft["follow_up_questions"],
            "recommended_clauses": draft["recommended_clauses"],
        },
    }

@router.post("/v2/templates/render-pdf")
async def v2_template_render_pdf(
    payload: LegacyTemplateRenderRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    if _is_official_template_id(payload.template_id):
        form_id = _extract_official_form_id(payload.template_id)
        file_bytes = await render_official_form_pdf(
            tenant_id=tenant_id,
            app_key=app_key,
            form_id=form_id,
            fields=payload.fields,
        )
        if not file_bytes:
            raise HTTPException(status_code=404, detail="Template not found")

        filename = f"{form_id}_official_filled.pdf"
        return StreamingResponse(
            file_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    template = _find_template(payload.template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    draft = render_template_document(template=template, fields=payload.fields)
    rendered_document = draft["rendered_document"]
    file_bytes = _render_text_as_pdf(rendered_document)
    filename = f"{payload.template_id}.pdf"
    return StreamingResponse(
        file_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/v2/official-forms/upload-guidelines")
async def v2_official_forms_upload_guidelines():
    return get_official_form_upload_guidelines()

@router.post("/v2/official-forms/upload")
async def v2_official_forms_upload(
    file: UploadFile = File(...),
    form_name: str = Form(...),
    purpose: str = Form(...),
    department: str = Form(...),
    form_code: str | None = Form(default=None),
    description: str | None = Form(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        item = await register_official_form(
            tenant_id=tenant_id,
            app_key=app_key,
            file_name=file.filename or "uploaded.pdf",
            content_type=file.content_type,
            payload=payload,
            form_name=form_name,
            purpose=purpose,
            department=department,
            form_code=form_code,
            description=description,
        )
    except OfficialFormValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.as_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to register official form: {str(exc)}") from exc

    return {"item": item, "upload_guidelines": get_official_form_upload_guidelines()}


@router.get("/v2/official-forms")
async def v2_official_forms_list(
    department: str | None = Query(default=None, max_length=120),
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    items = await list_official_forms(
        tenant_id=tenant_id,
        app_key=app_key,
        department=department,
        search=search,
        limit=limit,
    )
    return {"items": items, "count": len(items)}


@router.get("/v2/official-forms/{form_id}")
async def v2_official_forms_detail(
    form_id: str,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    item = await get_official_form(tenant_id=tenant_id, app_key=app_key, form_id=form_id)
    if not item:
        raise HTTPException(status_code=404, detail="Official form not found")
    return item


@router.post("/v2/official-forms/{form_id}/render-pdf")
async def v2_official_forms_render_pdf(
    form_id: str,
    payload: OfficialFormRenderRequest,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    try:
        file_bytes = await render_official_form_pdf(
            tenant_id=tenant_id,
            app_key=app_key,
            form_id=form_id,
            fields=payload.fields,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to render official form PDF: {str(exc)}") from exc

    if not file_bytes:
        raise HTTPException(status_code=404, detail="Official form not found")

    filename = f"{form_id}_filled.pdf"
    return StreamingResponse(
        file_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.get("/templates/categories")
async def templates_categories_legacy():
    categories = _template_categories(_STATIC_TEMPLATE_LIBRARY)
    return {"categories": categories, "items": categories}


@router.get("/templates/summary")
async def templates_summary_legacy():
    templates = _STATIC_TEMPLATE_LIBRARY
    categories = _template_categories(templates)
    premium_count = sum(1 for t in templates if bool(t.get("is_premium", False)))
    return {
        "total_templates": len(templates),
        "premium_templates": premium_count,
        "free_templates": max(0, len(templates) - premium_count),
        "categories": categories,
    }


@router.get("/models/recommended")
async def recommended_models_legacy():
    return {
        "models": [
            {
                "id": "sanmitra-legal-research-v1",
                "name": "SanMitra Legal Research",
                "purpose": "case_research",
                "latency_tier": "balanced",
                "recommended": True,
            },
            {
                "id": "sanmitra-legal-drafting-v1",
                "name": "SanMitra Legal Drafting",
                "purpose": "document_drafting",
                "latency_tier": "quality",
                "recommended": True,
            },
        ]
    }


@router.get("/diary")
async def list_professional_diary(
    limit: int = Query(default=20, ge=1, le=100),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    try:
        entries_col = get_collection("legal_diary_entries")
        cursor = entries_col.find({"tenant_id": tenant_id, "app_key": app_key}).sort("created_at", -1).limit(limit)
        entries = await cursor.to_list(length=limit)
    except Exception:
        entries = []

    items = [
        {
            "entry_id": str(doc.get("entry_id") or doc.get("_id") or ""),
            "title": str(doc.get("title") or "Diary Entry"),
            "content": str(doc.get("content") or ""),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "tags": doc.get("tags") or [],
        }
        for doc in entries
    ]
    return {"items": items, "count": len(items)}


@router.post("/diary")
async def create_professional_diary(
    payload: dict[str, Any],
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_app_key: str | None = Header(default=None, alias="X-App-Key"),
):
    tenant_id = _resolve_compat_tenant_id(x_tenant_id)
    app_key = _resolve_compat_app_key(x_app_key)

    entry_id = str(payload.get("entry_id") or f"diary-{datetime.now().strftime('%Y%m%d%H%M%S%f')}")
    now = datetime.now()
    doc = {
        "entry_id": entry_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "title": str(payload.get("title") or "Untitled Entry"),
        "content": str(payload.get("content") or ""),
        "tags": payload.get("tags") or [],
        "created_at": now,
        "updated_at": now,
    }

    try:
        entries_col = get_collection("legal_diary_entries")
        await entries_col.insert_one(doc)
    except Exception:
        # Preserve legacy UX even if datastore is unavailable in local/dev mode.
        pass

    return {
        "entry_id": entry_id,
        "status": "created",
        "title": doc["title"],
        "content": doc["content"],
        "tags": doc["tags"],
        "created_at": doc["created_at"],
    }
























