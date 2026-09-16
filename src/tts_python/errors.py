from __future__ import annotations

import subprocess


PERMANENT_NAMES = {"AuthenticationError", "PermissionDeniedError", "BadRequestError", "NotFoundError"}
TRANSIENT_NAMES = {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"}


def is_retryable(exc: Exception) -> bool:
    """Classify job failures using the mature converter's retry distinctions."""
    status = getattr(exc, "status_code", None)
    message = str(getattr(exc, "body", exc)).lower()
    if status in {401, 403, 404}:
        return False
    if status == 429 and any(term in message for term in
                             ("insufficient_quota", "billing_hard_limit", "current quota")):
        return False
    if status in {408, 409, 429} or isinstance(status, int) and status >= 500:
        return True
    if type(exc).__name__ in PERMANENT_NAMES:
        return False
    if type(exc).__name__ in TRANSIENT_NAMES:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, subprocess.SubprocessError)):
        return True
    # Input/configuration/format errors are deterministic unless a provider marks
    # its own exception retryable.
    return bool(getattr(exc, "retryable", False))
