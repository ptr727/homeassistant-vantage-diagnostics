"""Tests for running requests on the stock connection."""

import asyncio
from typing import cast

from aiovantage import Vantage
from aiovantage.errors import ClientConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vantage_diagnostics.config_entry import stock_client_released
from custom_components.vantage_diagnostics.const import UNLOADED
from custom_components.vantage_diagnostics.rpc import SharedRpc
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import FakeClient, add_vantage_entry


def keep_open() -> None:
    """Stand in for closing a released connection, which these tests don't look at."""


async def test_cancelled_caller_leaves_the_request_running() -> None:
    """Test that cancelling the wait for a request does not cancel the request."""
    rpc = SharedRpc(lambda: True, keep_open)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def request() -> str:
        await release.wait()
        finished.set()
        return "reply"

    caller = asyncio.ensure_future(rpc.run(request))
    await asyncio.sleep(0)
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    assert not finished.is_set()
    release.set()
    await rpc.async_shutdown()
    assert finished.is_set()


async def test_shutdown_waits_for_requests_and_refuses_new_ones() -> None:
    """Test that a shutdown stops new requests and awaits those sent."""
    rpc = SharedRpc(lambda: True, keep_open)
    started = asyncio.Event()
    release = asyncio.Event()

    async def request() -> str:
        started.set()
        await release.wait()
        return "reply"

    caller = asyncio.ensure_future(rpc.run(request))
    await started.wait()
    shutdown = asyncio.ensure_future(rpc.async_shutdown())
    await asyncio.sleep(0)

    assert not shutdown.done()
    assert not rpc.usable
    with pytest.raises(ClientConnectionError, match=UNLOADED):
        await rpc.run(request)

    release.set()
    await shutdown
    assert await caller == "reply"


