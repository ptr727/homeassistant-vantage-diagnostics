"""The service that reads which modules respond on each enclosure bus."""

import probatio as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.util.json import JsonValueType

from .config_entry import VantageDiagnosticsConfigEntry
from .const import SERVICE_GET_MODULE_STATUS
from .diagnostic_services import ReadError, async_register_response_service
from .module_status import read_module_status


async def _read_module_status(
    entry: VantageDiagnosticsConfigEntry, call: ServiceCall, errors: list[ReadError]
) -> list[JsonValueType]:
    vantage = entry.runtime_data.client
    runtime_data = entry.runtime_data
    status = await read_module_status(
        vantage, runtime_data.object_names, runtime_data.rpc
    )
    errors.extend(
        {"host": vantage.host, "enclosure": vid, "error": error}
        for vid, error in status.failed.items()
    )

    modules: list[JsonValueType] = []
    for module in vantage.modules:
        info = status.modules.get(module.vid)
        modules.append(
            {
                "host": vantage.host,
                "module_id": module.vid,
                "module_name": module.name,
                # None when the module's enclosure couldn't be read
                "online": status.is_online(module),
                "address": info.address if info else None,
                "model": info.model if info else None,
                "version": info.version if info else None,
                "status": [info.status.proc0, info.status.proc1]
                if info and info.status
                else None,
                "errors": info.errors if info else None,
            }
        )

    return modules


def async_register_module_status_service(hass: HomeAssistant) -> None:
    """Register the response-only service that reads the module status."""
    async_register_response_service(
        hass,
        SERVICE_GET_MODULE_STATUS,
        vol.Schema({}),
        "modules",
        _read_module_status,
        "get_module_status_failed",
    )
