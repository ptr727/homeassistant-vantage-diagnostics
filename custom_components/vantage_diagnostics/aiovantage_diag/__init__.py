"""The diagnostic protocol code that aiovantage does not release yet, vendored here.

Everything is imported from the vendored copies, never from aiovantage.
The stock integration installs its own aiovantage, so a copy of upstream's code could be absent or differ.
Switching to upstream's code is a deliberate, tested change, made once the stock integration's pin includes the merged pull requests.
"""

from .diagnostic import DiagnosticInterface, EventLogEntry, ModuleInfo, SysLogEntry
from .events import (
    SystemLogReceived,
    TriggerReceived,
    subscribe_system_log,
    subscribe_triggers,
)
from .log_types import SysLogType, TriggerType

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
