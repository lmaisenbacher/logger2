# logger2
A logger that reads out various devices and writes the output to an InfluxDB (>2.0).

Maintained by Lothar Maisenbacher (UC Berkeley), partly based on earlier software from Fabian Schmid and me at Max Planck Institute of Quantum Optics (MPQ).

## Supported devices

- `Keysight DAQ973A`: Keysight DAQ970A/973A multimeter (through VISA interface)
- `SMC HRS012-AN-10-T`: SMC HRS012-AN-10-T chiller (using RS-232 interface) (contributions by Jack Mango (UC Berkeley))
- `PurpleAir`: PurpleAir air quality sensor/particle counters (through web API)
- `KJLC 354`: Kurt J. Lesker KJLC 354 series ion pressure gauge and KJLC 300 series Pirani pressure gauge (through RS-485 interface) (contributions by Jack Mango (UC Berkeley))
- `KJLC ACG`: Kurt J. Lesker KJLC ACG series ambient capacitance manometer (through RS-232 port) (contributed by Jack Mango (UC Berkeley))
- `Met One DR-528`: Met One DR-528 handheld particle counter (through RS-232 interface) (contributed by Jack Mango (UC Berkeley))
- `SRS CTC100`: Stanford Research Instruments CTC100 cryogenic temperature controller (through USB interface)
- `Cryomech CPA1110`: Cryomech CPA1110 helium compressor (through Modbus TCP protocol over ethernet interface)
- `HighFinesse`: HighFinesse wavemeters (tested with models WS Ultimate 2 MC and WS/7) (through Windows DLL API communicating with wavemeter software). Channel type `Frequency` (THz, or GHz with `"Unit":"GHz"`); the wavemeter's result status rides along as the string field `status` on the same row (renamed with `"status-field-key"`, dropped with `"status-field-key": null`) — one row per wavemeter result: a valid result writes the frequency and `status="ok"`, an error writes only the status text (`overexposed`, `underexposed`, `no_signal`, `no_pulse`, ..., `unknown_error`; vocabulary in `amodevices.HighFinesseWS.STATUS_TEXT`), and "nothing new" (ReadOnce mode) writes nothing.
- `rp-lockbox`: Custom Red Pitaya lockbox [rp-lockbox](https://github.com/lmaisenbacher/rp-lockbox) (through SCPI over TCP/IP). Channel types per fast analog channel (`DeviceChannel` 1 or 2): `FastAnalogIn`, `FastAnalogOut`, `OutputMin`, `OutputMax` (V), `GeneratorState` (1/0, the signal generator on that output, which adds to the PID output); per auxiliary analog input (`DeviceChannel` 0–3): `AuxAnalogIn` (V); per PID controller (`PID` "11" to "22"): `GlobalGain`, `PGain`, `IGain`, `IIGain`, `DGain`, `Setpoint` (V), `PIDEnabled` (1/0, the "PID + relock output enabled" switch), `HoldState`, `RelockState` (1/0), `RelockMin`, `RelockMax` (V), `RelockStepsize` (V/s), `RelockInput` (V on the auxiliary input the PID's relock feature monitors), and `LockStatus`: 1/0 on every poll plus, on a change and at logger start, the companion fields `lock_event` ("locked", or "unlocked: relock input 0.120 V below window 0.500-1.200 V") and `lock_event_code` (1/-1) — the fields the cavity pointing PID server writes. `LockStatus` needs the rp-lockbox SCPI server with the `LOCKED?` query (newer than release 1.2.0).
- `pydase`: [pydase](https://github.com/tiqi-group/pydase) apps/plug-ins
- `SRS SIM922`: Stanford Research Instruments (SRS) SIM922 diode temperature monitor (through RS-232 port)

## Preparation

### Installing uv

logger2 is a [uv](https://docs.astral.sh/uv/) project: "pyproject.toml" in the repository declares its dependencies and its version, and "uv.lock" pins them. Install uv as its documentation describes, as a user-level install. When running logger2 as a service/daemon, run it under the user that uv was installed for, as it will not be found otherwise.

### Installing dependencies

In the repository directory, run

```
uv sync
```

which creates the virtual environment ".venv" there with every dependency, fetching a matching Python interpreter if the host has none. To check that everything is in place, run the logger's tests in that environment with

```
uv run pytest
```

On Raspberry Pi, importing numpy might not work right away, as compiled libraries are missing. Following https://github.com/numpy/numpy/issues/16012#issuecomment-615927988, these libraries can be installed with

```
sudo apt-get install libatlas-base-dev
```

The lab's shared environment (`~/venvs/unitrap`, its own uv project) declares the same dependencies. The loggers on the lab PCs run from it with its interpreter (`<venv>/Scripts/python.exe logger.py` on Windows) and need no ".venv" of their own.

### Upgrading package amodevices

The custom package amodevices is installed not from PyPI, but from its [GitHub repo](https://github.com/lmaisenbacher/amodevices), pinned to a commit in "uv.lock". To move the pin to the latest commit and install it, run

```
uv lock --upgrade-package amodevices
uv sync
```

### Adapting configuration

The logger uses two configuration files. "config.ini" contains the configuration of the database access, the update interval of the logger, and where the device configuration file is located. An example of a "config.ini" is included in the repository as "example_config.ini". The device configuration file is a JSON files that lists which devices and which channels on the given devices are read by the logger. An example device configuration file is included as "example_devices.json". Each channel writes the field named by its `field-key`; `Multiplier` and `Converter` transform that value. Device modules that report a per-reading status (currently `HighFinesse`) also write it as a companion STRING field on the same row, `status` by default (`ok` for a valid value, else a plain-word reason); `status-field-key` on a channel renames that field, and `"status-field-key": null` switches it off for the channel. Before any device is opened the logger refuses the key on any other model, a malformed key, and a status field key equal to the channel's `field-key`, so it never silently writes nothing or the wrong type.

By default, the logger will look for "config.ini" in the current working directory. To use a specific "config.ini", use the command line option `-c` to define the path to that file, e.g., `python logger.py -c /path/to/config.ini`.

## Database writing

Points are written to InfluxDB once per update cycle. Every point carries an explicit timestamp taken when its device was read (all channels of one device poll share it), so the recorded time series is independent of any write delay or buffering.

The `[Database]` option `write_mode` in "config.ini" selects how writes happen:

- `synchronous` (default): one blocking HTTP request per update cycle.
- `batching`: points are queued client-side and posted every 200 ms by logger2's own writer thread (`db_writer.py`), at most 5000 points per request; a request the database rejects (a 4xx status: malformed line, field type conflict, bad token) is dropped and logged, any other failure keeps its points at the head of the queue for a retry after 5 s, and the queue is capped at 20000 points (the oldest are dropped beyond it). A slow or unreachable database then never blocks device polling. The queue is drained at shutdown (bounded at 5 s). influxdb-client's own batching mode is deliberately not used: its RxPY window operator (reactivex 5.1.0) discards points pushed while a flush window is being closed, without reporting them (RxPY issue 694, fixed upstream but unreleased as of 2026-09).

Non-finite values (NaN/Inf) are never written - InfluxDB has no representation for them; a gap in the series marks them, and the log records each skipped reading.

### Clock-sync heartbeat

Every 10 s, one heartbeat point per device is written to that device's measurement, tagged `sensor="Clock sync"` and `process=<the logger's name>` - the one point in the database that names both a device and the process writing it, through which the Housekeeping dashboard joins a device's sampling period to its process. It carries this host's clock in the field `client_time_ns` and - deliberately - no explicit timestamp, so the database stamps `_time` at ingestion with its own clock: `_time - client_time_ns` is then the host-vs-database clock offset (plus one-way network latency). The [unitrap-pydase-apps](https://github.com/matterwaves/unitrap-pydase-apps) servers use the same convention, so all hosts appear on one clock-offset panel. Heartbeats are written synchronously even in batching mode (a written heartbeat means the database was reachable at that moment) and are sent even while a device read fails - they indicate the logger and database are alive, independent of data.

## Logs

The logger writes a rotating log file, in addition to whatever its console output is redirected to:

```
C:\logs\unitrap\<name>.log      Windows (the system drive)
~/logs/unitrap/<name>.log       Linux
```

up to six files of 10 MB. The service wrappers (FireDaemon, systemd) truncate their stdout redirect on every restart, so the log of a session that misbehaved is destroyed by the restart used to cure it; these files survive it.

The Windows location is a folder directly under the drive root on purpose. The FireDaemon services run as LocalSystem, whose profile is `C:\WINDOWS\system32\config\systemprofile`, so a per-account location puts a service's log where nobody looks and a terminal run of the same logger somewhere else. Windows grants Authenticated Users modify rights on everything beneath a folder created under the drive root, whoever created it, so `C:\logs\unitrap` is shared by the services and by anyone in a terminal without a permission being touched - and the name lock below only works if both can open the same file. Whenever the directory a logger ends up logging to is not the one it asked for, its first log lines say so and why.

`<name>` is the `[Logger]` key `name` in "config.ini", and it is MANDATORY: a config without one refuses to start, with a message naming the file and the key. The same string is the `process` tag of the health points below. It must equal the service name (the systemd unit or FireDaemon service name, which is also the Name column of the Notion list of loggers and servers), so that the log file, the database series, the service and the list all agree:

```
[Logger]
name = logger-cavity-temperature-monitor
```

The name lives in the config and nowhere else, deliberately. Deriving it from the config's location tied the identity to a directory layout (and every logger's config is called "config.ini", so naming from the file gave ten loggers one name), and reading it from the service definition would rely on every service being set up correctly, which is exactly what goes wrong when in doubt. Letters, digits, `.`, `_` and `-` only.

At startup the logger also takes a host-wide lock on its name (`<name>.lock` in the log directory, an OS file lock that dies with the process, so a crash cannot leave it behind). A second live process with the same name on the same host refuses to start and says which name is taken, which catches the easiest mistake there is: a copied config with the name left unchanged. Two processes sharing a name would write one log file, each rotating it out from under the other, and one health series with two uptimes interleaved. The claim is retried for a few seconds, because the operating system frees a dead holder's lock a few milliseconds after the process is gone and the service wrappers restart a crashed process at once.

`UNITRAP_LOG_DIR` overrides the log directory, for a host that keeps its logs elsewhere.

The file also carries the records of pydase's own logger, which never reach the root logger, so the `pydase` device module's connection problems are on it.

## Health telemetry

Once per 10 s the logger writes one point describing ITSELF, from a dedicated thread that touches neither the polling loop nor the data path. The [unitrap-pydase-apps](https://github.com/matterwaves/unitrap-pydase-apps) servers write the same point, so one dashboard covers the fleet: the Processes row of the lab's Housekeeping dashboard (`grafana/housekeeping_dashboard.json` there).

Measurement `serverhealth`, deliberately not the logger's own measurements - a logger serves many devices, in many measurements, and this describes none of them. Tags: `process`, `host`, plus `device` (the process name again) and `sensor="Health"`.

| Field | Type | Meaning |
| --- | --- | --- |
| `uptime_s` | float | Seconds since the logger started |
| `cycle_overrun_ms` | float | Worst amount a cycle's work ran past its interval since the last point |
| `cycle_overruns_total` | int | Cycles that ran past their interval, cumulative |
| `period_set_s` | float | The SET polling interval (`[Update] interval`) |
| `period_actual_s` | float | The period the polling loop achieved, mean over the interval before the point (a loop whose reads outlast the interval skips slots and reads a multiple of it); without a cycle start in it, the larger of the last mean and the time since the last start, so a wedged loop's cycle grows |
| `n_warnings` | int | WARNING records logged, cumulative |
| `n_errors` | int | ERROR records logged, cumulative |
| `n_written` | int | Records the buffered writer delivered, cumulative |
| `n_dropped` | int | Records the buffered writer discarded, cumulative |
| `software_version` | string | logger2's version, see Version below |
| `software_commit` | string | The checkout's short git commit, `+dirty` when tracked files are modified |
| `amodevices_version` | string | The installed amodevices version |
| `pydase_version` | string | The installed pydase version |
| `event` | string | `started`, on the first point written after a start only |
| `event_code` | int | 1 with `event` |

The first point goes out at once when the logger starts, not after an interval, and carries the `started` event; it stays pending until a point carrying it is written, so a database still booting after a lab-wide power cycle gets the start annotation late rather than never (that point's `uptime_s` says how late). The Housekeeping dashboard shows these as its "Process starts" annotations.

A logger writes no `loop_lag_ms`: that field is a pydase server's event-loop wake-up delay, and a plain polling loop has no event loop to measure. Its cycle overruns, a device read outlasting the interval being the usual cause, are the same event the servers report under the same two fields, so those are comparable across the fleet. The "worst" field is the maximum seen in the ten seconds before each point, reset at every point; a zero means no cycle overran in that interval.

InfluxDB pins a field's type per measurement, and the servers write this same measurement, so every field is coerced at one place in `health.ProcessHealth._build_point` and the tests assert the exact Python type of each. The write counters are absent in synchronous mode. A point the database REJECTS three times in a row disables the telemetry for this process, with one ERROR line saying so; nothing else the logger writes is affected.

The health point deliberately does not ride the clock-sync heartbeat, which is written per DEVICE: an appended health point would emit once per device, fifteen identical points every 10 s on a populated logger. The heartbeat is also the fleet's liveness signal and cannot sit behind a diagnostic.

## Version

logger2's version is the `[project]` version in "pyproject.toml", manual SemVer, bumped in the same commit as the change it describes: PATCH for fixes and refinements that change nothing about what is recorded, MINOR for a new capability (a device module, a channel type, a logged quantity), MAJOR for a change older readers of the loggers' database series would misread.

At startup the logger captures, once, that version, the checkout's short git commit (`+dirty` when tracked files are modified) and the installed amodevices and pydase versions (`fleet_version.py`, identical in [unitrap-pydase-apps](https://github.com/matterwaves/unitrap-pydase-apps), where each server declares its own `__version__`). A `git pull` under a running logger changes the checkout, not the running code, so the values describe the process until its restart. They show up in the startup log line (`logger-lockbox 1.0.0 (dd3d5d1), pydase 0.10.21, amodevices 0.1.23`) and as the string fields on every health point above, which the Housekeeping dashboard's Fleet table lists as Version, Commit, amodevices and pydase. A missing pyproject, a missing git, a refused probe each cost one WARNING and leave the field absent; nothing here can stop a logger from starting.

The Windows services run as LocalSystem while the checkouts belong to the Unitrap user, and git refuses a repository owned by another account unless `safe.directory` names it; every call names it on the command line, and if git still cannot run the hash is read from the ".git" directory itself, without the dirty check, with a WARNING saying so.

## Running the logger

### Stand-alone

To run the logger in the repository's virtual environment, use

```
uv run logger.py
```

(`uv run logger.py -c /path/to/config.ini` for a specific configuration). The file "run.bat" in the repository is a template for a Windows service or shortcut doing the same. From the lab's shared environment, run `python logger.py` with that environment's interpreter instead.

### As a service/daemon under Linux

The logger can be run as a system service, or daemon, in the background, also allowing it to be launched automatically after booting.

First, create a new daemon configuration file, here named "logger-chiller.service" to read out the temperatures of a laser chiller, using

```
sudo nano /etc/systemd/system/logger-chiller.service
```

Continuing the example of the laser chiller, the configuration file can e.g. look like

```
[Unit]
Description=Logger for chiller for 1064 nm fiber amplifier
After=multi-user.target

[Service]
Type=simple
Restart=always
WorkingDirectory=/home/rp-chiller/Coding/logger2
ExecStart=/home/rp-chiller/.local/bin/uv run logger.py
User=rp-chiller

[Install]
WantedBy=multi-user.target
```

Importantly, the daemon is run under the user "rp-chiller", as given by `User=rp-chiller`, which is the user for which uv was installed here. Also important is to define the working directory with `WorkingDirectory=...` to be the repository directory, where uv finds "pyproject.toml" and the virtual environment, and where the configuration file "config.ini" and the device JSON file defined in that configuration file are located, which here is just the directory of the repository itself. To use a different "config.ini" - and the device JSON defined in that "config.ini" - use the `-c` command line argument, e.g., to use "home/rp-chiller/Coding/logger2-config/chiller/config.ini":

```
ExecStart=/home/rp-chiller/.local/bin/uv run logger.py -c /home/rp-chiller/Coding/logger2-config/chiller/config.ini
```

After creating a new configuration file or editing it, the configurations need to be re-loaded with

```
sudo systemctl daemon-reload
```

Now, we can start the daemon with

```
sudo systemctl start logger-chiller
```

The status of the daemon can be checked with

```
sudo systemctl status logger-chiller
```

If everything is fine, this should output something like

```
● logger-chiller.service - Logger for chiller for 1064 nm fiber amplifier
     Loaded: loaded (/etc/systemd/system/logger-chiller.service; enabled; vendor preset: enabled)
     Active: active (running) since Thu 2022-06-09 18:18:50 PDT; 3s ago
```

Note the `enabled` in the second line - this will only be there if the daemon has been enabled as described below.

The output of the daemon, which will also include more details in case it fails to start, can be viewed with

```
sudo journalctl -fu logger-chiller
```

To stop the service, use

```
sudo systemctl stop logger-chiller
```

If the daemon should be started upon boot, it needs to be 'enabled' with

```
sudo systemctl enable logger-chiller
```
