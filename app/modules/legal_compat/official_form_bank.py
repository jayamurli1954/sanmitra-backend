from __future__ import annotations

import hashlib
import logging
import re
import textwrap
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

_form_logger = logging.getLogger(__name__)

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import get_settings
from app.db.mongo import get_collection

_FORM_BANK_COLLECTION = "official_form_bank"
_FORM_STORAGE_DIR = Path(__file__).resolve().parent / "data" / "official_forms" / "uploaded"


class OfficialFormValidationError(ValueError):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint or ""

    def as_detail(self) -> dict[str, str]:
        detail = {"code": self.code, "message": self.message}
        if self.hint:
            detail["hint"] = self.hint
        return detail


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return text or "official-form"


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _normalize_fields(fields: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (fields or {}).items():
        k = str(key).strip()
        if not k:
            continue
        v = str(value).strip()
        if v:
            normalized[k] = v
    return normalized


def _file_extension(filename: str, content_type: str | None) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return ".pdf"
    if name.endswith(".docx"):
        return ".docx"
    if (content_type or "").lower() == "application/pdf":
        return ".pdf"
    if "wordprocessingml" in (content_type or "").lower():
        return ".docx"
    return ".bin"


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _extract_label_candidates(text: str) -> list[str]:
    labels: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line or len(line) < 4 or len(line) > 80:
            continue
        if ":" in line:
            candidate = line.split(":", 1)[0].strip()
        else:
            candidate = line
        candidate = re.sub(r"^[0-9().\-\s]+", "", candidate).strip()
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if len(candidate) < 3 or len(candidate) > 60:
            continue
        if any(ch.isdigit() for ch in candidate) and len(candidate) <= 5:
            continue
        labels.add(candidate)
    return sorted(labels)


def _profile_pdf(payload: bytes) -> dict[str, Any]:
    reader = PdfReader(BytesIO(payload))
    form_fields = reader.get_fields() or {}

    suggested: set[str] = set()
    for page in reader.pages[:20]:
        text = page.extract_text() or ""
        for label in _extract_label_candidates(text):
            suggested.add(label)
            if len(suggested) >= 120:
                break
        if len(suggested) >= 120:
            break

    return {
        "page_count": len(reader.pages),
        "embedded_field_count": len(form_fields),
        "has_embedded_fields": bool(form_fields),
        "embedded_field_names": sorted(str(name) for name in form_fields.keys())[:150],
        "suggested_labels": sorted(suggested)[:150],
    }


def _field_aliases(field_name: str) -> list[str]:
    base = _normalize(field_name)
    if not base:
        return []
    aliases = {base}
    aliases.add(base.replace(" no ", " number "))
    aliases.add(base.replace(" number ", " no "))
    aliases.add(base.replace(" gstin ", " gst in "))
    return [a for a in aliases if a]


def _collect_pdf_text_positions(page) -> list[tuple[str, float, float]]:
    out: list[tuple[str, float, float]] = []

    def visitor_text(txt, _cm, tm, _font_dict, _font_size):
        raw = str(txt or "").strip()
        if not raw:
            return
        x = float(tm[4]) if len(tm) > 4 else 0.0
        y = float(tm[5]) if len(tm) > 5 else 0.0
        out.append((raw, x, y))

    page.extract_text(visitor_text=visitor_text)
    return out


def _overlay_nonfillable_pdf(payload: bytes, fields: dict[str, str]) -> BytesIO:
    reader = PdfReader(BytesIO(payload))
    writer = PdfWriter()

    field_aliases = {name: _field_aliases(name) for name in fields.keys()}
    consumed: set[str] = set()

    for page in reader.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        text_positions = _collect_pdf_text_positions(page)
        matches: list[tuple[str, float, float]] = []

        for text, x, y in text_positions:
            line = _normalize(text)
            if not line:
                continue
            for field_name, aliases in field_aliases.items():
                if field_name in consumed:
                    continue
                if any(alias and alias in line for alias in aliases):
                    matches.append((field_name, x, y))
                    consumed.add(field_name)
                    break

        if matches:
            packet = BytesIO()
            overlay = canvas.Canvas(packet, pagesize=(width, height))
            overlay.setFont("Helvetica", 8)
            for field_name, x, y in matches:
                value = fields.get(field_name, "")
                wrapped = textwrap.wrap(value, width=40) or [value]
                y_cursor = max(18, y - 10)
                x_cursor = min(width - 170, x + 170)
                for line in wrapped[:3]:
                    overlay.drawString(x_cursor, y_cursor, line[:80])
                    y_cursor -= 9
            overlay.save()
            packet.seek(0)
            overlay_pdf = PdfReader(packet)
            page.merge_page(overlay_pdf.pages[0])

        writer.add_page(page)

    packet = BytesIO()
    annex = canvas.Canvas(packet, pagesize=A4)
    page_w, page_h = A4
    y = page_h - 48
    annex.setFont("Helvetica-Bold", 12)
    annex.drawString(42, y, "Annexure: Submitted Form Data")
    y -= 24
    annex.setFont("Helvetica", 10)

    for key, value in fields.items():
        line = f"{key}: {value}"
        wrapped = textwrap.wrap(line, width=92) or [line]
        for part in wrapped:
            if y < 48:
                annex.showPage()
                annex.setFont("Helvetica", 10)
                y = page_h - 48
            annex.drawString(42, y, part)
            y -= 14

    annex.save()
    packet.seek(0)
    annex_pdf = PdfReader(packet)
    writer.add_page(annex_pdf.pages[0])

    out = BytesIO()
    writer.write(out)
    out.seek(0)
    return out


def _fill_pdf(payload: bytes, fields: dict[str, str]) -> BytesIO:
    reader = PdfReader(BytesIO(payload))
    form_fields = reader.get_fields() or {}
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    if form_fields:
        for idx in range(len(writer.pages)):
            try:
                writer.update_page_form_field_values(
                    writer.pages[idx],
                    fields,
                    auto_regenerate=False,
                )
            except Exception as exc:
                _form_logger.warning("PDF form field update failed on page %d: %s", idx, exc)
                continue
        out = BytesIO()
        writer.write(out)
        out.seek(0)
        return out

    return _overlay_nonfillable_pdf(payload, fields)


def _public_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "form_id": str(doc.get("form_id") or ""),
        "form_name": str(doc.get("form_name") or ""),
        "purpose": str(doc.get("purpose") or ""),
        "department": str(doc.get("department") or ""),
        "form_code": str(doc.get("form_code") or ""),
        "description": str(doc.get("description") or ""),
        "tenant_id": str(doc.get("tenant_id") or ""),
        "app_key": str(doc.get("app_key") or ""),
        "source_filename": str(doc.get("source_filename") or ""),
        "file_extension": str(doc.get("file_extension") or ""),
        "file_size_bytes": int(doc.get("file_size_bytes") or 0),
        "file_sha256": str(doc.get("file_sha256") or ""),
        "page_count": int(doc.get("page_count") or 0),
        "embedded_field_count": int(doc.get("embedded_field_count") or 0),
        "has_embedded_fields": bool(doc.get("has_embedded_fields", False)),
        "embedded_field_names": doc.get("embedded_field_names") or [],
        "suggested_labels": doc.get("suggested_labels") or [],
        "compatibility_mode": (
            "embedded_fields" if bool(doc.get("has_embedded_fields", False)) else "overlay_with_annexure"
        ),
        "created_at": (
            doc.get("created_at").isoformat()
            if isinstance(doc.get("created_at"), datetime)
            else str(doc.get("created_at") or "")
        ),
        "updated_at": (
            doc.get("updated_at").isoformat()
            if isinstance(doc.get("updated_at"), datetime)
            else str(doc.get("updated_at") or "")
        ),
    }


