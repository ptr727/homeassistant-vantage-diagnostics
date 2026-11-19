"""Fixtures for the Vantage Diagnostics integration tests."""

from collections import defaultdict
from collections.abc import Callable, Generator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from xsdata.formats.dataclass.models.generics import AnyElement

from custom_components.vantage_diagnostics.const import (
    CONF_VANTAGE_ENTRY_ID,
    DOMAIN,
    VANTAGE_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_SSL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

HOST = "192.0.2.10"
PEER = "192.0.2.20"
MODULE_VID = 102
ENCLOSURE_VID = 201
BUTTON_VID = 110
KEYPAD_VID = 111
CONTROLLER_VID = 112
TASK_VID = 120
MASTER_VID = 130


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


def patch_enabled_default(enabled: bool) -> Any:
    """Patch whether every entity starts enabled, whatever its own default."""
    return patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=enabled,
        new_callable=PropertyMock,
    )


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable every entity, including those disabled by default."""
    with patch_enabled_default(True):
        yield


@pytest.fixture
def entity_registry_disabled_by_default() -> Generator[None]:
    """Disable every entity, including those enabled by default, so nothing reads the bus."""
    with patch_enabled_default(False):
        yield


class FakeController:
    """A stand-in for an aiovantage controller, iterable and subscribable."""

    def __init__(self, objects: list[Any]) -> None:
        """Hold the objects."""
        self.objects = objects
        self.subscribers: list[tuple[type, Callable[[Any], None]]] = []

    def __iter__(self) -> Any:
        """Iterate the objects."""
        return iter(self.objects)

    def subscribe(self, event_type: type, callback: Callable[[Any], None]) -> MagicMock:
        """Record a subscription."""
        self.subscribers.append((event_type, callback))
        return MagicMock()

    def fire(self, event: Any) -> None:
        """Pass an event to the subscribers of its type."""
        for event_type, callback in self.subscribers:
            if isinstance(event, event_type):
                callback(event)


def make_module(vid: int = MODULE_VID, name: str = "Module 1") -> SimpleNamespace:
    """Return a module mounted in the enclosure, on master 1."""
    return SimpleNamespace(
        vid=vid,
        name=name,
        master=1,
        parent=SimpleNamespace(vid=ENCLOSURE_VID, position=1),
    )


class FakeClient:
    """A stand-in for the stock integration's aiovantage client."""

    def __init__(self) -> None:
        """Build a small constructed project."""
        self.host = HOST
        self.peers: list[int] = []
        self.master_number: int | None = 1
        self.config_client = MagicMock(name="config_client")
        self.modules = FakeController([make_module()])
        self.masters = FakeController(
            [SimpleNamespace(vid=MASTER_VID, name="Controller", number=1)]
        )
        self.objects = {
            MODULE_VID: self.modules.objects[0],
            BUTTON_VID: SimpleNamespace(
                vid=BUTTON_VID,
                name="Hall Button",
                parent=SimpleNamespace(vid=KEYPAD_VID, position=1),
            ),
            KEYPAD_VID: SimpleNamespace(
                vid=KEYPAD_VID,
                name="Hall Keypad",
                parent=SimpleNamespace(vid=CONTROLLER_VID, position=1),
            ),
            CONTROLLER_VID: SimpleNamespace(
                vid=CONTROLLER_VID, name="Controller 1", parent=None
            ),
            TASK_VID: SimpleNamespace(vid=TASK_VID, name="Lamp", parent=None),
        }

    def __contains__(self, vid: int) -> bool:
        """Return whether the project has the object."""
        return vid in self.objects

    def __getitem__(self, vid: int) -> Any:
        """Return an object."""
        return self.objects[vid]


class FakeWriter:
    """A stand-in for the stream writer of the event stream's socket."""

    def __init__(self, peername: Any = (PEER, 3010), closing: bool = False) -> None:
        """Hold the peer the socket connects to."""
        self.peername = peername
        self.closing = closing

    def is_closing(self) -> bool:
        """Return whether the socket is closing."""
        return self.closing

    def get_extra_info(self, name: str) -> Any:
        """Return the socket's peer name, the only extra info the integration reads."""
        assert name == "peername"
        return self.peername


class FakeConnection:
    """A stand-in for the event stream's connection, which the stream reopens on a reconnect."""

    def __init__(self, supports_enhanced_log: bool) -> None:
        """Connect the socket to the default peer."""
        self.supports_enhanced_log = supports_enhanced_log
        self._writer: FakeWriter | None = FakeWriter()

    def reopen(self, writer: FakeWriter | None) -> None:
        """Replace the socket, or close it with None."""
        self._writer = writer


