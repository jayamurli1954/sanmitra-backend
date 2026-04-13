from __future__ import annotations

import asyncio
import logging
import smtplib
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

from app.config import get_settings
from app.db.mongo import get_collection, get_mongo_client

logger = logging.getLogger(__name__)

_WORKER_TASK: asyncio.Task | None = None
_STOP_EVENT: asyncio.Event | None = None

MANDIR_SEVA_BOOKINGS = "mandir_seva_bookings"
MANDIR_SEVAS = "mandir_sevas"
MANDIR_DEVOTEES = "mandir_devotees"
MANDIR_TEMPLES = "mandir_temples"

# How often the worker wakes up (seconds). Sends email only when within window.
_POLL_SECONDS = 3600  # 1 hour


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Email helper (automated — via SMTP/Brevo)
# ---------------------------------------------------------------------------

async def _send_reminder_email(
    *,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> tuple[bool, str | None]:
    """Send renewal reminder email via configured SMTP (Brevo or any SMTP relay)."""
    settings = get_settings()
    to_email = str(to_email or "").strip().lower()
    if not to_email or "@" not in to_email:
        return False, "invalid email address"
    if not settings.SMTP_HOST:
        return False, "SMTP not configured"

    from_header = settings.SMTP_FROM_EMAIL
    if settings.SMTP_FROM_NAME:
        from_header = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    def _send_sync() -> None:
        if settings.SMTP_USE_SSL:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
                if settings.SMTP_USE_TLS:
                    smtp.starttls()
                if settings.SMTP_USERNAME:
                    smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                smtp.send_message(msg)

    try:
        await asyncio.to_thread(_send_sync)
        return True, None
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# WhatsApp deep-link builder (admin-click-to-send — no API key required)
# ---------------------------------------------------------------------------

def build_whatsapp_reminder_link(
    *,
    devotee_phone: str,
    devotee_name: str,
    seva_name: str,
    temple_name: str,
    expiry_date: str,
    amount: float,
    days_left: int,
    receipt_number: str,
) -> str:
    """Generate a wa.me deep link so the admin can send a WhatsApp message
    to the devotee with one click (opens WhatsApp with pre-filled text)."""
    phone = str(devotee_phone or "").strip().lstrip("+")
    if not phone:
        return ""

    days_label = f"in {days_left} day{'s' if days_left != 1 else ''}" if days_left > 0 else "today"
    message = (
        f"Namaste {devotee_name},\n\n"
        f"This is a gentle reminder that your *{seva_name}* subscription at "
        f"*{temple_name}* is due for renewal *{days_label}* (expiry: {expiry_date}).\n\n"
        f"Renewal Amount: *Rs.{amount:,.0f}*\n"
        f"Receipt No: {receipt_number}\n\n"
        f"Kindly contact the temple office to renew your seva and continue receiving "
        f"blessings uninterrupted.\n\n"
        f"With regards,\n{temple_name}"
    )
    return f"https://wa.me/{phone}?text={quote(message)}"


# ---------------------------------------------------------------------------
# Email message builders
# ---------------------------------------------------------------------------

def _build_email_body(
    *,
    devotee_name: str,
    seva_name: str,
    temple_name: str,
    expiry_date: str,
    amount: float,
    days_left: int,
    receipt_number: str,
) -> tuple[str, str]:
    """Return (html_body, text_body) for the renewal reminder email."""
    urgency = "soon" if days_left > 7 else "urgently"
    days_label = f"in {days_left} day{'s' if days_left != 1 else ''}" if days_left > 0 else "today"

    text = (
        f"Namaste {devotee_name},\n\n"
        f"This is a reminder that your seva subscription is due for renewal {days_label}.\n\n"
        f"  Seva       : {seva_name}\n"
        f"  Temple     : {temple_name}\n"
        f"  Expiry Date: {expiry_date}\n"
        f"  Amount     : Rs.{amount:,.0f}\n"
        f"  Receipt No : {receipt_number}\n\n"
        f"Please contact the temple office to renew your seva {urgency}.\n\n"
        f"With regards,\n{temple_name}"
    )

    html = (
        "<!DOCTYPE html><html><body style=\"font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px;\">"
        "<div style=\"max-width:560px;margin:0 auto;background:#fff;border-radius:10px;overflow:hidden;"
        "box-shadow:0 2px 8px rgba(0,0,0,.1);\">"
        f"<div style=\"background:#FF9933;padding:20px 24px;text-align:center;\">"
        f"<h2 style=\"color:#fff;margin:0;font-size:22px;\">&#127897; Seva Renewal Reminder</h2>"
        f"<p style=\"color:#fff3e0;margin:4px 0 0;font-size:14px;\">{temple_name}</p></div>"
        f"<div style=\"padding:24px;\">"
        f"<p style=\"font-size:16px;\">Namaste <strong>{devotee_name}</strong>,</p>"
        f"<p>Your <strong>{seva_name}</strong> subscription is due for renewal <strong>{days_label}</strong>.</p>"
        "<table style=\"width:100%;border-collapse:collapse;margin:16px 0;\">"
        f"<tr style=\"background:#fff8f0;\"><td style=\"padding:8px 12px;border:1px solid #ffe0b2;"
        f"font-weight:bold;\">Seva</td><td style=\"padding:8px 12px;border:1px solid #ffe0b2;\">{seva_name}</td></tr>"
        f"<tr><td style=\"padding:8px 12px;border:1px solid #ffe0b2;font-weight:bold;\">Temple</td>"
        f"<td style=\"padding:8px 12px;border:1px solid #ffe0b2;\">{temple_name}</td></tr>"
        f"<tr style=\"background:#fff8f0;\"><td style=\"padding:8px 12px;border:1px solid #ffe0b2;"
        f"font-weight:bold;\">Expiry Date</td><td style=\"padding:8px 12px;border:1px solid #ffe0b2;"
        f"color:#d32f2f;\"><strong>{expiry_date}</strong></td></tr>"
        f"<tr><td style=\"padding:8px 12px;border:1px solid #ffe0b2;font-weight:bold;\">Amount</td>"
        f"<td style=\"padding:8px 12px;border:1px solid #ffe0b2;\">&#8377;{amount:,.0f}</td></tr>"
        f"<tr style=\"background:#fff8f0;\"><td style=\"padding:8px 12px;border:1px solid #ffe0b2;"
        f"font-weight:bold;\">Receipt No</td><td style=\"padding:8px 12px;border:1px solid #ffe0b2;\">"
        f"{receipt_number}</td></tr></table>"
        f"<p style=\"background:#fff3e0;border-left:4px solid #FF9933;padding:12px 16px;border-radius:4px;\">"
        f"&#128591; Please contact the temple office to renew your seva {urgency} to ensure "
        f"uninterrupted blessings.</p></div>"
        f"<div style=\"background:#f5f5f5;padding:12px 24px;text-align:center;font-size:12px;color:#888;\">"
        f"This is an automated reminder from {temple_name} via MandirMitra.</div>"
        "</div></body></html>"
    )

    return html, text


# ---------------------------------------------------------------------------
# Temple name cache helper
# ---------------------------------------------------------------------------

_temple_name_cache: dict[str, str] = {}


async def _get_temple_name(tenant_id: str) -> str:
    if tenant_id in _temple_name_cache:
        return _temple_name_cache[tenant_id]
    doc = await get_collection(MANDIR_TEMPLES).find_one({"tenant_id": tenant_id}) or {}
    name = str(doc.get("temple_name") or doc.get("name") or "Temple")
    _temple_name_cache[tenant_id] = name
    return name


# ---------------------------------------------------------------------------
# Core reminder logic
# ---------------------------------------------------------------------------

async def run_reminders_once(
    *,
    tenant_id: str | None = None,
    seva_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Scan bookings for sevas with reminder_enabled=True and send email reminders.
    WhatsApp reminders are sent manually by admin via the frontend deep links.

    Args:
        tenant_id: Limit to a single tenant (None = all tenants).
        seva_id:   Limit to a single seva (None = all reminder-enabled sevas).
        force:     If True, re-send email even if reminder_sent_at is recent.
    """
    now = _now_utc()
    summary: dict[str, Any] = {
        "scanned": 0,
        "sent": 0,
        "email_ok": 0,
        "email_fail": 0,
        "skipped": 0,
        "errors": [],
    }

    sevas_col = get_collection(MANDIR_SEVAS)
    seva_filter: dict[str, Any] = {"reminder_enabled": True, "is_active": True}
    if tenant_id:
        seva_filter["tenant_id"] = tenant_id
    if seva_id:
        seva_filter["id"] = seva_id

    reminder_sevas = await sevas_col.find(seva_filter).to_list(length=500)
    if not reminder_sevas:
        return summary

    bookings_col = get_collection(MANDIR_SEVA_BOOKINGS)
    devotees_col = get_collection(MANDIR_DEVOTEES)

    for seva in reminder_sevas:
        sid = str(seva.get("id") or "")
        s_tenant = str(seva.get("tenant_id") or "")
        reminder_days = int(seva.get("reminder_days_before") or 30)
        seva_name = str(seva.get("name_english") or seva.get("name") or "Seva")

        window_end = now + timedelta(days=reminder_days)
        yesterday = now - timedelta(days=1)

        booking_filter: dict[str, Any] = {
            "tenant_id": s_tenant,
            "seva_id": sid,
            "expiry_date": {"$exists": True, "$ne": None},
            "status": {"$ne": "cancelled"},
        }

        bookings = await bookings_col.find(booking_filter).to_list(length=1000)

        for b in bookings:
            expiry_raw = str(b.get("expiry_date") or "")
            if not expiry_raw:
                continue

            try:
                expiry_dt = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00"))
                if expiry_dt.tzinfo is None:
                    expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            if not (yesterday <= expiry_dt <= window_end):
                continue

            days_left = max(0, (expiry_dt.date() - now.date()).days)
            summary["scanned"] += 1

            # Skip if already reminded recently (< 25 days), unless force=True
            if not force:
                last_sent = b.get("reminder_sent_at")
                if last_sent:
                    try:
                        last_dt = datetime.fromisoformat(str(last_sent).replace("Z", "+00:00"))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        if (now - last_dt).days < 25:
                            summary["skipped"] += 1
                            continue
                    except Exception:
                        pass  # parse error — send anyway

            devotee_name = str(b.get("devotee_name") or b.get("devotee_names") or "Devotee")
            devotee_email = str(b.get("devotee_email") or "").strip()
            amount = float(b.get("amount_paid") or b.get("amount") or 0.0)
            receipt_number = str(b.get("receipt_number") or b.get("id") or "")

            # Enrich from devotee record if needed
            dev_id = str(b.get("devotee_id") or "").strip()
            if dev_id and not devotee_email:
                dev_doc = await devotees_col.find_one({"id": dev_id, "tenant_id": s_tenant})
                if dev_doc:
                    devotee_email = str(dev_doc.get("email") or "").strip()

            temple_name = await _get_temple_name(s_tenant)
            expiry_label = expiry_dt.strftime("%d %b %Y")

            email_ok = False

            # Send email (automated)
            if devotee_email and "@" in devotee_email:
                html_body, text_body = _build_email_body(
                    devotee_name=devotee_name,
                    seva_name=seva_name,
                    temple_name=temple_name,
                    expiry_date=expiry_label,
                    amount=amount,
                    days_left=days_left,
                    receipt_number=receipt_number,
                )
                ok, err = await _send_reminder_email(
                    to_email=devotee_email,
                    subject=f"Seva Renewal Reminder — {seva_name} expires {expiry_label}",
                    html_body=html_body,
                    text_body=text_body,
                )
                if ok:
                    summary["email_ok"] += 1
                    email_ok = True
                else:
                    summary["email_fail"] += 1
                    logger.warning("Reminder email failed booking=%s: %s", b.get("id"), err)

            if email_ok:
                summary["sent"] += 1
                await bookings_col.update_one(
                    {"id": b["id"], "tenant_id": s_tenant},
                    {
                        "$set": {
                            "reminder_sent_at": now.isoformat(),
                            "updated_at": now.isoformat(),
                        },
                        "$inc": {"reminder_count": 1},
                    },
                )

    return summary


# ---------------------------------------------------------------------------
# Background worker loop
# ---------------------------------------------------------------------------

async def _worker_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            result = await run_reminders_once()
            logger.info(
                "Seva reminder run: scanned=%d sent=%d email_ok=%d skipped=%d",
                result["scanned"], result["sent"], result["email_ok"], result["skipped"],
            )
        except Exception:
            logger.exception("Seva reminder worker loop error")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=float(_POLL_SECONDS))
        except asyncio.TimeoutError:
            continue


async def start_seva_reminder_worker() -> bool:
    global _WORKER_TASK, _STOP_EVENT

    try:
        get_mongo_client()
    except Exception:
        logger.warning("Seva reminder worker skipped: MongoDB not initialized")
        return False

    if _WORKER_TASK and not _WORKER_TASK.done():
        return True

    _STOP_EVENT = asyncio.Event()
    _WORKER_TASK = asyncio.create_task(
        _worker_loop(_STOP_EVENT), name="seva-reminder-worker"
    )
    logger.info("Seva reminder worker started (poll every %ds)", _POLL_SECONDS)
    return True


async def stop_seva_reminder_worker() -> None:
    global _WORKER_TASK, _STOP_EVENT

    task = _WORKER_TASK
    stop_event = _STOP_EVENT
    _WORKER_TASK = None
    _STOP_EVENT = None

    if task is None:
        return
    if stop_event is not None:
        stop_event.set()
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.TimeoutError:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
