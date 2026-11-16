"""Tests for resolving object names."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from aiovantage.errors import ClientConnectionError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("init_integration")


def timer(name: str) -> SimpleNamespace:
    """Return a lookup answer for a timer, an object the client lacks."""
    return SimpleNamespace(vid=70, obj=SimpleNamespace(name=name, parent=None))


async def test_clear_during_lookup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enclosure_lookup: AsyncMock,
) -> None:
    """Test that a lookup started before the names are cleared isn't cached."""
    names = init_integration.runtime_data.object_names
    answer = asyncio.Event()
    old, new = timer("Sunset"), timer("Dusk")
    replies = [old, new]

    async def lookup(*args: Any) -> list[Any]:
        reply = replies.pop(0)
        if reply is old:
            await answer.wait()
        return [reply]

    enclosure_lookup.side_effect = lookup

    # The controller is reprogrammed while the first lookup is in flight
    first = hass.async_create_task(names.name(70))
    await asyncio.sleep(0)
    names.clear()

    # A lookup after the clear doesn't share the one in flight
    assert await names.name(70) == "Dusk"

    # The first still answers its caller, but leaves the newer name cached
    answer.set()
    assert await first == "Sunset"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await names.name(70) == "Dusk"
    assert enclosure_lookup.await_count == 2


async def test_failed_lookup_names_by_vid_and_is_not_cached(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that a failed lookup falls back to the VID, and tries again next time."""
    names = init_integration.runtime_data.object_names
    enclosure_lookup.side_effect = ClientConnectionError("Refused")

    assert await names.name(70) == "VID 70"
    await asyncio.sleep(0)

    enclosure_lookup.side_effect = None
    enclosure_lookup.return_value = [timer("Sunset")]
    assert await names.name(70) == "Sunset"


async def test_unexpected_error_warns_once(
    init_integration: MockConfigEntry,
    enclosure_lookup: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that an error aiovantage doesn't wrap warns once per object."""
    names = init_integration.runtime_data.object_names
    enclosure_lookup.side_effect = ValueError("Bad XML")

    assert await names.name(70) == "VID 70"
    assert await names.name(70) == "VID 70"

    assert caplog.text.count("Failed to read object 70, naming it by VID") == 1


async def test_reply_for_another_object_is_ignored(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that a reply describing another object is not used for this one."""
    names = init_integration.runtime_data.object_names
    enclosure_lookup.return_value = [
        SimpleNamespace(vid=71, obj=SimpleNamespace(name="Other", parent=None))
    ]

    assert await names.name(70) == "VID 70"


async def test_empty_reply(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that a lookup answering no object names it by VID."""
    names = init_integration.runtime_data.object_names
    enclosure_lookup.return_value = []

    assert await names.name(70) == "VID 70"


async def test_lookup_after_close(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that nothing is looked up once the names are closed."""
    names = init_integration.runtime_data.object_names
    init_integration.runtime_data.rpc.close()

    assert await names.name(70) == "VID 70"
    enclosure_lookup.assert_not_awaited()


async def test_cancelled_caller_does_not_cancel_the_shared_lookup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enclosure_lookup: AsyncMock,
) -> None:
    """Test that cancelling one caller leaves the lookup the others share running."""
    names = init_integration.runtime_data.object_names
    answer = asyncio.Event()

    async def lookup(*args: Any) -> list[Any]:
        await answer.wait()
        return [timer("Sunset")]

    enclosure_lookup.side_effect = lookup
    first = asyncio.ensure_future(names.name(70))
    second = asyncio.ensure_future(names.name(70))
    await asyncio.sleep(0)

    first.cancel()
    answer.set()

    assert await second == "Sunset"
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_unload_cancels_pending_lookup(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    enclosure_lookup: AsyncMock,
) -> None:
    """Test that a caller waiting on a lookup the unload cancels gets the VID."""
    names = init_integration.runtime_data.object_names

    release = asyncio.Event()

    async def lookup_until_released(*args: Any) -> list[Any]:
        await release.wait()
        return []

    enclosure_lookup.side_effect = lookup_until_released
    waiting = asyncio.ensure_future(names.name(70))
    await asyncio.sleep(0)

    names.cancel()

    assert await waiting == "VID 70"

    # The request itself is never cancelled, so it ends only once released
    release.set()


async def test_typed_object_without_name(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Test that a typed object with no name is named by VID."""
    names = init_integration.runtime_data.object_names
    enclosure_lookup.return_value = [
        SimpleNamespace(vid=70, obj=SimpleNamespace(name="", parent=None))
    ]

    assert await names.name(70) == "VID 70"
