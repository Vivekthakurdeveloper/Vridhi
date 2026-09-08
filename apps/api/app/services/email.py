from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    to: str
    subject: str
    text: str


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: EmailMessage) -> None:
        # Phase 1 default: log-only. Never log secrets/tokens in production beyond URL path.
        if self.settings.email_provider == "log":
            logger.info(
                "email.send",
                extra={
                    "operation": "email.send",
                    "to": message.to,
                    "subject": message.subject,
                },
            )
            logger.info("email.body %s", message.text)
            return
        # SMTP can be wired later without changing callers.
        raise NotImplementedError("SMTP email provider is not configured yet.")
