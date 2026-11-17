"""Tests for the diagnostic services."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from aiovantage.errors import ClientConnectionError, ClientResponseError
import probatio as vol
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vantage_diagnostics.aiovantage_diag import (
    EventLogEntry,
    ModuleInfo,
    SysLogEntry,
    SysLogType,
    TriggerType,
)
from custom_components.vantage_diagnostics.const import (
    DOMAIN,
    SERVICE_GET_MODULE_STATUS,
    SERVICE_GET_SYSTEM_LOG,
    SERVICE_GET_TRIGGER_HISTORY,
    UNLOADED,
)
from custom_components.vantage_diagnostics.diagnostic_services import format_errors
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import (
    BUTTON_VID,
    ENCLOSURE_VID,
    HOST,
    MODULE_VID,
    TASK_VID,
    FakeClient,
    add_vantage_entry,
)

pytestmark = pytest.mark.usefixtures("init_integration")


@pytest.fixture(autouse=True)
def masters(client: FakeClient) -> None:
    """Connect to master 1, with peers 2 and 3."""
    client.peers = [2, 3]


def by_master(
    logs: dict[int | None, list[Any] | Exception],
) -> Callable[..., Awaitable[list[Any]]]:
    """Return a log reader answering with each master's log, or raising its error."""

    async def read(client: Any, master: int | None = None) -> list[Any]:
        log = logs[master]
        if isinstance(log, Exception):
            raise log
        return log

    return read


async def test_get_trigger_history(hass: HomeAssistant) -> None:
    """Test reading the trigger history of each master."""
    button_press = EventLogEntry(
        time_stamp="2026-01-02T03:04:05",
        source=BUTTON_VID,
        destination=TASK_VID,
        event=TriggerType.BUTTON_DOWN,
        param1=0,
        param2=0,
    )
    logs: dict[int | None, list[Any] | Exception] = {
        None: [replace(button_press, time_stamp="2026-01-01T00:00:00"), button_press],
        2: [replace(button_press, event=99)],
        3: [],
    }

    with patch(
        "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_event_log",
        side_effect=by_master(logs),
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_TRIGGER_HISTORY,
            {"limit": 1},
            blocking=True,
            return_response=True,
        )

    entry = {
        "time": "2026-01-02T03:04:05",
        "host": HOST,
        "master": 1,
        "trigger_type": "button_down",
        "event": TriggerType.BUTTON_DOWN,
        "source_id": BUTTON_VID,
        "source_name": "Hall Button",
        "source_path": ["Hall Button", "Hall Keypad", "Controller 1"],
        "task_id": TASK_VID,
        "task_name": "Lamp",
        "param1": 0,
        "param2": 0,
    }
    assert response == {
        "entries": [
            entry,
            entry | {"master": 2, "trigger_type": "unknown_99", "event": 99},
        ],
        "errors": [],
    }


async def test_get_system_log(hass: HomeAssistant) -> None:
    """Test reading the system log, with a peer that answers with an error."""
    warning = SysLogEntry(
        type=SysLogType.WARNING, time="01-02T03:04:05", src="Controller", msg="Hot "
    )
    logs: dict[int | None, list[Any] | Exception] = {
        None: [warning],
        2: ClientResponseError("Not available"),
        3: [replace(warning, type=42, msg=None)],
    }

    with patch(
        "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_sys_log",
        side_effect=by_master(logs),
    ):
        response = await hass.services.async_call(
            DOMAIN, SERVICE_GET_SYSTEM_LOG, blocking=True, return_response=True
        )

    entry = {
        "time": "01-02T03:04:05",
        "host": HOST,
        "master": 1,
        "type": "warning",
        "source": "Controller",
        "message": "Hot",
    }
    assert response == {
        "entries": [entry, entry | {"master": 3, "type": "unknown_42", "message": ""}],
        "errors": [{"host": HOST, "master": 2, "error": "Not available"}],
    }


