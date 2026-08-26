"""Minimal SMTP helper for password-reset codes.

Configure on Render (or local) via:
  SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM (or MAIL_FROM), SMTP_USE_TLS (default 1).
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage


def smtp_configured() -> bool:
    return bool((os.getenv("SMTP_HOST") or "").strip())


def send_plain_email(*, to: str, subject: str, body: str) -> tuple[bool, str]:
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        return False, "SMTP_HOST is not configured"
    port = int((os.getenv("SMTP_PORT") or "587").strip() or "587")
    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD") or ""
    mail_from = (
        (os.getenv("SMTP_FROM") or os.getenv("MAIL_FROM") or user or "").strip()
    )
    if not mail_from:
        return False, "SMTP_FROM / MAIL_FROM is not configured"
    use_tls = (os.getenv("SMTP_USE_TLS") or "1").strip() not in {"0", "false", "False"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = to
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True, "sent"
    except Exception as exc:
        return False, str(exc)
