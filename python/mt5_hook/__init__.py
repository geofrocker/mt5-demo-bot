"""Local Python hook for a MetaTrader 5 demo account."""

from .client import HookClient, HookError

__all__ = ["HookClient", "HookError"]
__version__ = "1.0.0"
