from __future__ import annotations

import logging
import sys
import uuid
from typing import Any


class JsonFormatter(logging.Formatter):
    """Minimal structured JSON logs without logging document content."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone

        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "vridhi-api"),
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in (
            "request_id",
            "tenant_id",
            "user_id",
            "operation",
            "latency_ms",
            "status",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "info", service: str = "vridhi-api") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    logging.LoggerAdapter(root, {"service": service})


def new_request_id() -> str:
    return str(uuid.uuid4())