async def test_get_system_log_connection_error_stops_reading(
    hass: HomeAssistant,
) -> None:
    """Test that a connection error stops reading, and names the masters not read."""
    logs: dict[int | None, list[Any] | Exception] = {
        None: [SysLogEntry(type=SysLogType.ERROR, time="01-02T03:04:05", msg="Down")],
        2: ClientConnectionError(),
        3: [],
    }

    with patch(
        "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_sys_log",
        side_effect=by_master(logs),
    ) as get_sys_log:
        response = await hass.services.async_call(
            DOMAIN, SERVICE_GET_SYSTEM_LOG, blocking=True, return_response=True
        )

    assert get_sys_log.await_count == 2
    assert response
    assert len(cast(list[Any], response["entries"])) == 1
    assert response["errors"] == [
        {"host": HOST, "master": 2, "error": "ClientConnectionError"},
        {"host": HOST, "master": 3, "error": "Not read after master 2 failed"},
    ]


@pytest.mark.parametrize(
    ("service", "method", "error_match"),
    [
        (
            SERVICE_GET_TRIGGER_HISTORY,
            "get_event_log",
            "Failed to read the trigger history: ",
        ),
        (SERVICE_GET_SYSTEM_LOG, "get_sys_log", "Failed to read the system log: "),
    ],
)
async def test_log_service_fails_when_no_master_can_be_read(
    hass: HomeAssistant, service: str, method: str, error_match: str
) -> None:
    """Test that a log service fails, with a translated error, when nothing is read."""
    with (
        patch(
            f"custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.{method}",
            side_effect=ClientConnectionError("Connection refused"),
        ),
        pytest.raises(HomeAssistantError, match=error_match) as err,
    ):
        await hass.services.async_call(
            DOMAIN, service, blocking=True, return_response=True
        )

    assert err.value.translation_domain == DOMAIN
    assert str(err.value) == (
        f"{error_match}master 1: Connection refused; "
        "master 2: Not read after master 1 failed; "
        "master 3: Not read after master 1 failed"
    )


@pytest.mark.parametrize(
    "service", [SERVICE_GET_TRIGGER_HISTORY, SERVICE_GET_SYSTEM_LOG]
)
@pytest.mark.parametrize("limit", [0, 5001, "many"])
async def test_invalid_limit(hass: HomeAssistant, service: str, limit: Any) -> None:
    """Test that a limit outside the range services.yaml offers is rejected."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, service, {"limit": limit}, blocking=True, return_response=True
        )


def test_format_errors() -> None:
    """Test that errors read as text, naming the controller when several failed."""
    assert format_errors([{"host": HOST, "master": 2, "error": "Timeout"}]) == (
        "master 2: Timeout"
    )
    assert (
        format_errors(
            [
                {"host": "192.0.2.10", "enclosure": 7, "error": "Timeout"},
                {"host": "192.0.2.11", "error": "ClientConnectionError"},
            ]
        )
        == "192.0.2.10 enclosure 7: Timeout; 192.0.2.11: ClientConnectionError"
    )


async def test_unload_during_read_reports_masters_not_read(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test that the masters not yet read when the entry unloads are reported."""
    rpc = init_integration.runtime_data.rpc

    async def unload_during_read(client: Any, master: int | None = None) -> list[Any]:
        rpc.close()
        return []

    with (
        patch(
            "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_event_log",
            side_effect=unload_during_read,
        ) as get_event_log,
        pytest.raises(HomeAssistantError, match=UNLOADED),
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_GET_TRIGGER_HISTORY, blocking=True, return_response=True
        )

    get_event_log.assert_awaited_once()


