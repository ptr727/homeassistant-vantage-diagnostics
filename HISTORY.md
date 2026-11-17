# homeassistant-vantage-diagnostics

Home Assistant integration adding controller diagnostics to the Vantage InFusion integration.

## Release History

- **Version 0.1**:
  - Initial repository standup from the fleet baseline.
  - `vantage_trigger` events for each task a controller object starts, and `vantage_system_log` events for notable system log entries.
  - Logbook entries for the trigger and system log events, with a trigger also shown on its source's stock entity where it has one.
  - Module connectivity, hardware model, hardware revision, firmware version and bus error sensors, attached to the stock module devices.
  - Event stream and host sensors on the controller device.
  - The `get_trigger_history`, `get_system_log` and `get_module_status` response-only services.
