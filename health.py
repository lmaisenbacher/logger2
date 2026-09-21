# -*- coding: utf-8 -*-
"""
Durable file logging and per-process health telemetry for logger2.

Mirrors the fleet implementation in the pydase apps'
`unitrap_services.py`, the way `db_writer.py` mirrors its
`BufferedWriter`: same environment variables, same default directory,
same file format, and the same `serverhealth` measurement, so one
Grafana dashboard covers the loggers and the servers together.

Two things the fleet could not answer during the 2026-09-15 incident:
the text log of the session that misbehaved was gone (the service
wrappers truncate their stdout redirect on every restart, so the
restart used to cure a problem destroys its evidence), and the
database knew every reading a logger took and nothing about the logger
itself. The files here rotate and survive restarts, and the health
point carries uptime, cycle overruns and the log's own error counts.

@author: Lothar Maisenbacher/UC Berkeley.
"""
import atexit
import configparser
import gc
import logging
import logging.handlers
import math
import os
import platform
import queue
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import fleet_version
from db_writer import BufferedWriter

logger = logging.getLogger(__name__)

# Environment override for the log directory
LOG_DIR_ENV = 'UNITRAP_LOG_DIR'
# Default directory. Windows: <system drive>\logs\unitrap - a folder
# directly under the drive root, because Windows grants Authenticated
# Users modify rights on everything beneath such a folder by inheritance,
# whoever created it, so a service running as LocalSystem and a person in
# a terminal share one directory and can open each other's files, which
# the name lock relies on; the account's own profile is useless
# (LocalSystem's is C:\WINDOWS\system32\config\systemprofile) and
# ProgramData would need permissions added. Linux: ~/logs/unitrap, the
# services there run as the user.
LOG_DIR_WINDOWS_PARTS = ('logs', 'unitrap')
LOG_DIR_HOME_PARTS = ('logs', 'unitrap')
# The process name is the config's mandatory key in this section
PROCESS_NAME_SECTION = 'Logger'
PROCESS_NAME_KEY = 'name'
# A name becomes a file name and a database tag, so it is restricted to
# characters that are safe in both, with no whitespace
PROCESS_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
# How long a start keeps trying to claim its name before concluding that
# another live process holds it (s), and the pause between attempts
PROCESS_NAME_CLAIM_TIMEOUT_S = 5.0
PROCESS_NAME_CLAIM_RETRY_S = 0.1
# Rotation: at most (backups + 1) files of this size per process
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
# Matches the console format of `logger._setup_logging`
LOG_FILE_FORMAT = ('%(asctime)s.%(msecs)03d | %(levelname)-8s | '
                   '%(name)s:%(funcName)s:%(lineno)d - %(message)s')
LOG_FILE_DATEFMT = '%Y-%m-%d %H:%M:%S'
# After a failed rollover, keep appending for this long before retrying
LOG_ROLLOVER_RETRY_S = 60.0
# Rate limit for a handler's own error reports (s)
LOG_ERROR_REPORT_INTERVAL_S = 60.0
_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')
# pydase configures its own logger with propagate=False, so records
# from it never reach a handler on the root logger. `dev_pydase` uses
# pydase clients, whose connection problems are logged there.
_NON_PROPAGATING_LOGGERS = ('pydase',)
# Measurement carrying one point per PROCESS, shared with the pydase
# apps. Deliberately not the logger's own measurement: a logger serves
# many devices, in many measurements, and this describes none of them.
SERVER_HEALTH_MEASUREMENT = 'serverhealth'
# Interval between health points (s)
SERVER_HEALTH_INTERVAL_S = 10.0
# Consecutive rejected writes (4xx below 429, which retrying cannot
# fix) after which this process stops emitting health points
SERVER_HEALTH_REJECT_LIMIT = 3
# The health emitter's sleep between its wake-ups (s): between points
# it is the thread-scheduling sentinel (see `ProcessHealth`)
HEALTH_SENTINEL_SLEEP_S = 0.01
# The event the first health point after a start carries, in the fleet's
# event convention: a comma-free `event` text stating what happened,
# repeating no identifier the point carries as a tag or another field
# (the process is a tag), plus an integer `event_code`
EVENT_STARTED = 'started'
EVENT_CODE_STARTED = 1
# The checkout this module runs from, for `capture_software_versions`
REPO_ROOT = Path(__file__).resolve().parent


