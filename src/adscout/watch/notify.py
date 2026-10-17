"""Desktop notifications through the optional ``desktop-notifier`` package (extra ``notify``).

Without the package every call is a no-op; a failing notification is logged, never raised.
"""

import importlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

APP_NAME = "AdScout"
INSTALL_HINT = "Install with: uv sync --extra notify"


def _module() -> Any | None:
    try:
        return importlib.import_module("desktop_notifier")
    except ImportError:
        return None


def available() -> bool:
    """Whether desktop notifications can be shown (the optional package is installed)."""
    return _module() is not None


async def notify(title: str, body: str) -> None:
    """Show a desktop notification, or do nothing when the package is missing."""
    module = _module()
    if module is None:
        return
    try:
        notifier = module.DesktopNotifier(app_name=APP_NAME)
        await notifier.send(title=title, message=body)
    except Exception:
        logger.warning("Could not show a desktop notification", exc_info=True)
