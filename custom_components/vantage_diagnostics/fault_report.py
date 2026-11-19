"""Build a report when a module faults, eg. when its breaker trips, and tag the triggers the fault causes."""

from collections import deque
from collections.abc import Sequence
import datetime as dt
from decimal import Decimal
from functools import partial
from typing import Any

from aiovantage import Vantage
from aiovantage.events import ObjectUpdated
from aiovantage.objects import Load, SystemObject

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .aiovantage_diag import EventLogEntry
from .const import (
    DOMAIN,
    EVENT_MODULE_FAULT,
    EVENT_MODULE_RECOVERED,
    LOGGER,
    SIGNAL_OFFLINE,
)
from .diagnostic_services import ReadError, read_trigger_history
from .module_status import ModuleStatusCoordinator

# A module off the bus this long starts a fault on its own.
# A program write re-announces the modules within a few seconds, and a breaker stays tripped until someone resets it.
OFFLINE_HOLD = 30.0

# A range trigger this soon after a fault started on its module is a side effect of the fault.
# The other signals of the same fault arrive within it too, and add to the fault rather than starting another.
FAULT_WINDOW = dt.timedelta(seconds=120)

# A back-in-range trigger this soon after its module joined or left the bus is a side effect of the recovery
CONNECTIVITY_WINDOW = dt.timedelta(seconds=300)

# The timeline covers this long before the fault, and a little after, since the controller's times are whole seconds
TIMELINE_WINDOW = dt.timedelta(minutes=30)
TIMELINE_SLACK = dt.timedelta(seconds=2)

# Keeps the event well under the recorder's 32 KB limit on event data
TIMELINE_MAX_ROWS = 60
MAX_SIDE_EFFECTS = 50

# What an unpowered module's sensor reports in place of a reading
NOT_REPORTED = Decimal("-0.999")

# A module's sensors sit at fixed positions: the temperature, then power and current for line A to D
TEMPERATURE_POSITION = 1
POWER_POSITIONS = range(2, 6)
CURRENT_POSITIONS = range(6, 10)
LINE_NAMES = "ABCD"

# How many consecutive channels each line feed powers, by the start of the module's model
CHANNELS_PER_FEED = {"SDM12": 3}

STORAGE_VERSION = 1
SAVE_DELAY = 1.0


def controller_time(text: str | None) -> dt.datetime | None:
    """Return a trigger history time, in the controller's local time, as UTC.

    The controller keeps no offset, so its clock is taken to be in Home Assistant's time zone.
    """
    if text is None:
        return None

    try:
        local = dt.datetime.fromisoformat(text)
    except ValueError:
        return None

    return dt_util.as_utc(local.replace(tzinfo=dt_util.get_default_time_zone()))


def reading(value: Decimal | None) -> float | None:
    """Return a sensor's level, or None where it is not reporting."""
    if value is None or value == NOT_REPORTED:
        return None

    return float(value)


def iso(value: dt.datetime | None) -> str | None:
    """Return a time as ISO 8601 text, or None."""
    return value.isoformat() if value is not None else None


def parent_vid(obj: object) -> int | None:
    """Return the VID of the object's parent."""
    return getattr(getattr(obj, "parent", None), "vid", None)


def position(obj: object) -> int | None:
    """Return the object's position under its parent, eg. a load's channel."""
    return getattr(getattr(obj, "parent", None), "position", None)