class _AnsiStripFormatter(logging.Formatter):
    """Formatter that removes ANSI escapes from the FORMATTED STRING.

    pydase colors some of its messages with `click.style` before the
    record reaches any handler, so a plain formatter writes escape
    sequences into the file. Stripping happens on the output string and
    never on the record: handlers share one record object, so rewriting
    it here would also decolor the console.
    """

    def format(self, record):
        return _ANSI_RE.sub('', super().format(record))


class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Rotating handler whose failures cannot flood or recurse.

    On Windows the rename in `doRollover` fails whenever a second
    handle is open on the file — an operator running the logger by hand
    beside the service, a sync client, a virus scanner. The stdlib then
    retries the rollover on the very next record, so one stuck file
    becomes a rename syscall plus a stderr traceback per record. Here a
    failed rollover starts a cool-down and the handler keeps appending
    to the current file, and error reports are rate-limited.

    Nothing in here may call `logger.*`: the record would re-enter this
    same handler.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rollover_retry_after = -math.inf
        self._last_error_report = -math.inf

    def shouldRollover(self, record):
        if time.monotonic() < self._rollover_retry_after:
            return False
        return super().shouldRollover(record)

    def doRollover(self):
        try:
            super().doRollover()
        except OSError:
            self._rollover_retry_after = (
                time.monotonic() + LOG_ROLLOVER_RETRY_S)
            if self.stream is None:
                self.stream = self._open()

    def handleError(self, record):
        now = time.monotonic()
        if now - self._last_error_report < LOG_ERROR_REPORT_INTERVAL_S:
            return
        self._last_error_report = now
        super().handleError(record)


