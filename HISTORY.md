# homeassistant-vantage-diagnostics

Home Assistant integration adding controller diagnostics to the Vantage InFusion integration.

## Release History

- **Version 0.1**:
  - Initial repository standup from the fleet baseline.
  - `vantage_trigger` events for each task a controller object starts, and `vantage_system_log` events for notable system log entries.
  - Logbook entries for the trigger and system log events, with a trigger also shown on its source's stock entity where it has one.
  - Module connectivity, hardware model, hardware revision, firmware version and bus error sensors, attached to the stock module devices.
  - Event stream, host and IP address sensors on the controller device.
  - The `get_trigger_history`, `get_system_log` and `get_module_status` response-only services.
  - A module fault report, fired as a `vantage_module_fault` event when a module loses a line feed or drops off its bus. A `vantage_module_recovered` event, a **Last fault** sensor per module and the `get_fault_report` service go with it.
  - A `side_effect` flag on each out-of-range and back-in-range trigger, telling a trip's side effects from genuine readings.
