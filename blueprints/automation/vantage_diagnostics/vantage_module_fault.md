# Vantage Module Fault Blueprint

An automation blueprint that notifies when a Vantage module faults, such as when its breaker trips, and when it recovers.
It also notifies on genuine out-of-range and back-in-range readings, and drops the readings the integration flags as side effects of a module fault.
It runs on the events and entities of the [Vantage Diagnostics][readme] integration, which must be set up first.
It needs a release of the integration that includes this blueprint, since it reads event fields older releases lack.
[A Tripped Module Breaker](#troubleshooting-example-a-tripped-module-breaker) follows one trip from the controller to the notifications, then through the investigation after it.

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
| **Tasks** | Every task | Notify only on range triggers that start one of these tasks, named as in Design Center. Use it when some of the program's range triggers start tasks you don't want notified |
| **Module fault** | On | Notify when a module faults |
| **Module recovered** | On | Notify when a faulted module comes back on its bus |
| **Out of range** | On | Notify when a monitored sensor reads out of range |
| **Back in range** | On | Notify when a monitored sensor reads back in range |
| **Trip side effects** | Off | Also notify on the range triggers a module fault causes |
| **Line power sensors** | None | The stock line power sensors the stock fallback watches |
| **Actions** | None | What to run, such as a notification |

The blueprint keys off each trigger's `trigger_type`, so it works with any program and needs no particular task names.

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

A trip that drops the module off its bus makes each of its monitored sensors read out of range.
They read back in range once the breaker is reset.
The integration flags each of those triggers with `side_effect: true`.
With **Trip side effects** off, such a trip sends one fault notification and one recovery notification.
It sends no notification per monitored sensor.
That needs a module sensor that reads the bus enabled, such as **Connectivity**, which starts enabled.
With none enabled, the integration never sees the module leave its bus, so the trip reads as a fault on one line feed.
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
| `kind` | `fault`, `recovered`, `out_of_range`, `in_range`, `fallback`, or `test` for a manual run |
| `report` | The event's data: the fault report, the recovery, or the trigger. Empty for `fallback` and `test` |

The fields of `report` are described under [Events][readme-events] in the README.
For example, this action sends a phone notification.

```yaml
- action: notify.mobile_app_my_phone
  data:
    title: "{{ title }}"
    message: "{{ message }}"
```

The automation runs in queued mode, so notifications that arrive together each run in turn.
Once its queue is full, Home Assistant logs a warning and drops the run.
Running the automation by hand, such as with **Run actions** in the editor, sends a test notification.
Its title is `Vantage module fault test`, so it shows whether the actions work.

## Stock Fallback

The stock fallback watches the stock integration's line power sensors and notifies when one reads below 0 for 2 minutes.
The sensors of a module with no power read `-0.999` in place of a reading.
The fallback needs only the stock integration, so it still catches a trip when Vantage Diagnostics has failed to set up.
It stays quiet when the module's **Last fault** sensor shows a fault in the last 5 minutes, and **Module fault** notifies for that module.
So a trip this integration reports usually notifies once.
When the fallback notifies, each listed sensor notifies on its own.
So a module that loses every line feed then notifies once per listed sensor on it.
The 2 minutes outlast the time the integration takes to report a bus drop that only its 60 second poll sees.
A drop that takes longer, such as while the module's enclosure can't be read, can notify twice.
The stock line power sensors start disabled, so enable the ones to watch first.

## Troubleshooting Example: A Tripped Module Breaker

This example assumes the program is set up as above.
It also assumes controller firmware with the enhanced log, which the trigger and system log events need, as [Events][readme-events] in the README describes.
On older firmware only the bus drop starts a fault, so no line feed is named.
It also assumes an action that sends a notification, such as the one under [Actions and Variables](#actions-and-variables).
The other inputs keep their defaults, so **Trip side effects** is off.

### What a Trip Reports

A dimmer module such as the SDM-12 takes its power through its line feeds, each wired to a breaker.
When a breaker that feeds every line feed trips, every load on the module goes dark at once.
The stock integration still shows those loads as on, since a load's state is the level the controller commanded rather than a reading.

A breaker that feeds every line feed of the module produces the sequence below, timed from the moment it trips.

| Time | What happens | Where it shows |
| --- | --- | --- |
| 0 s | The controller logs a warning that the module detected a missing line feed | A `vantage_system_log` event with `type: warning`, naming the module in `object_name` |
| 0 s | The module raises its alert | A `vantage_trigger` event with `trigger_type: module_alert` and the missing line feed in `param1` |
| 0 s | The integration starts a fault report | The module's **Last fault** sensor updates. The `vantage_module_fault` event follows once the trigger history is read, and the fault notification with it |
| About 3 s | The module drops off its enclosure bus | A `Module Offline` system log event, and the module's **Connectivity** sensor turns off |
| 6 s to 36 s | Each range-monitored sensor on the module reads out of range | A `vantage_trigger` event per sensor, with `trigger_type: out_of_range` and `side_effect: true`. None of them notifies |
| Breaker reset | The module comes back on its bus | A `Module Online` system log event. **Connectivity** turns on, **Bus errors** rises by one, and the recovery notification follows `vantage_module_recovered` |
| After the reset | Each range-monitored sensor reads back in range | A `vantage_trigger` event per sensor, with `trigger_type: in_range` and `side_effect: true`. None of them notifies |

So the trip sends two notifications, such as these.

```text
Module 2 lost Line A at 06:47. Loads on it: Kitchen Cans (100%), Pantry (off).
Module 2 is back on its bus after 3 min 12 s.
```

That holds while a module sensor that reads the bus is enabled, such as **Connectivity**.
[What Notifies](#what-notifies) covers the cases that send more.
One is a breaker reset more than 2 minutes after a fault that left the module on its bus.
A breaker that feeds only some of the module's line feeds can leave the module on its bus.
The trip then reads as a fault on one line feed.
The fault notification lists the loads on the missing line feed only on an SDM-12.
That is the one model whose channels the integration maps to line feeds.

While the module has no power, its stock line power and current sensors read `-0.999`.
That is the marker for a sensor that is not reporting, not a reading.
The stock module temperature sensor reads the same marker in degrees Celsius.
In Fahrenheit that shows as about 30.2 °F, which looks like a real temperature.
The fault report holds the readings taken when the fault started.
So a line that has just lost power may still show its last power there, since its sensor reports the loss later.

### Investigating After the Fact

1. Open the module's device page in Home Assistant.
1. Read its **Last fault** sensor, which shows when the fault started, the missing line feed and the outage.
1. Read the history of its **Connectivity** and **Bus errors** sensors, which shows each trip that dropped the module off its bus.
1. Call `vantage_diagnostics.get_fault_report` with no `module_id`, which returns the report of every module that has faulted.
1. Read the module's report: each line's reading at the fault, each load's level and when it last turned on and off, and the timeline.
1. Call `vantage_diagnostics.get_system_log` with a `limit` that reaches back to the trip, and find the warning and the `Module Offline` and `Module Online` entries.
1. Call `vantage_diagnostics.get_module_status`, and confirm that every module is back on its bus.

Each service is described under [Services][readme-services] in the README.

### When the Same Module Trips Again

The integration keeps only the last fault report of each module, so a later trip replaces it.
So save each report before the next trip, such as by calling `vantage_diagnostics.get_fault_report` after each one.
Then compare the saved reports.
Each `timeline` shows the triggers and load changes before its fault, such as a scene that switched loads off.
It reaches back at most 30 minutes and keeps at most the 60 rows nearest the fault, as the [`timeline`][readme-module-fault] field describes.
So a busy program's timeline covers less time, and a missing row is no proof that nothing happened.
A load that changed level just before several trips is the first to suspect.
Where each line feed has a breaker of its own, the named line feed says which breaker tripped.
Then repeated trips on one line feed point at the loads on it, which the report lists on an SDM-12.
Where one breaker feeds every line feed, the named line feed is only the first one the module noticed, so it points at no load.

Writing a program to the controller clears its trigger history.
So before a write, save a copy with `vantage_diagnostics.get_trigger_history` and `limit` at its maximum of 5000.
A master that returns fewer than 5000 entries has returned its whole history.

<!-- Shields -->

[blueprint-import-shield]: https://my.home-assistant.io/badges/blueprint_import.svg

<!-- Repo -->

[readme]: ../../../README.md
[readme-events]: ../../../README.md#events
[readme-module-fault]: ../../../README.md#vantage_module_fault
[readme-services]: ../../../README.md#services

<!-- External -->

[blueprint-import-link]: https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fptr727%2Fhomeassistant-vantage-diagnostics%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fvantage_diagnostics%2Fvantage_module_fault.yaml