async def test_unload_during_system_log_read(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test that the system log service reports masters not read after an unload."""
    rpc = init_integration.runtime_data.rpc

    async def unload_during_read(client: Any, master: int | None = None) -> list[Any]:
        rpc.close()
        return []

    with (
        patch(
            "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_sys_log",
            side_effect=unload_during_read,
        ),
        pytest.raises(HomeAssistantError, match=UNLOADED),
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_GET_SYSTEM_LOG, blocking=True, return_response=True
        )


async def test_get_module_status(
    hass: HomeAssistant, client: FakeClient, modules_read: AsyncMock
) -> None:
    """Test reading the modules on each enclosure's bus."""
    modules_read.return_value = [
        ModuleInfo(
            address=1,
            vid=MODULE_VID,
            model="Module",
            revision=3,
            version="1.0",
            status=ModuleInfo.Status(proc0="Running", proc1="Running"),
            errors=2,
        ),
        ModuleInfo(address=2),
    ]

    response = await hass.services.async_call(
        DOMAIN, SERVICE_GET_MODULE_STATUS, blocking=True, return_response=True
    )

    # The enclosure is addressed by its position, on the connected master
    modules_read.assert_awaited_with(client.config_client, 1, master=None)
    assert response == {
        "modules": [
            {
                "host": HOST,
                "module_id": MODULE_VID,
                "module_name": "Module 1",
                "online": True,
                "address": 1,
                "model": "Module",
                "revision": 3,
                "version": "1.0",
                "status": ["Running", "Running"],
                "errors": 2,
            }
        ],
        "errors": [],
    }


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (ClientConnectionError(), "ClientConnectionError"),
        (ValueError("Bad reply"), "Bad reply"),
    ],
)
async def test_get_module_status_error(
    hass: HomeAssistant,
    modules_read: AsyncMock,
    side_effect: Exception,
    error: str,
) -> None:
    """Test that an enclosure that can't be read is reported, its modules unknown."""
    modules_read.side_effect = side_effect

    response = await hass.services.async_call(
        DOMAIN, SERVICE_GET_MODULE_STATUS, blocking=True, return_response=True
    )

    assert response
    assert response["errors"] == [
        {"host": HOST, "enclosure": ENCLOSURE_VID, "error": error}
    ]
    modules = cast(list[dict[str, Any]], response["modules"])
    assert [module["online"] for module in modules] == [None]


async def test_module_status_failure_raises_translated_error(
    hass: HomeAssistant,
) -> None:
    """Test that a reader that raises, with nothing else read, fails the service."""
    with (
        patch(
            "custom_components.vantage_diagnostics.module_status_service.read_module_status",
            side_effect=ValueError("Bad reply"),
        ),
        pytest.raises(HomeAssistantError, match="Bad reply") as err,
    ):
        await hass.services.async_call(
            DOMAIN, SERVICE_GET_MODULE_STATUS, blocking=True, return_response=True
        )

    assert err.value.translation_key == "get_module_status_failed"


async def test_two_controllers_are_merged(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test that a call reads every loaded companion entry, like the fork did."""
    other_client = FakeClient()
    other_client.host = "192.0.2.11"
    other_stock = add_vantage_entry(hass, other_client)
    second = MockConfigEntry(
        domain=DOMAIN,
        data={"vantage_entry_id": other_stock.entry_id},
        unique_id=other_stock.entry_id,
    )
    second.add_to_hass(hass)
    with patch("custom_components.vantage_diagnostics.EventStream") as stream_cls:
        stream_cls.return_value = type(
            "S",
            (),
            {
                "start": AsyncMock(
                    return_value=type("C", (), {"supports_enhanced_log": False})()
                ),
                "stop": lambda self: None,
                "subscribe": lambda self, *a: lambda: None,
            },
        )()
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()

    with patch(
        "custom_components.vantage_diagnostics.diagnostic_services.DiagnosticInterface.get_sys_log",
        return_value=[
            SysLogEntry(type=SysLogType.ERROR, time="01-02T03:04:05", msg="Down")
        ],
    ):
        response = await hass.services.async_call(
            DOMAIN, SERVICE_GET_SYSTEM_LOG, blocking=True, return_response=True
        )

    assert response
    entries = cast(list[dict[str, Any]], response["entries"])
    assert {entry["host"] for entry in entries} == {HOST, "192.0.2.11"}
    assert response["errors"] == []
