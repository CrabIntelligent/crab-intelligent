from __future__ import annotations


class ApiError(Exception):
    """An error the API returns to the caller as {"error": message} with an HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status
