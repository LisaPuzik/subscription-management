import logging
import smtplib
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol

import anyio

from app.config import get_settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


class SmtpEmailSender:
    def _send(self, message: EmailMessage) -> None:
        settings = get_settings()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user and settings.smtp_password:
                smtp.login(settings.smtp_user, settings.smtp_password.get_secret_value())
            smtp.send_message(message)

    async def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = get_settings().smtp_sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            await anyio.to_thread.run_sync(self._send, message)
        except (OSError, smtplib.SMTPException) as exc:
            logger.error("Email delivery failed: %s", type(exc).__name__)


class ConsoleEmailSender:
    async def send(self, to: str, subject: str, body: str) -> None:
        print(f"--- email to {to}: {subject}\n{body}\n---", flush=True)


@lru_cache
def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.smtp_host:
        return SmtpEmailSender()
    if settings.environment == "production":
        raise RuntimeError("SMTP_HOST is required in production")
    return ConsoleEmailSender()


def link(path: str, token: str) -> str:
    return f"{get_settings().frontend_url.rstrip('/')}/{path}?token={token}"
