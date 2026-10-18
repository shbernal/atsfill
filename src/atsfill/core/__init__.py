"""The commands, loaded on first use so that adapters can import core's other modules."""

from typing import Any

__all__ = ["fill", "login", "logout", "read", "send", "status"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from atsfill.core import commands

    return getattr(commands, name)