class FaultTracker:
    """Track module faults, keep each module's last fault report, and tell trip side effects from genuine readings."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry[Any],
        vantage: Vantage,
        module_status: ModuleStatusCoordinator,
    ) -> None:
        """Initialize the tracker."""
        self._hass = hass
        self._entry = entry
        self._vantage = vantage
        self._module_status = module_status
        self._store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.fault_reports.{entry.entry_id}"
        )

        # Each module's last fault report, keyed by VID
        self._reports: dict[int, dict[str, Any]] = {}

        # When each module last joined or left the bus, keyed by VID
        self._changed: dict[int, dt.datetime] = {}

        # Modules off the bus, keyed by VID, with the time they left and the hold that starts a fault
        self._holds: dict[int, tuple[dt.datetime, CALLBACK_TYPE]] = {}

        # Each module's load level changes within the timeline window, and when each load last turned on and off
        self._load_changes: dict[int, deque[tuple[dt.datetime, dict[str, Any]]]] = {}
        self._last_on: dict[int, dt.datetime] = {}
        self._last_off: dict[int, dt.datetime] = {}

    def signal_updated(self, vid: int) -> str:
        """Dispatcher signal sent when a module's fault report changes."""
        return f"{DOMAIN}_fault_report_{self._entry.entry_id}_{vid}"

    def report(self, vid: int) -> dict[str, Any] | None:
        """Return a module's last fault report."""
        return self._reports.get(vid)

    def reports(self) -> list[dict[str, Any]]:
        """Return each module's last fault report."""
        return list(self._reports.values())

    async def async_load(self) -> None:
        """Load the reports kept across a restart."""
        stored = await self._store.async_load() or {}
        self._reports = {int(vid): report for vid, report in stored.items()}

    @callback
    def async_start(self) -> None:
        """Follow the modules' connectivity and their loads' levels."""
        entry = self._entry
        entry.async_on_unload(
            self._module_status.async_add_transition_listener(self._on_transition)
        )
        entry.async_on_unload(
            self._vantage.loads.subscribe(ObjectUpdated, self._on_load_updated)
        )
        entry.async_on_unload(self._cancel_holds)
        entry.async_on_unload(self._async_flush)

    @callback
    def async_signal(
        self,
        vid: int,
        signal: str,
        line_feed: int | None = None,
        started: dt.datetime | None = None,
    ) -> None:
        """Start a fault on a module, unless one is already in progress."""
        if (module := self._module(vid)) is None:
            return

        now = dt_util.utcnow()
        if self._in_progress(vid, now):
            return

        started = started or now
        report = self._build(module, signal, line_feed, started)
        self._reports[vid] = report
        self._updated(vid)
        LOGGER.debug("Module %d fault started by %s", vid, signal)

        # Fired once the timeline is read, which takes a request to each master
        self._entry.async_create_background_task(
            self._hass, self._complete(vid, report), "vantage_module_fault"
        )

    @callback
    def async_classify(
        self, vid: int | None, trigger: dict[str, Any], *, out_of_range: bool
    ) -> bool:
        """Return whether a range trigger from a module's sensor is a side effect of a fault, and record it on the fault."""
        if vid is None or (module := self._module(vid)) is None:
            return False

        now = dt_util.utcnow()
        report = self._reports.get(vid)
        fault_recent = report is not None and now - self._time(report) <= FAULT_WINDOW
        if out_of_range:
            # A sensor that lost power reads out of range
            side_effect = (
                self._module_status.data.is_online(module) is False or fault_recent
            )
        else:
            # A sensor that regained power reads back in range
            changed = self._changed.get(vid)
            side_effect = fault_recent or (
                changed is not None and now - changed <= CONNECTIVITY_WINDOW
            )

        if side_effect and report is not None:
            side_effects: list[dict[str, Any]] = report["side_effects"]
            if len(side_effects) < MAX_SIDE_EFFECTS:
                side_effects.append(
                    {
                        "time": now.isoformat(),
                        "trigger_type": trigger["trigger_type"],
                        "source_id": trigger["source_id"],
                        "source_name": trigger["source_name"],
                    }
                )
                self._updated(vid)

        return side_effect

    def _module(self, vid: int) -> SystemObject | None:
        return next(
            (module for module in self._vantage.modules if module.vid == vid), None
        )

    def _time(self, report: dict[str, Any]) -> dt.datetime:
        return dt_util.parse_datetime(report["time"]) or dt_util.utcnow()

    def _in_progress(self, vid: int, now: dt.datetime) -> bool:
        """Return whether a module's last fault is still in progress, so a new signal adds to it."""
        if (report := self._reports.get(vid)) is None or report[
            "recovered"
        ] is not None:
            return False

        # Off the bus until it recovers, or still within the window of the signals one fault raises
        return report["offline"] is not None or now - self._time(report) <= FAULT_WINDOW

    @callback
    def _on_transition(
        self, module: SystemObject, previous: bool | None, online: bool
    ) -> None:
        vid = module.vid
        now = dt_util.utcnow()
        report = self._reports.get(vid)

        # The first known state after setup is no change, though it can show a module recovered while Home Assistant was down
        if previous is not None:
            self._changed[vid] = now

        if not online:
            if previous is None:
                return

            if report is not None and self._in_progress(vid, now):
                if report["offline"] is None:
                    report["offline"] = now.isoformat()
                    self._updated(vid)
                return

            # Off the bus alone starts a fault only once it outlasts the hold
            self._cancel_hold(vid)
            self._holds[vid] = (
                now,
                async_call_later(
                    self._hass, OFFLINE_HOLD, partial(self._hold_expired, vid)
                ),
            )
            return

        self._cancel_hold(vid)
        if (
            report is None
            or report["recovered"] is not None
            or report["offline"] is None
        ):
            return

        started = self._time(report)
        report["recovered"] = now.isoformat()
        report["outage"] = round((now - started).total_seconds(), 1)
        self._updated(vid)
        LOGGER.debug("Module %d recovered after %s s", vid, report["outage"])
        self._hass.bus.async_fire(
            EVENT_MODULE_RECOVERED,
            {
                "host": self._vantage.host,
                "module_id": vid,
                "module_name": report["module_name"],
                "time": report["time"],
                "recovered": report["recovered"],
                "outage": report["outage"],
            },
        )

    @callback
    def _hold_expired(self, vid: int, _now: dt.datetime) -> None:
        if (held := self._holds.pop(vid, None)) is None:
            return

        left, _cancel = held
        if (module := self._module(vid)) is None or self._module_status.data.is_online(
            module
        ) is not False:
            return

        self.async_signal(vid, SIGNAL_OFFLINE, started=left)
        if (report := self._reports.get(vid)) is not None and report["offline"] is None:
            report["offline"] = left.isoformat()
            self._updated(vid)

    def _cancel_hold(self, vid: int) -> None:
        if (held := self._holds.pop(vid, None)) is not None:
            held[1]()

    @callback
    def _cancel_holds(self) -> None:
        for vid in list(self._holds):
            self._cancel_hold(vid)

    @callback
    def _on_load_updated(self, event: ObjectUpdated[Load]) -> None:
        if "level" not in event.attrs_changed:
            return

        load = event.obj
        now = dt_util.utcnow()
        if load.level:
            self._last_on[load.vid] = now
        else:
            self._last_off[load.vid] = now

        if (vid := parent_vid(load)) is None or self._module(vid) is None:
            return

        changes = self._load_changes.setdefault(vid, deque())
        # The level as it changed, since the load's own level moves on
        changes.append(
            (
                now,
                {
                    "kind": "load",
                    "load_id": load.vid,
                    "name": load.name,
                    "level": float(load.level) if load.level is not None else None,
                },
            )
        )
        while changes and now - changes[0][0] > TIMELINE_WINDOW:
            changes.popleft()

    def _build(
        self,
        module: SystemObject,
        signal: str,
        line_feed: int | None,
        started: dt.datetime,
    ) -> dict[str, Any]:
        """Return a module's report, as it stands when the fault starts."""
        feeds: Sequence[Any] = getattr(module, "line_feed_table", None) or []
        feed_name = (
            getattr(feeds[line_feed - 1], "name", None)
            if line_feed is not None and 0 < line_feed <= len(feeds)
            else None
        )

        sensors = {
            position(sensor): sensor
            for sensor in self._vantage.omni_sensors
            if parent_vid(sensor) == module.vid
        }

        def level(index: int) -> float | None:
            sensor = sensors.get(index)
            return reading(getattr(sensor, "level", None))

        lines = [
            {
                "line_feed": number,
                "line": LINE_NAMES[number - 1],
                "name": getattr(feeds[number - 1], "name", None)
                if number <= len(feeds)
                else None,
                "power": level(power),
                "current": level(current),
            }
            for number, (power, current) in enumerate(
                zip(POWER_POSITIONS, CURRENT_POSITIONS, strict=True), start=1
            )
            if number <= len(feeds) or power in sensors or current in sensors
        ]

        per_feed = self._channels_per_feed(module)
        loads: list[dict[str, Any]] = []
        for load in self._vantage.loads:
            if parent_vid(load) != module.vid:
                continue

            channel = position(load)
            load_feed = (
                (channel - 1) // per_feed + 1
                if per_feed is not None and channel is not None
                else None
            )
            loads.append(
                {
                    "load_id": load.vid,
                    "name": load.name,
                    "channel": channel,
                    "line_feed": load_feed,
                    "on_missing_line": line_feed is not None and load_feed == line_feed,
                    "level": float(load.level) if load.level is not None else None,
                    "last_on": iso(self._last_on.get(load.vid)),
                    "last_off": iso(self._last_off.get(load.vid)),
                }
            )
        loads.sort(key=lambda load: (load["channel"] is None, load["channel"] or 0))

        return {
            "host": self._vantage.host,
            "module_id": module.vid,
            "module_name": module.name,
            "signal": signal,
            "time": started.isoformat(),
            "line_feed": line_feed,
            "line_feed_name": feed_name,
            "offline": None,
            "recovered": None,
            "outage": None,
            "temperature": level(TEMPERATURE_POSITION),
            "lines": lines,
            "loads": loads,
            "timeline": [],
            "side_effects": [],
        }

    def _channels_per_feed(self, module: SystemObject) -> int | None:
        """Return how many channels each of a module's line feeds powers, or None for a model this doesn't know."""
        info = self._module_status.last_info.get(module.vid)
        models = (getattr(module, "model", None), info.model if info else None)
        for model in models:
            for prefix, per_feed in CHANNELS_PER_FEED.items():
                if model and model.upper().replace("-", "").startswith(prefix):
                    return per_feed

        return None

    async def _complete(self, vid: int, report: dict[str, Any]) -> None:
        """Add the timeline to a report, then fire it."""
        started = self._time(report)
        try:
            report["timeline"] = await self._timeline(vid, started)
        except Exception:  # noqa: BLE001
            # Still fire the report, since the fault matters more than its timeline
            LOGGER.exception("Failed to read the timeline of module %d's fault", vid)

        self._updated(vid)
        self._hass.bus.async_fire(EVENT_MODULE_FAULT, dict(report))

    async def _timeline(self, vid: int, started: dt.datetime) -> list[dict[str, Any]]:
        """Return the controller's triggers and the module's load changes before a fault, nearest the fault last."""
        first = started - TIMELINE_WINDOW
        last = started + TIMELINE_SLACK

        def within(when: dt.datetime | None) -> bool:
            return when is not None and first <= when <= last

        def select(log: list[EventLogEntry]) -> list[EventLogEntry]:
            return [item for item in log if within(controller_time(item.time_stamp))]

        rows: list[tuple[dt.datetime, dict[str, Any]]] = []
        errors: list[ReadError] = []
        for trigger in await read_trigger_history(self._entry, select, errors):
            if (when := controller_time(trigger["time"])) is None:
                continue
            rows.append(
                (
                    when,
                    {
                        "kind": "trigger",
                        "source_id": trigger["source_id"],
                        "source_name": trigger["source_name"],
                        "task_name": trigger["task_name"],
                        "trigger_type": trigger["trigger_type"],
                        "param1": trigger["param1"],
                    },
                )
            )
        if errors:
            LOGGER.debug("Timeline of module %d's fault is missing %s", vid, errors)

        rows.extend(
            (when, change)
            for when, change in self._load_changes.get(vid, ())
            if within(when)
        )

        # A busy window keeps the rows nearest the fault
        rows.sort(key=lambda row: row[0])
        return [
            {"time": when.isoformat(), "t": round((when - started).total_seconds(), 1)}
            | row
            for when, row in rows[-TIMELINE_MAX_ROWS:]
        ]

    @callback
    def _updated(self, vid: int) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)
        async_dispatcher_send(self._hass, self.signal_updated(vid))

    def _data(self) -> dict[str, dict[str, Any]]:
        return {str(vid): report for vid, report in self._reports.items()}

    async def _async_flush(self) -> None:
        # Written now rather than after the delay, so a reload reads what this entry kept
        await self._store.async_save(self._data())