def get_official_form_upload_guidelines() -> dict[str, Any]:
    settings = get_settings()
    max_mb = max(1, int(settings.LEGAL_OFFICIAL_FORM_MAX_UPLOAD_MB))
    max_pages = max(1, int(settings.LEGAL_OFFICIAL_FORM_MAX_PAGES))
    min_labels = max(0, int(settings.LEGAL_OFFICIAL_FORM_MIN_SUGGESTED_LABELS))

    return {
        "accepted_extensions": [".pdf"],
        "accepted_content_types": ["application/pdf"],
        "max_upload_mb": max_mb,
        "max_upload_bytes": max_mb * 1024 * 1024,
        "max_pages": max_pages,
        "required_metadata": ["form_name", "purpose", "department"],
        "optional_metadata": ["form_code", "description"],
        "upload_instructions": [
            "Upload only official government/authority PDF forms (not images/screenshots).",
            "Provide required metadata: form_name, purpose, and department for indexing.",
            f"Keep file size <= {max_mb} MB and page count <= {max_pages}.",
            "Use unlocked PDFs with selectable text for best auto-fill compatibility.",
        ],
        "compatibility_rules": [
            f"PDF must be <= {max_mb} MB and <= {max_pages} pages",
            "Corrupted/encrypted PDFs are rejected",
            (
                "For non-fillable PDFs, text labels must be machine-readable; "
                f"minimum extracted labels: {min_labels}"
            ),
        ],
        "error_catalog": {
            "missing_metadata": "Required metadata missing or invalid",
            "upload_too_large": "Uploaded file exceeds configured upload size",
            "unsupported_file_type": "Only PDF upload is supported",
            "incompatible_pdf": "PDF cannot be parsed or is not compatible for MVP auto-fill",
        },
        "error_codes": [
            "missing_metadata",
            "upload_too_large",
            "unsupported_file_type",
            "incompatible_pdf",
        ],
    }