class FakeEventStream:
    """A stand-in for the companion's own EventStream."""

    supports_enhanced_log = True
    start_error: Exception | None = None
    instances: list[FakeEventStream] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record how it was built."""
        self.args = args
        self.kwargs = kwargs
        self.stop = MagicMock()
        self.log_subscriptions: list[tuple[Callable[[Any], None], tuple[str, ...]]] = []
        self.peers_seen: list[Any] = []
        self.subscribers: dict[type, list[Callable[[Any], None]]] = defaultdict(list)
        self.unsubscribe = MagicMock()
        self.connection = FakeConnection(self.supports_enhanced_log)
        FakeEventStream.instances.append(self)

    async def start(self) -> FakeConnection:
        """Pretend to connect."""
        if self.start_error is not None:
            raise self.start_error
        return self.connection

    def subscribe_enhanced_log(
        self, callback: Callable[[Any], None], *log_types: str, peers: Any = ()
    ) -> MagicMock:
        """Record an enhanced log subscription."""
        self.log_subscriptions.append((callback, log_types))
        self.peers_seen.append(list(peers))
        return self.unsubscribe

    def subscribe(self, event_type: type, callback: Callable[[Any], None]) -> MagicMock:
        """Record a connection event subscription."""
        self.subscribers[event_type].append(callback)
        return self.unsubscribe

    def emit_log(self, line: str) -> None:
        """Deliver an enhanced log line to every subscription, as the real stream does."""
        from aiovantage.events import EnhancedLogReceived  # noqa: PLC0415

        for callback, _types in self.log_subscriptions:
            callback(EnhancedLogReceived(line))

    def emit(self, event: Any) -> None:
        """Deliver a connection event."""
        for callback in self.subscribers[type(event)]:
            callback(event)


@pytest.fixture
def supports_enhanced_log() -> bool:
    """Whether the controller firmware has the enhanced log."""
    return True


@pytest.fixture
def stream_start_error() -> Exception | None:
    """An error the stream raises when it starts, if any."""
    return None


@pytest.fixture
def fake_stream(
    supports_enhanced_log: bool, stream_start_error: Exception | None
) -> Generator[type[FakeEventStream]]:
    """Replace the companion's EventStream with a fake."""
    FakeEventStream.instances = []
    FakeEventStream.supports_enhanced_log = supports_enhanced_log
    FakeEventStream.start_error = stream_start_error
    with patch("custom_components.vantage_diagnostics.EventStream", FakeEventStream):
        yield FakeEventStream


@pytest.fixture
def client() -> FakeClient:
    """Return the stock client."""
    return FakeClient()


def add_vantage_entry(
    hass: HomeAssistant,
    client: FakeClient,
    state: ConfigEntryState = ConfigEntryState.LOADED,
) -> MockConfigEntry:
    """Add a stock entry holding the client, and a device for each controller and module."""
    entry = MockConfigEntry(
        domain=VANTAGE_DOMAIN,
        title="Vantage InFusion",
        data={
            CONF_HOST: HOST,
            CONF_SSL: True,
            CONF_USERNAME: "user",
            CONF_PASSWORD: "secret",
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(client=client)
    entry.mock_state(hass, state)
    registry = dr.async_get(hass)
    for obj in [*client.masters, *client.modules]:
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(VANTAGE_DOMAIN, str(obj.vid))},
            name=obj.name,
        )
    return entry


@pytest.fixture
def vantage_entry(hass: HomeAssistant, client: FakeClient) -> MockConfigEntry:
    """Return a loaded stock entry."""
    return add_vantage_entry(hass, client)


@pytest.fixture
def config_entry(vantage_entry: MockConfigEntry) -> MockConfigEntry:
    """Return a companion entry following the stock entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Vantage InFusion Diagnostics",
        data={CONF_VANTAGE_ENTRY_ID: vantage_entry.entry_id},
        unique_id=vantage_entry.entry_id,
    )


@pytest.fixture(autouse=True)
def enclosure_lookup() -> Generator[AsyncMock]:
    """Answer the name lookup of the enclosure, the one object the client lacks."""
    enclosure = SimpleNamespace(
        vid=ENCLOSURE_VID,
        obj=AnyElement(
            qname="Enclosure",
            children=[
                AnyElement(qname="Name", text="Enclosure 1"),
                AnyElement(qname="Parent", text="1", attributes={"Position": "1"}),
            ],
        ),
    )
    with patch(
        "custom_components.vantage_diagnostics.names.ConfigurationInterface.get_object",
        return_value=[enclosure],
    ) as get_object:
        yield get_object


@pytest.fixture(autouse=True)
def modules_read() -> Generator[AsyncMock]:
    """Answer the bus read, where the module responds."""
    from custom_components.vantage_diagnostics.aiovantage_diag import ModuleInfo  # noqa: PLC0415

    with patch(
        "custom_components.vantage_diagnostics.module_status.DiagnosticInterface.get_modules",
        return_value=[ModuleInfo(address=1, vid=MODULE_VID, errors=0)],
    ) as get_modules:
        yield get_modules


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    fake_stream: type[FakeEventStream],
) -> MockConfigEntry:
    """Set up the companion entry."""
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry


@pytest.fixture
def forget_setup_lookup(
    init_integration: MockConfigEntry, enclosure_lookup: AsyncMock
) -> None:
    """Forget the enclosure lookup the bus read at setup made, so a test counts only its own."""
    enclosure_lookup.reset_mock()


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip setting up the entry a config flow creates."""
    with patch(
        "custom_components.vantage_diagnostics.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry
