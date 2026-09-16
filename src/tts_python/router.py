from __future__ import annotations
import logging
from typing import Protocol, TypeVar


class Provider(Protocol):
    is_cloud: bool


T = TypeVar("T", bound=Provider)


class PrivacyError(RuntimeError):
    pass


def candidates(preferred: str, fallback: str, providers: dict[str, T], private: bool) -> list[T]:
    names = [preferred] + ([] if fallback == "none" or fallback == preferred else [fallback])
    result = []
    for name in names:
        provider = providers.get(name)
        if provider is None:
            continue
        if private and provider.is_cloud:
            continue
        result.append(provider)
    if private and not result:
        raise PrivacyError("private jobs require a configured local provider; cloud fallback is disabled")
    return result


def execute_with_fallback(options: list[T], operation, logger: logging.Logger):
    if not options:
        raise RuntimeError("no provider is configured")
    last = None
    for index, provider in enumerate(options):
        try:
            return operation(provider)
        except Exception as exc:
            last = exc
            if index + 1 == len(options):
                raise
            logger.warning("%s unavailable (%s). Falling back to %s.", type(provider).__name__, exc,
                           type(options[index + 1]).__name__)
    raise last or RuntimeError("provider routing failed")