def _validate_upload_metadata(form_name: str, purpose: str, department: str) -> tuple[str, str, str]:
    normalized_form_name = (form_name or "").strip()
    normalized_purpose = (purpose or "").strip()
    normalized_department = (department or "").strip()

    if not normalized_form_name:
        raise OfficialFormValidationError("missing_metadata", "form_name is required")
    if not normalized_purpose:
        raise OfficialFormValidationError("missing_metadata", "purpose is required")
    if not normalized_department:
        raise OfficialFormValidationError("missing_metadata", "department is required")

    if len(normalized_form_name) > 180:
        raise OfficialFormValidationError("missing_metadata", "form_name must be 180 characters or less")
    if len(normalized_purpose) > 300:
        raise OfficialFormValidationError("missing_metadata", "purpose must be 300 characters or less")
    if len(normalized_department) > 120:
        raise OfficialFormValidationError("missing_metadata", "department must be 120 characters or less")

    return normalized_form_name, normalized_purpose, normalized_department


def _validate_upload_payload(payload: bytes, *, max_upload_mb: int) -> None:
    if not payload:
        raise OfficialFormValidationError("missing_metadata", "Uploaded file is empty")

    max_bytes = max_upload_mb * 1024 * 1024
    if len(payload) > max_bytes:
        raise OfficialFormValidationError(
            "upload_too_large",
            f"Uploaded file exceeds {max_upload_mb} MB",
            hint=f"Compress/split the form and keep PDF size <= {max_upload_mb} MB",
        )


def _validate_profile_compatibility(
    profile: dict[str, Any],
    *,
    max_pages: int,
    min_suggested_labels: int,
) -> None:
    page_count = int(profile.get("page_count") or 0)
    has_embedded = bool(profile.get("has_embedded_fields", False))
    suggested_count = len(profile.get("suggested_labels") or [])

    if page_count <= 0:
        raise OfficialFormValidationError(
            "incompatible_pdf",
            "PDF has no readable pages",
            hint="Upload a valid, non-empty PDF generated from official source",
        )

    if page_count > max_pages:
        raise OfficialFormValidationError(
            "incompatible_pdf",
            f"PDF has {page_count} pages which exceeds max allowed {max_pages}",
            hint="Split very large bundles and upload one official form at a time",
        )

    if not has_embedded and suggested_count < min_suggested_labels:
        raise OfficialFormValidationError(
            "incompatible_pdf",
            "PDF is not compatible for MVP auto-fill (insufficient machine-readable form labels)",
            hint="Upload a fillable PDF or a digital-text PDF (not only scanned image pages)",
        )


