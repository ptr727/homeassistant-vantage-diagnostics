# Vantage Module Fault Blueprint

An automation blueprint that notifies when a Vantage module faults, such as when its breaker trips, and when it recovers.
It also notifies on genuine out-of-range and back-in-range readings, and drops the readings the integration flags as side effects of a module fault.
It runs on the events and entities of the [Vantage Diagnostics][readme] integration, which must be set up first.

## Import

[![Open your Home Assistant instance and show the blueprint import dialog with this blueprint pre-filled.][blueprint-import-shield]][blueprint-import-link]

The button opens the blueprint import dialog in your Home Assistant.
To import it by hand instead, follow these steps.

1. Open **Settings -> Automations & scenes -> Blueprints**.
1. Select **Import blueprint**.
1. Paste the blueprint's URL, `https://github.com/ptr727/homeassistant-vantage-diagnostics/blob/main/blueprints/automation/vantage_diagnostics/vantage_module_fault.yaml`, and select **Preview** and then **Import**.
1. Select **Vantage module fault** and create an automation from it.

The integration does not install the blueprint, so import it once per Home Assistant instance.
The blueprint names its source, so **Re-import blueprint** in its menu fetches the latest version.
A re-import replaces the blueprint file, and each automation made from it keeps its inputs.

## Set Up the Program First

The controller raises a trigger only when the Design Center program assigns a task to the event.
In Design Center, assign a task to the following events, then write the program to the controller.

- **Each module's Alert event.** It raises the `module_alert` trigger, which names the missing line feed.
- **The out-of-range and in-range events of each sensor to monitor**, such as each line's power sensor and the module temperature. Give each a range and a hold time.

The task can do anything, such as play a tone on a station, or nothing at all.
Without the Alert task, a module fault still reports from the controller's system log warning, or from the module dropping off its bus.
Without the range tasks, no out-of-range or back-in-range reading notifies.

## Inputs

| Input | Default | What it does |
| --- | --- | --- |
| **Modules** | Every module | The modules to watch. The list shows the stock module devices that carry this integration's **Last fault** sensor |
| **Tasks** | Every task | Notify only on range triggers that start one of these tasks, named as in Design Center. Use it when the program raises range triggers you don't want notified |
| **Module fault** | On | Notify when a module faults |
| **Module recovered** | On | Notify when a faulted module comes back on its bus |
| **Out of range** | On | Notify when a monitored sensor reads out of range |
| **Back in range** | On | Notify when a monitored sensor reads back in range |
| **Trip side effects** | Off | Also notify on the range triggers a module fault causes |
| **Line power sensors** | None | The stock line power sensors the stock fallback watches |
| **Actions** | None | What to run, such as a notification |

The blueprint keys off each trigger's `trigger_type`, never off task names, so it works with any program.

## What Notifies

| Kind | Event | Notifies when | Example message |
| --- | --- | --- | --- |
| `fault` | `vantage_module_fault` | The module is watched | `Module 2 lost Line A at 06:47. Loads on it: Kitchen Cans (100%), Pantry (off).` |
| `recovered` | `vantage_module_recovered` | The module is watched | `Module 2 is back on its bus after 3 min 12 s.` |
| `out_of_range` | `vantage_trigger` | The sensor's module and the task are watched, and the trigger is no side effect | `Power Sensor - Line A on Module 2 is out of range.` |
| `in_range` | `vantage_trigger` | As for `out_of_range` | `Power Sensor - Line A on Module 2 is back in range.` |
| `fallback` | A line power sensor's state | The sensor reads below 0 for 2 minutes, unless a recent fault of its module notified | `Module 2 Power Sensor - Line A reads no power, so its module may have lost power.` |

A fault with no named line feed reads `Module 2 dropped off its bus at 06:47.` when it started with the bus drop.
Otherwise it reads `Module 2 raised a fault at 06:47.`
The loads listed are the ones on the missing line feed, with the level the controller last commanded.
A range trigger from a sensor on no module is watched only while **Modules** is empty.

A trip makes each of the module's monitored sensors read out of range, and then back in range once the breaker is reset.
The integration flags each of those triggers with `side_effect: true`.
With **Trip side effects** off, a trip that drops the module off its bus sends one fault notification and one recovery notification.
It sends no notification per monitored sensor.
A fault on one line feed that leaves the module on its bus sends no recovery notification, since the module never left its bus.
When its breaker is reset more than 2 minutes after the fault, its sensors back in range are not flagged, so each one notifies.
Turn off **Back in range** to drop those too.
The logbook shows every trigger either way.

## Actions and Variables

The actions run with these variables, so a notification can use them as they are.

| Variable | Content |
| --- | --- |
| `title` | A ready-made notification title, such as `Vantage module fault` |
| `message` | A ready-made notification message, as in the examples above |
| `kind` | `fault`, `recovered`, `out_of_range`, `in_range` or `fallback` |
| `report` | The event's data: the fault report, the recovery, or the trigger. Empty for `fallback` |

The fields of `report` are described under [Events][readme-events] in the README.
For example, this action sends a phone notification.

```yaml
- action: notify.mobile_app_my_phone
  data:
    title: "{{ title }}"
    message: "{{ message }}"
```

The automation runs in queued mode, so notifications that arrive together each run in turn.

## Stock Fallback

The stock fallback watches the stock integration's line power sensors and notifies when one reads below 0 for 2 minutes.
The sensors of a module with no power read `-0.999` in place of a reading.
The fallback needs only the stock integration, so it still catches a trip when Vantage Diagnostics has failed to set up.
It stays quiet when the module's **Last fault** sensor shows a fault in the last 5 minutes, and **Module fault** notifies for that module.
So a trip this integration reports usually notifies once.
The 2 minutes outlast the time the integration takes to report a bus drop that only its 60 second poll sees.
A drop that takes longer, such as while the module's enclosure can't be read, can notify twice.
The stock line power sensors start disabled, so enable the ones to watch first.

<!-- Shields -->

[blueprint-import-shield]: https://my.home-assistant.io/badges/blueprint_import.svg

<!-- Repo -->

[readme]: ../../../README.md
[readme-events]: ../../../README.md#events

<!-- External -->

[blueprint-import-link]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fptr727%2Fhomeassistant-vantage-diagnostics%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fvantage_diagnostics%2Fvantage_module_fault.yaml