class _LogCounter(logging.Handler):
    """Counts WARNING and ERROR records for the health point.

    Two plain integers on purpose: anything that accumulates records
    would grow without bound over the weeks these processes run.
    `emit` never logs and never calls `handleError`, because either
    would re-enter this handler.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.n_warnings = 0
        self.n_errors = 0

    def emit(self, record):
        try:
            if record.levelno >= logging.ERROR:
                self.n_errors += 1
            elif record.levelno >= logging.WARNING:
                self.n_warnings += 1
        except Exception:
            pass


_LOGGING_LOCK = threading.Lock()
_LOG_COUNTER = _LogCounter()
_FILE_HANDLER = None
_LOG_LISTENER = None


def _hostname():
    """This host's name, or 'unknown'. Taken once: Windows and Linux
    disagree on case, and a per-call split would silently double the
    database series."""
    try:
        return platform.node() or 'unknown'
    except Exception:
        return 'unknown'


class ProcessHealth:
    """Per-PROCESS telemetry, written as one `serverhealth` point per
    interval by its own emitter thread.

    Deliberately NOT carried by the clock-sync heartbeat, which this
    logger writes once per DEVICE inside the polling loop: an appended
    health point would emit once per device, fifteen identical points
    every ten seconds on a populated logger. The heartbeat is also the
    fleet's liveness signal and cannot sit behind a diagnostic — a
    health point the database rejects for good, a field-type conflict
    being enough, would take it down with it.

    Beside the numbers, every point carries the software the process
    runs as string fields (`set_software`: version, commit, dependency
    versions), and the first point written after a start carries the
    `started` event, so a restart is one annotation in Grafana.

    Three fields look INSIDE the process, the same ones the pydase
    servers write (their `unitrap_services.ProcessHealth`), so a slow
    cycle can be told apart by cause: `gc_pause_ms`, the longest
    garbage-collection pause since the last point (`gc.callbacks`; a
    collection stops every thread) with `gc_gen2`, the number of
    generation-2 passes; `gil_lag_ms`, the worst overshoot of the
    emitter thread's own 10 ms sleeps between points - the delay any
    thread of this process suffers before it runs again, the OS
    scheduler plus the wait for the interpreter lock; and
    `cpu_percent`, the process's CPU time over the interval (user plus
    system, all threads, in percent of one core), with `n_threads`.
    The gc meter keeps its own lock (see `__init__`): the collector's
    callback may run inside a `_lock` region.
    """

    def __init__(self, interval_s=SERVER_HEALTH_INTERVAL_S):
        self._t0 = time.monotonic()
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self.process = None
        self.host = _hostname()
        self._counter = None
        self._db_writers = []
        self._overrun_max_ms = 0.
        self._overruns = 0
        # The in-process meters: gc pauses by generation in flight, the
        # worst pause and the gen-2 count since the last point, the
        # sentinel's worst overshoot, and the CPU-time snapshot the
        # last point was built from (at construction: the first point
        # then averages over the object's life, not the process's
        # imports over a fraction of a second).
        # The gc scalars sit under their OWN lock, never `_lock`: the
        # collector's callback runs in whichever thread allocated past
        # the threshold, which can be a thread INSIDE a `_lock` region
        # (`_build_point` allocates its dicts under it), and a plain
        # lock taken again by its holder blocks forever. The regions
        # of `_gc_lock` allocate nothing and call no Python function,
        # so no collection can start inside them.
        self._gc_lock = threading.Lock()
        self._gc_starts = {}
        self._gc_max_s = 0.
        self._gc_gen2 = 0
        self._gc_installed = False
        self._gil_max_s = 0.
        t = os.times()
        self._cpu_last = (float(t.user + t.system), self._t0)
        # The polling loop's set interval, the periods it achieved
        # since the last point, when it last started a cycle and the
        # last mean published (see `note_cycle`)
        self._cycle = None
        self._software = {}
        self._pending_event = None
        self._enabled = True
        self._reject_streak = 0
        self._warned = set()
        self._write = None
        self._thread = None
        self._stop = threading.Event()

    # -- registration --------------------------------------------------

    def set_process_name(self, name):
        with self._lock:
            self.process = name

    def set_software(self, fields):
        """Register the software fields every point carries: the dict
        `fleet_version.capture_versions` returns, values stringified."""
        with self._lock:
            self._software = {str(k): str(v) for k, v in dict(fields).items()}

    def software_fields(self):
        """A copy of the registered software fields."""
        with self._lock:
            return dict(self._software)

    def attach_counter(self, counter):
        with self._lock:
            self._counter = counter

    def register_db_writer(self, writer):
        with self._lock:
            if not any(w is writer for w in self._db_writers):
                self._db_writers.append(writer)

    # -- collection ----------------------------------------------------

    def note_cycle(self, interval_s, period_s):
        """Record the start of one polling cycle: the set interval and
        the period achieved since the previous start (None on the
        first).

        Published as `period_set_s` and `period_actual_s`, the mean period
        achieved since the last point - so a logger set to 1 s whose
        reads take 1.4 s, and which therefore skips every other slot,
        reads interval 1 s, cycle 2 s, which the overrun fields alone do
        not say. Without a cycle start since the last point (an
        interval longer than the health interval, or a wedged loop) the
        larger of the last mean and the time since the last start is
        published, so a slow loop keeps its true period and a wedged
        one's cycle grows point by point. The pydase servers write the
        same two fields for their fastest paced loop.
        """
        try:
            now = time.monotonic()
            with self._lock:
                if self._cycle is None:
                    self._cycle = {'interval_s': interval_s, 'sum': 0.,
                                   'n': 0, 'last_at': now, 'last_mean': None}
                cycle = self._cycle
                cycle['interval_s'] = interval_s
                cycle['last_at'] = now
                if period_s is not None:
                    cycle['sum'] += period_s
                    cycle['n'] += 1
        except Exception:
            pass

    def _cycle_fields(self, now):
        """`period_set_s`/`period_actual_s`, resetting the window (under the
        lock)."""
        cycle = self._cycle
        if cycle is None:
            return {}
        if cycle['n']:
            mean = cycle['sum'] / cycle['n']
            cycle['last_mean'] = mean
        else:
            mean = max(cycle['last_mean'] or 0., now - cycle['last_at'])
        cycle['sum'], cycle['n'] = 0., 0
        return {'period_set_s': float(cycle['interval_s']),
                'period_actual_s': float(mean)}

    def note_cycle_overrun_ms(self, overrun_ms):
        """Record one cycle that ran past its interval, from the poll
        loop.

        Published as `cycle_overrun_ms`, the worst overrun since the
        last point (then reset), plus the cumulative count
        `cycle_overruns_total` - the same two fields a pydase server
        writes for the same event, so the two are comparable. A logger
        writes no `loop_lag_ms`: that is a server's event-loop wake-up
        delay, and a plain polling loop has no event loop to measure.
        """
        try:
            with self._lock:
                self._overruns += 1
                if overrun_ms > self._overrun_max_ms:
                    self._overrun_max_ms = overrun_ms
        except Exception:
            pass

    def _gc_callback(self, phase, info):
        """`gc.callbacks` hook: runs in whichever thread triggered the
        collection - possibly one holding `_lock` - so it touches only
        the gc scalars under `_gc_lock` and never logs. `_gc_starts` is
        mutated without a lock: collections are serialized process-wide
        by the collector itself, so one generation's start and stop
        never interleave across threads."""
        try:
            gen = int(info.get('generation', 0))
            if phase == 'start':
                self._gc_starts[gen] = time.perf_counter()
                return
            t0 = self._gc_starts.pop(gen, None)
            if t0 is None:
                return
            pause = time.perf_counter() - t0
            with self._gc_lock:
                if pause > self._gc_max_s:
                    self._gc_max_s = pause
                if gen >= 2:
                    self._gc_gen2 += 1
        except Exception:
            pass

    def _install_gc_hook(self):
        with self._lock:
            if self._gc_installed:
                return
            self._gc_installed = True
        gc.callbacks.append(self._gc_callback)

    def _remove_gc_hook(self):
        with self._lock:
            if not self._gc_installed:
                return
            self._gc_installed = False
        try:
            gc.callbacks.remove(self._gc_callback)
        except ValueError:
            pass

    def _take_gc(self):
        """The worst pause (s) and the gen-2 count since the last call,
        then reset. Two scalar reads and two constant stores under the
        gc lock - no container is built inside it (a tuple allocation
        could schedule a collection whose callback wants this lock)."""
        with self._gc_lock:
            gc_max_s = self._gc_max_s
            gc_gen2 = self._gc_gen2
            self._gc_max_s = 0.
            self._gc_gen2 = 0
        return gc_max_s, gc_gen2

    def note_sentinel_lag_s(self, lag_s):
        """Record one overshoot of the sentinel's sleep (the emitter
        thread's own, between points): published as `gil_lag_ms`, the
        maximum since the last point."""
        try:
            with self._lock:
                if lag_s > self._gil_max_s:
                    self._gil_max_s = lag_s
        except Exception:
            pass

    def _cpu_fields(self, now):
        """`cpu_percent` over the interval since the last point (since
        construction for the first), from `os.times`; under the lock."""
        t = os.times()
        cpu = float(t.user + t.system)
        last = self._cpu_last
        self._cpu_last = (cpu, now)
        elapsed, used = now - last[1], cpu - last[0]
        if elapsed <= 0.:
            return {}
        return {'cpu_percent': float(100. * used / elapsed)}

    def _build_point(self):
        now = time.monotonic()
        gc_max_s, gc_gen2 = self._take_gc()     # outside `_lock`, see __init__
        with self._lock:
            fields = {
                'uptime_s': float(now - self._t0),
                'cycle_overrun_ms': float(self._overrun_max_ms),
                'cycle_overruns_total': int(self._overruns),
                'gc_pause_ms': float(gc_max_s * 1e3),
                'gc_gen2': int(gc_gen2),
                'gil_lag_ms': float(self._gil_max_s * 1e3),
                'n_threads': int(threading.active_count()),
                **self._cpu_fields(now),
                **self._cycle_fields(now),
                }
            self._overrun_max_ms = 0.
            self._gil_max_s = 0.
            if self._counter is not None:
                fields['n_warnings'] = int(self._counter.n_warnings)
                fields['n_errors'] = int(self._counter.n_errors)
            if self._db_writers:
                fields['n_written'] = int(sum(
                    getattr(w, 'n_written', 0) for w in self._db_writers))
                fields['n_dropped'] = int(sum(
                    getattr(w, 'n_dropped', 0) for w in self._db_writers))
            for key, value in self._software.items():
                fields[key] = str(value)
            if self._pending_event is not None:
                fields['event'] = str(self._pending_event[0])
                fields['event_code'] = int(self._pending_event[1])
            tags = {'process': self.process or 'unknown', 'host': self.host}
        # `device` and `sensor` repeat the process name so the fleet's
        # existing Flux helpers, which group on those tags, still work
        tags['device'] = tags['process']
        tags['sensor'] = 'Health'
        # Explicit timestamp, unlike the clock-sync heartbeat, whose
        # whole purpose is to be stamped on arrival. Every field is
        # coerced at this single site: InfluxDB pins a field's type per
        # measurement, and one int where a float went before rejects
        # the whole request — across the loggers AND the servers, which
        # share this measurement; the software fields and the event text
        # are strings for the same reason, the code an int.
        return {
            'measurement': SERVER_HEALTH_MEASUREMENT,
            'tags': tags,
            'fields': {k: v for k, v in fields.items()
                       if not isinstance(v, float) or math.isfinite(v)},
            'time': time.time_ns(),
            }

    # -- the emitter ---------------------------------------------------

    def start(self, write_func):
        """Start emitting, using a SYNCHRONOUS write function.

        The first caller wins: one process emits one series, however
        many devices it polls. The first point goes out at once, not
        after an interval, and carries the `started` event; it stays
        pending until a point carrying it is WRITTEN, because a
        database still booting after a lab-wide power cycle is exactly
        when a start annotation matters, and the point's `uptime_s`
        then says how late it is. A rejection drops it instead, so the
        numbers get their chance.
        """
        with self._lock:
            if self._thread is not None or not self._enabled:
                return
            self._write = write_func
            self._pending_event = (EVENT_STARTED, EVENT_CODE_STARTED)
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name='process-health', daemon=True)
            thread = self._thread
        self._install_gc_hook()
        thread.start()

    def stop(self):
        """Stop emitting. Idempotent; never waits long."""
        with self._lock:
            thread, self._thread = self._thread, None
            self._write = None
        if thread is not None:
            self._stop.set()
            thread.join(timeout=2.)
        self._remove_gc_hook()

    def _run(self):
        """The emitter thread: a point every interval, and between
        points the sentinel - it sleeps `HEALTH_SENTINEL_SLEEP_S` at a
        time and notes each overshoot, which is how late this thread
        was scheduled and got the interpreter lock back."""
        sleep_s = min(HEALTH_SENTINEL_SLEEP_S, self._interval_s)
        next_at = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= next_at:
                try:
                    self._emit_once()
                except Exception as e:
                    self._warn_once('emit', 'Could not emit the process'
                                            ' health point: %s', e)
                # Absolute schedule: the point cadence is not stretched
                # by the emit's own duration; after a stall longer than
                # an interval (a database timing out) the next point
                # waits a full interval instead of following at once
                next_at += self._interval_s
                now = time.monotonic()
                if next_at < now:
                    next_at = now + self._interval_s
            t0 = time.monotonic()
            if self._stop.wait(sleep_s):
                return
            self.note_sentinel_lag_s(time.monotonic() - t0 - sleep_s)

    def _emit_once(self):
        with self._lock:
            write, enabled = self._write, self._enabled
        if write is None or not enabled:
            return
        point = self._build_point()
        try:
            write([point])
        except Exception as e:
            self._on_write_failed(e)
        else:
            with self._lock:
                self._reject_streak = 0
                if 'event' in point['fields']:
                    self._pending_event = None

    def _on_write_failed(self, exc):
        """Count the failure and, for a rejection retrying cannot fix,
        stop emitting rather than repeat it every interval."""
        disabled = False
        with self._lock:
            if not BufferedWriter.is_rejected(exc):
                self._reject_streak = 0
            else:
                self._pending_event = None
                self._reject_streak += 1
                if self._reject_streak >= SERVER_HEALTH_REJECT_LIMIT:
                    self._enabled = False
                    disabled = True
        if disabled:
            logger.error(
                'Process health telemetry disabled: the database rejected'
                ' the \'%s\' point %d times in a row (%s). Everything else'
                ' this process writes is unaffected.',
                SERVER_HEALTH_MEASUREMENT, SERVER_HEALTH_REJECT_LIMIT, exc)
        else:
            self._warn_once(
                'write', 'Could not write the process health point: %s', exc)

    def _warn_once(self, key, message, *args):
        """One warning per key for the process's lifetime, logged
        OUTSIDE the lock — a log call can run arbitrary handler code."""
        with self._lock:
            if key in self._warned:
                return
            self._warned.add(key)
        logger.warning(message, *args)


PROCESS_HEALTH = ProcessHealth()


def default_log_dir(windows=None):
    """The directory a process logs to when nothing overrides it.

    See `LOG_DIR_WINDOWS_PARTS` for why Windows gets a folder under the
    drive root rather than a profile. `windows` defaults to the running
    platform; tests pass it explicitly.
    """
    if windows is None:
        windows = os.name == 'nt'
    if windows:
        drive = os.environ.get('SystemDrive', 'C:')
        return Path(drive + os.sep).joinpath(*LOG_DIR_WINDOWS_PARTS)
    return Path.home().joinpath(*LOG_DIR_HOME_PARTS)


_LOG_DIR_CACHE = {}


def resolve_log_dir(log_dir=None):
    """The directory this process logs to, or None when none is usable.

    `UNITRAP_LOG_DIR` wins, then the argument, then `default_log_dir()`.
    Then the fallbacks: the account's own home (guarded, because
    `expanduser` can hand back a literal '~' that would become a
    directory in the working directory) and the temp directory, so a
    process logs SOMEWHERE - but a fallback is announced with a
    warning, since it is exactly how a service ends up logging into a
    place nobody looks. Resolved once per process.
    """
    key = (os.environ.get(LOG_DIR_ENV), None if log_dir is None else str(log_dir))
    if key in _LOG_DIR_CACHE:
        return _LOG_DIR_CACHE[key]
    candidates = []
    if key[0]:
        candidates.append(('%s' % LOG_DIR_ENV, Path(key[0])))
    if log_dir is not None:
        candidates.append(('the configured directory', Path(log_dir)))
    candidates.append(('the default directory', default_log_dir()))
    try:
        home = Path.home()
        if str(home) not in ('~', ''):
            candidates.append(('the account\'s home',
                               home.joinpath(*LOG_DIR_HOME_PARTS)))
    except Exception:
        pass
    candidates.append(('the temp directory', Path(
        tempfile.gettempdir()).joinpath(*LOG_DIR_HOME_PARTS)))
    failures = []
    resolved = None
    for what, candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            resolved = candidate
            break
        except Exception as e:
            failures.append(f'{what} \'{candidate}\' ({e})')
    if failures and resolved is not None:
        logger.warning(
            'Logging to %s because these could not be used: %s',
            resolved, '; '.join(failures))
    _LOG_DIR_CACHE[key] = resolved
    return resolved


class ProcessNameError(Exception):
    """The configuration names this process badly or not at all."""


class ProcessNameTakenError(ProcessNameError):
    """Another live process on this host already runs under this name."""


def process_name_from_config(config, config_path):
    """This process's mandatory name, used for the log file AND the
    `process` tag of its health points.

    Read from the config's `[Logger]` section, key `name`, and
    required: a config without one raises `ProcessNameError` and the
    logger does not start. The name is defined in the config and
    nowhere else, deliberately. Deriving it from the config's location
    tied the identity to a directory layout, and reading it from the
    service definition relied on every service being set up correctly
    - both are exactly what people get wrong when in doubt. The name
    must match the service name, so the log file, the database series,
    the service and the Notion list of loggers and servers all agree.

    config : configparser.ConfigParser
        The parsed configuration.
    config_path : pathlib.Path
        Its path, for the error messages.
    """
    try:
        name = config.get(PROCESS_NAME_SECTION, PROCESS_NAME_KEY,
                          fallback=None)
    except (configparser.Error, AttributeError):
        name = None
    where = (f'\'{PROCESS_NAME_KEY}\' in the [{PROCESS_NAME_SECTION}]'
             f' section')
    if name is None or not name.strip():
        raise ProcessNameError(
            f'Configuration file \'{config_path}\' has no {where}. Every'
            f' logger needs one: it names the log file and the'
            f' \'process\' tag of its health points, and it must equal the'
            f' service name, e.g. {PROCESS_NAME_KEY} ='
            f' logger-cavity-temperature-monitor.')
    if not PROCESS_NAME_RE.match(name.strip()):
        raise ProcessNameError(
            f'Configuration file \'{config_path}\': {where} is {name!r},'
            f' but a process name may only contain letters, digits, \'.\','
            f' \'_\' and \'-\', and must start with a letter or digit.')
    return name.strip()


_PROCESS_LOCK = None


def claim_process_name(name, log_dir=None):
    """Take the host-wide lock on `name`, held until this process ends.

    Two processes with one name would share a log file, each rotating
    it out from under the other, and write one health series with two
    uptimes interleaved - and a copied config with the name left
    unchanged is the easiest mistake to make. The lock is an OS file
    lock on `<log dir>/<name>.lock`, released by the operating system
    when the holder dies, so a crash never leaves a stale one. Raises
    `ProcessNameTakenError` when another live process holds it.
    Idempotent within one process.
    """
    global _PROCESS_LOCK
    with _LOGGING_LOCK:
        if _PROCESS_LOCK is not None:
            return
    directory = resolve_log_dir(log_dir)
    if directory is None:
        logger.warning(
            'No writable log directory (set %s): cannot guard against'
            ' a second process named \'%s\'', LOG_DIR_ENV, name)
        return
    path = directory / f'{name}.lock'
    # Retried for a moment: the operating system releases a dead
    # holder's lock a few milliseconds AFTER the process is gone (about
    # 10 ms measured on Windows), and the service wrappers restart a
    # crashed process at once, so a single attempt would refuse the
    # very restart that recovers from a crash
    deadline = time.monotonic() + PROCESS_NAME_CLAIM_TIMEOUT_S
    while True:
        handle = open(path, 'a+')
        try:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            handle.close()
            if time.monotonic() >= deadline:
                raise ProcessNameTakenError(
                    f'Another process named \'{name}\' is already running'
                    f' on this host (it holds \'{path}\'). Two processes'
                    f' cannot share a name: give this one its own in the'
                    f' config, or stop the other first.') from None
            time.sleep(PROCESS_NAME_CLAIM_RETRY_S)
    with _LOGGING_LOCK:
        if _PROCESS_LOCK is None:
            _PROCESS_LOCK = handle
        else:
            handle.close()


def release_process_name():
    """Release the name lock again. FOR TESTS ONLY - a logger holds
    its name for its lifetime."""
    global _PROCESS_LOCK
    with _LOGGING_LOCK:
        handle, _PROCESS_LOCK = _PROCESS_LOCK, None
    if handle is not None:
        handle.close()


def _attach_to_log_tree(handler):
    """Attach `handler` to the root logger and to every logger that
    does not propagate to it.

    pydase configures its own logger with `propagate: False` and its
    own stream handler, so a root-only handler would miss everything
    `dev_pydase`'s clients report. The propagation check is
    load-bearing in both directions: attaching to a logger that DOES
    propagate would write and count every one of its records twice.
    """
    for target in (logging.getLogger(), *(
            logging.getLogger(name) for name in _NON_PROPAGATING_LOGGERS)):
        if target.name != 'root' and target.propagate:
            continue
        if any(h is handler for h in target.handlers):
            continue
        target.addHandler(handler)


def setup_process_logging(name, log_dir=None, max_bytes=LOG_MAX_BYTES,
                          backup_count=LOG_BACKUP_COUNT):
    """Give this process a durable log file and the health counters.

    `name` is the process name from `process_name_from_config`; it
    names the file and the health points. Adds a rotating file handler
    and a warning/error counter to the root logger and to the loggers
    that do not propagate to it. Returns the log file path, or None
    when no directory was usable. Idempotent, and never raises: a
    logger must run whether or not it can write a log.

    Call it AFTER `logging.basicConfig(...)`, which the logger reaches
    before reading its configuration. Nothing here changes a logger's
    level or removes a handler.

    The file write itself happens on a listener thread, so a stalled
    or network-mounted log directory cannot slow the polling loop.
    """
    global _FILE_HANDLER, _LOG_LISTENER
    with _LOGGING_LOCK:
        if _LOG_COUNTER not in logging.getLogger().handlers:
            _attach_to_log_tree(_LOG_COUNTER)
        PROCESS_HEALTH.attach_counter(_LOG_COUNTER)
        PROCESS_HEALTH.set_process_name(name)
        if _FILE_HANDLER is not None:
            return Path(_FILE_HANDLER.baseFilename)
        directory = resolve_log_dir(log_dir)
        if directory is None:
            logger.warning(
                'No writable log directory (set %s); this process logs to'
                ' the console only', LOG_DIR_ENV)
            return None
        try:
            handler = _SafeRotatingFileHandler(
                directory / f'{name}.log', maxBytes=max_bytes,
                backupCount=backup_count, encoding='utf-8',
                errors='backslashreplace')
        except Exception as e:
            logger.warning(
                'Could not open the log file in \'%s\': %s; this process'
                ' logs to the console only', directory, e)
            return None
        handler.setFormatter(_AnsiStripFormatter(
            fmt=LOG_FILE_FORMAT, datefmt=LOG_FILE_DATEFMT))
        listener = logging.handlers.QueueListener(
            queue.SimpleQueue(), handler, respect_handler_level=True)
        listener.start()
        atexit.register(listener.stop)
        queue_handler = logging.handlers.QueueHandler(listener.queue)
        _attach_to_log_tree(queue_handler)
        _FILE_HANDLER, _LOG_LISTENER = handler, listener
    logger.info('Logging to file \'%s\'', handler.baseFilename)
    return Path(handler.baseFilename)


def capture_software_versions(name, app_version=None):
    """Capture the software this process runs ONCE, hand it to the
    health points, and log one line naming it.

    `app_version` defaults to the `[project]` version of this
    checkout's pyproject.toml (`fleet_version.version_from_pyproject`);
    the commit comes from the checkout (`REPO_ROOT`), the dependency
    versions from the installed distributions. Returns the fields, `{}`
    when nothing could be captured. Never raises: a logger must run
    whether or not it can say what it is.
    """
    try:
        if app_version is None:
            app_version = fleet_version.version_from_pyproject(REPO_ROOT)
        fields = fleet_version.capture_versions(app_version, REPO_ROOT)
        PROCESS_HEALTH.set_software(fields)
        logger.info('%s %s', name, fleet_version.describe(fields))
        return fields
    except Exception as e:
        logger.warning('Could not capture the software version: %s', e)
        return {}


def teardown_process_logging():
    """Remove this process's file logging again. FOR TESTS ONLY.

    A logger installs logging once and keeps it for its lifetime; a
    test session installs it many times and must not leak a handler
    into the next test, which on Windows would also keep a log file
    open.
    """
    global _FILE_HANDLER, _LOG_LISTENER
    with _LOGGING_LOCK:
        listener, _LOG_LISTENER = _LOG_LISTENER, None
        handler, _FILE_HANDLER = _FILE_HANDLER, None
        targets = [logging.getLogger(), *(logging.getLogger(name)
                                          for name in
                                          _NON_PROPAGATING_LOGGERS)]
        for target in targets:
            for existing in list(target.handlers):
                if isinstance(existing, logging.handlers.QueueHandler) or (
                        existing is _LOG_COUNTER):
                    target.removeHandler(existing)
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        if handler is not None:
            try:
                handler.close()
            except Exception:
                pass
    release_process_name()
    PROCESS_HEALTH.set_software({})
    _LOG_DIR_CACHE.clear()