async def register_official_form(
    *,
    tenant_id: str,
    app_key: str,
    file_name: str,
    content_type: str | None,
    payload: bytes,
    form_name: str,
    purpose: str,
    department: str,
    form_code: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    max_upload_mb = max(1, int(settings.LEGAL_OFFICIAL_FORM_MAX_UPLOAD_MB))
    max_pages = max(1, int(settings.LEGAL_OFFICIAL_FORM_MAX_PAGES))
    min_labels = max(0, int(settings.LEGAL_OFFICIAL_FORM_MIN_SUGGESTED_LABELS))

    form_name, purpose, department = _validate_upload_metadata(form_name, purpose, department)
    _validate_upload_payload(payload, max_upload_mb=max_upload_mb)

    extension = _file_extension(file_name, content_type)
    if extension != ".pdf":
        raise OfficialFormValidationError(
            "unsupported_file_type",
            "Only PDF upload is supported in this MVP",
            hint="Upload official form in .pdf format",
        )

    try:
        profile = _profile_pdf(payload)
    except Exception as exc:
        message = "Unable to parse this PDF"
        lower = str(exc).lower()
        if "encrypted" in lower or "decrypt" in lower:
            message = "Encrypted/password-protected PDF is not supported"
        raise OfficialFormValidationError(
            "incompatible_pdf",
            message,
            hint="Use an unlocked official PDF with selectable text",
        ) from exc

    _validate_profile_compatibility(
        profile,
        max_pages=max_pages,
        min_suggested_labels=min_labels,
    )

    form_id = f"{_slugify(department)}-{_slugify(form_name)}-{uuid4().hex[:10]}"
    _FORM_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    stored_file_path = _FORM_STORAGE_DIR / f"{form_id}{extension}"

    now = _now_utc()
    doc = {
        "form_id": form_id,
        "tenant_id": tenant_id,
        "app_key": app_key,
        "form_name": form_name,
        "purpose": purpose,
        "department": department,
        "form_code": (form_code or "").strip(),
        "description": (description or "").strip(),
        "source_filename": file_name,
        "content_type": content_type or "",
        "file_extension": extension,
        "file_size_bytes": len(payload),
        "file_sha256": _sha256_bytes(payload),
        "stored_file_path": str(stored_file_path),
        "page_count": int(profile.get("page_count") or 0),
        "embedded_field_count": int(profile.get("embedded_field_count") or 0),
        "has_embedded_fields": bool(profile.get("has_embedded_fields", False)),
        "embedded_field_names": profile.get("embedded_field_names") or [],
        "suggested_labels": profile.get("suggested_labels") or [],
        "created_at": now,
        "updated_at": now,
    }

    # Insert the DB record FIRST so a file-write failure doesn't create an orphaned file
    # with no corresponding DB entry, and a DB failure doesn't leave an orphaned file on disk.
    collection = get_collection(_FORM_BANK_COLLECTION)
    await collection.insert_one(doc)

    try:
        stored_file_path.write_bytes(payload)
    except Exception as exc:
        # DB record was inserted; roll it back to keep DB and filesystem in sync.
        _form_logger.error(
            "File write failed for form_id=%s after DB insert; rolling back DB record: %s",
            form_id, exc,
        )
        try:
            await collection.delete_one({"form_id": form_id})
        except Exception as rollback_exc:
            _form_logger.error(
                "Rollback of DB record for form_id=%s also failed: %s",
                form_id, rollback_exc,
            )
        raise

    return _public_doc(doc)


async def list_official_forms(
    *,
    tenant_id: str,
    app_key: str,
    department: str | None = None,
    search: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"tenant_id": tenant_id, "app_key": app_key}
    if (department or "").strip():
        query["department"] = {"$regex": f"^{re.escape(department.strip())}$", "$options": "i"}
    if (search or "").strip():
        term = re.escape(search.strip())
        query["$or"] = [
            {"form_name": {"$regex": term, "$options": "i"}},
            {"purpose": {"$regex": term, "$options": "i"}},
            {"department": {"$regex": term, "$options": "i"}},
            {"form_code": {"$regex": term, "$options": "i"}},
        ]

    collection = get_collection(_FORM_BANK_COLLECTION)
    cursor = collection.find(query).sort("updated_at", -1).limit(max(1, min(limit, 200)))
    docs = await cursor.to_list(length=max(1, min(limit, 200)))
    return [_public_doc(doc) for doc in docs]


async def get_official_form(
    *,
    tenant_id: str,
    app_key: str,
    form_id: str,
) -> dict[str, Any] | None:
    collection = get_collection(_FORM_BANK_COLLECTION)
    doc = await collection.find_one({"tenant_id": tenant_id, "app_key": app_key, "form_id": form_id})
    return _public_doc(doc) if doc else None


async def render_official_form_pdf(
    *,
    tenant_id: str,
    app_key: str,
    form_id: str,
    fields: dict[str, Any],
) -> BytesIO | None:
    collection = get_collection(_FORM_BANK_COLLECTION)
    doc = await collection.find_one({"tenant_id": tenant_id, "app_key": app_key, "form_id": form_id})
    if not doc:
        return None

    stored_file_path = Path(str(doc.get("stored_file_path") or ""))
    if not stored_file_path.exists():
        return None
    if str(doc.get("file_extension") or "").lower() != ".pdf":
        return None

    payload = stored_file_path.read_bytes()
    normalized_fields = _normalize_fields(fields)
    if not normalized_fields:
        return BytesIO(payload)

    return _fill_pdf(payload, normalized_fields)



