"""The service that returns each module's last fault report."""

import copy

import probatio as vol

from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.util.json import JsonValueType

from .config_entry import VantageDiagnosticsConfigEntry
from .const import SERVICE_GET_FAULT_REPORT
from .diagnostic_services import ReadError, async_register_response_service

ATTR_MODULE_ID = "module_id"

FAULT_REPORT_SCHEMA = vol.Schema({vol.Optional(ATTR_MODULE_ID): cv.positive_int})


async def _fault_reports(
    entry: VantageDiagnosticsConfigEntry, call: ServiceCall, errors: list[ReadError]
) -> list[JsonValueType]:
    # Kept by the integration, so nothing is read from the controller and nothing can fail
    tracker = entry.runtime_data.fault_tracker
    if (vid := call.data.get(ATTR_MODULE_ID)) is not None:
        report = tracker.report(vid)
        reports = [report] if report is not None else []
    else:
        reports = tracker.reports()

    # Copies, since a stored report goes on changing as its fault does
    return list[JsonValueType](copy.deepcopy(reports))


def async_register_fault_report_service(hass: HomeAssistant) -> None:
    """Register the response-only service that returns the last fault reports."""
    async_register_response_service(
        hass,
        SERVICE_GET_FAULT_REPORT,
        FAULT_REPORT_SCHEMA,
        "reports",
        _fault_reports,
        "get_fault_report_failed",
    )