async def test_shutdown_gives_up_on_a_stuck_request(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that a request still running at the timeout is logged and left alone."""
    monkeypatch.setattr(
        "custom_components.vantage_diagnostics.rpc.SHUTDOWN_TIMEOUT", 0.01
    )
    caplog.set_level("DEBUG")
    rpc = SharedRpc(lambda: True, keep_open)
    started = asyncio.Event()
    stuck = asyncio.Event()

    async def request() -> None:
        started.set()
        await stuck.wait()

    caller = asyncio.ensure_future(rpc.run(request))
    await started.wait()
    await rpc.async_shutdown()

    assert "still running after shutdown" in caplog.text
    stuck.set()
    await caller


async def test_request_error_reaches_the_caller_only_once() -> None:
    """Test that a failing request raises to its caller."""
    rpc = SharedRpc(lambda: True, keep_open)

    async def request() -> None:
        raise ValueError("Bad reply")

    with pytest.raises(ValueError, match="Bad reply"):
        await rpc.run(request)


async def test_unusable_when_the_stock_client_is_gone() -> None:
    """Test that a request is refused once the stock client may no longer be used."""
    usable = True
    rpc = SharedRpc(lambda: usable, keep_open)
    usable = False

    async def request() -> None:
        raise AssertionError("must not be sent")

    assert not rpc.usable
    with pytest.raises(ClientConnectionError, match=UNLOADED):
        await rpc.run(request)


async def test_request_not_sent_once_unusable_before_it_starts() -> None:
    """Test that a request is not sent once the stock client became unusable after run() was called."""
    usable = True
    rpc = SharedRpc(lambda: usable, keep_open)
    sent = False

    async def request() -> None:
        nonlocal sent
        sent = True

    # The caller's first step schedules the request's task, which runs after this test resumes
    caller = asyncio.ensure_future(rpc.run(request))
    await asyncio.sleep(0)
    usable = False

    with pytest.raises(ClientConnectionError, match=UNLOADED):
        await caller
    assert not sent


async def test_release_checked_after_each_request() -> None:
    """Test that every request sent, whether it succeeds or fails, checks for a released connection."""
    checked = 0

    def close_if_released() -> None:
        nonlocal checked
        checked += 1

    rpc = SharedRpc(lambda: True, close_if_released)

    async def request() -> str:
        return "reply"

    async def failing() -> None:
        raise ValueError("Bad reply")

    assert await rpc.run(request) == "reply"
    with pytest.raises(ValueError, match="Bad reply"):
        await rpc.run(failing)
    assert checked == 2


@pytest.mark.parametrize(
    ("state", "held", "released"),
    [
        (ConfigEntryState.LOADED, True, False),
        (ConfigEntryState.FAILED_UNLOAD, True, False),
        (ConfigEntryState.UNLOAD_IN_PROGRESS, True, True),
        (ConfigEntryState.LOADED, False, True),
        (ConfigEntryState.NOT_LOADED, False, True),
    ],
)
async def test_stock_client_released(
    hass: HomeAssistant,
    client: FakeClient,
    state: ConfigEntryState,
    held: bool,
    released: bool,
) -> None:
    """Test when the stock integration has stopped using a client."""
    entry = add_vantage_entry(hass, client if held else FakeClient(), state)

    assert stock_client_released(entry, cast(Vantage, client)) is released


async def test_stock_client_released_once_the_entry_is_removed(
    client: FakeClient,
) -> None:
    """Test that a removed stock entry no longer uses its client."""
    assert stock_client_released(None, cast(Vantage, client))


async def test_request_outlasting_the_stock_client_closes_its_connection(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    vantage_entry: MockConfigEntry,
    client: FakeClient,
) -> None:
    """Test that a request ending once the stock entry started unloading closes the client's connection."""
    rpc = init_integration.runtime_data.rpc
    started = asyncio.Event()
    release = asyncio.Event()

    async def request() -> str:
        started.set()
        await release.wait()
        return "reply"

    # Stands in for a request reconnecting while the stock unload closes the client
    caller = hass.async_create_task(rpc.run(request))
    await started.wait()
    vantage_entry.mock_state(hass, ConfigEntryState.UNLOAD_IN_PROGRESS)
    release.set()

    assert await caller == "reply"
    client.config_client.close.assert_called_once_with()


async def test_request_leaves_the_connection_open_while_the_stock_client_is_in_use(
    init_integration: MockConfigEntry, client: FakeClient
) -> None:
    """Test that a request leaves the connection alone while the stock entry still uses its client."""
    rpc = init_integration.runtime_data.rpc

    async def request() -> str:
        return "reply"

    assert await rpc.run(request) == "reply"
    client.config_client.close.assert_not_called()


async def test_connection_left_open_when_only_this_runner_shuts_down(
    init_integration: MockConfigEntry, client: FakeClient
) -> None:
    """Test that shutting this runner down leaves the stock client's connection alone."""
    rpc = init_integration.runtime_data.rpc
    started = asyncio.Event()
    release = asyncio.Event()

    async def request() -> str:
        started.set()
        await release.wait()
        return "reply"

    caller = asyncio.ensure_future(rpc.run(request))
    await started.wait()
    shutdown = asyncio.ensure_future(rpc.async_shutdown())
    await asyncio.sleep(0)
    assert not rpc.usable
    release.set()

    await shutdown
    assert await caller == "reply"
    client.config_client.close.assert_not_called()


async def test_unload_awaits_a_request_in_flight(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test that an entry unload waits for a request already sent."""
    rpc = init_integration.runtime_data.rpc
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def request() -> None:
        started.set()
        await release.wait()
        finished.set()

    caller = hass.async_create_task(rpc.run(request))
    await started.wait()
    unload = hass.async_create_task(
        hass.config_entries.async_unload(init_integration.entry_id)
    )
    await asyncio.sleep(0.05)

    assert not unload.done()
    assert not finished.is_set()

    release.set()
    assert await unload
    await caller
    assert finished.is_set()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
