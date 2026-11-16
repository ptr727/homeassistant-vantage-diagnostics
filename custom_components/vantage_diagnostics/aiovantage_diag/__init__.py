"""Diagnostic protocol code that aiovantage does not release yet.

Each group prefers aiovantage's own copy, for when upstream merges the pull requests.
Otherwise it falls back to the vendored copy in this package.
"""

from typing import TYPE_CHECKING

# Type check against the vendored copies, which runtime imports mirror once released
if TYPE_CHECKING:
    from .diagnostic import DiagnosticInterface, EventLogEntry, ModuleInfo, SysLogEntry
    from .events import (
        SystemLogReceived,
        TriggerReceived,
        subscribe_system_log,
        subscribe_triggers,
    )
    from .log_types import SysLogType, TriggerType
else:
    try:
        from aiovantage.config_client import (
            DiagnosticInterface,
            EventLogEntry,
            ModuleInfo,
            SysLogEntry,
        )
    except ImportError:
        from .diagnostic import (
            DiagnosticInterface,
            EventLogEntry,
            ModuleInfo,
            SysLogEntry,
        )

    try:
        from aiovantage.config_client import SysLogType, TriggerType
    except ImportError:
        from .log_types import SysLogType, TriggerType

    try:
        from aiovantage.events import SystemLogReceived, TriggerReceived
    except ImportError:
        from .events import SystemLogReceived, TriggerReceived

    from .events import subscribe_system_log, subscribe_triggers

__all__ = [
    "DiagnosticInterface",
    "EventLogEntry",
    "ModuleInfo",
    "SysLogEntry",
    "SysLogType",
    "SystemLogReceived",
    "TriggerReceived",
    "TriggerType",
    "subscribe_system_log",
    "subscribe_triggers",
]
