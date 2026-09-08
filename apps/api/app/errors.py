from __future__ import annotations


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def error_body(code: str, message: str, request_id: str | None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
