"""Tests for running requests on the stock connection."""

import asyncio

from aiovantage.errors import ClientConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vantage_diagnostics.const import UNLOADED
from custom_components.vantage_diagnostics.rpc import SharedRpc
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant


async def test_cancelled_caller_leaves_the_request_running() -> None:
    """Test that cancelling the wait for a request does not cancel the request."""
    rpc = SharedRpc(lambda: True)
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
    rpc = SharedRpc(lambda: True)
    release = asyncio.Event()

    async def request() -> str:
        await release.wait()
        return "reply"

    caller = asyncio.ensure_future(rpc.run(request))
    await asyncio.sleep(0)
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
    rpc = SharedRpc(lambda: True)
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
    rpc = SharedRpc(lambda: True)

    async def request() -> None:
        raise ValueError("Bad reply")

    with pytest.raises(ValueError, match="Bad reply"):
        await rpc.run(request)


async def test_unusable_when_the_stock_client_is_gone() -> None:
    """Test that a request is refused once the stock client may no longer be used."""
    usable = True
    rpc = SharedRpc(lambda: usable)
    usable = False

    async def request() -> None:
        raise AssertionError("must not be sent")

    assert not rpc.usable
    with pytest.raises(ClientConnectionError, match=UNLOADED):
        await rpc.run(request)


async def test_request_not_sent_once_unusable_before_it_starts() -> None:
    """Test that a request admitted while usable is not sent once that has changed."""
    usable = True
    rpc = SharedRpc(lambda: usable)
    sent = False

    async def request() -> None:
        nonlocal sent
        sent = True

    # The caller's first step admits the request and schedules its task, which runs after this test resumes
    caller = asyncio.ensure_future(rpc.run(request))
    await asyncio.sleep(0)
    usable = False

    with pytest.raises(ClientConnectionError, match=UNLOADED):
        await caller
    assert not sent


async def test_unload_awaits_a_request_in_flight(
    hass: HomeAssistant, init_integration: MockConfigEntry
) -> None:
    """Test that an entry unload waits for a request already sent."""
    rpc = init_integration.runtime_data.rpc
    release = asyncio.Event()
    finished = asyncio.Event()

    async def request() -> None:
        await release.wait()
        finished.set()

    caller = hass.async_create_task(rpc.run(request))
    await asyncio.sleep(0)
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
