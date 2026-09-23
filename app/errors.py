"""Stable application errors used by services and HTTP handlers."""

from __future__ import annotations


class AppError(Exception):
    """An expected failure with a stable public error contract."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field
        self.retryable = retryable
