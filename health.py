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

from db_writer import BufferedWriter

logger = logging.getLogger(__name__)

# Environment overrides for the log directory and the process name
LOG_DIR_ENV = 'UNITRAP_LOG_DIR'
LOG_NAME_ENV = 'UNITRAP_LOG_NAME'
# Default directory, under the user's home on both Windows and Linux
LOG_DIR_DEFAULT_PARTS = ('logs', 'unitrap')
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
# Default process name; the config stem is appended when it is not the
# default, so several logger instances on one host stay apart
PROCESS_NAME_DEFAULT = 'logger2'
CONFIG_STEM_DEFAULT = 'config'

# Measurement carrying one point per PROCESS, shared with the pydase
# apps. Deliberately not the logger's own measurement: a logger serves
# many devices, in many measurements, and this describes none of them.
SERVER_HEALTH_MEASUREMENT = 'serverhealth'
# Interval between health points (s)
SERVER_HEALTH_INTERVAL_S = 10.0
# Consecutive rejected writes (4xx below 429, which retrying cannot
# fix) after which this process stops emitting health points
SERVER_HEALTH_REJECT_LIMIT = 3


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
    """

    def __init__(self, interval_s=SERVER_HEALTH_INTERVAL_S):
        self._t0 = time.monotonic()
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self.process = None
        self.host = _hostname()
        self._counter = None
        self._db_writers = []
        self._lag_max_ms = 0.
        self._overruns = 0
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

    def attach_counter(self, counter):
        with self._lock:
            self._counter = counter

    def register_db_writer(self, writer):
        with self._lock:
            if not any(w is writer for w in self._db_writers):
                self._db_writers.append(writer)

    # -- collection ----------------------------------------------------

    def note_cycle_overrun(self, overrun_ms):
        """Record one cycle that overran its slot, from the poll loop.

        The logger's counterpart to the servers' event-loop lag: it
        answers "was this process keeping up?" independently of why,
        which is exactly what the database could not say during the
        2026-09-15 incident. Published as the maximum since the last
        point, then reset, alongside a cumulative count.
        """
        try:
            with self._lock:
                self._overruns += 1
                if overrun_ms > self._lag_max_ms:
                    self._lag_max_ms = overrun_ms
        except Exception:
            pass

    def _build_point(self):
        now = time.monotonic()
        with self._lock:
            fields = {
                'uptime_s': float(now - self._t0),
                'loop_lag_ms': float(self._lag_max_ms),
                'cycle_overruns_total': int(self._overruns),
                }
            self._lag_max_ms = 0.
            if self._counter is not None:
                fields['n_warnings'] = int(self._counter.n_warnings)
                fields['n_errors'] = int(self._counter.n_errors)
            if self._db_writers:
                fields['n_written'] = int(sum(
                    getattr(w, 'n_written', 0) for w in self._db_writers))
                fields['n_dropped'] = int(sum(
                    getattr(w, 'n_dropped', 0) for w in self._db_writers))
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
        # share this measurement.
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
        many devices it polls.
        """
        with self._lock:
            if self._thread is not None or not self._enabled:
                return
            self._write = write_func
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name='process-health', daemon=True)
            thread = self._thread
        thread.start()

    def stop(self):
        """Stop emitting. Idempotent; never waits long."""
        with self._lock:
            thread, self._thread = self._thread, None
            self._write = None
        if thread is not None:
            self._stop.set()
            thread.join(timeout=2.)

    def _run(self):
        while not self._stop.wait(self._interval_s):
            try:
                self._emit_once()
            except Exception as e:
                self._warn_once('emit', 'Could not emit the process health'
                                        ' point: %s', e)

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

    def _on_write_failed(self, exc):
        """Count the failure and, for a rejection retrying cannot fix,
        stop emitting rather than repeat it every interval."""
        disabled = False
        with self._lock:
            if not BufferedWriter.is_rejected(exc):
                self._reject_streak = 0
            else:
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


def resolve_log_dir(log_dir=None):
    """The directory this process logs to, or None when none is usable.

    `UNITRAP_LOG_DIR` wins, then the argument, then `~/logs/unitrap`.
    The home lookup is guarded: a Windows service running as
    LocalSystem has no usable profile, and `expanduser` can hand back a
    literal '~' that would otherwise become a directory in the working
    directory. Falls back to the temp directory so a process without a
    home still logs somewhere.
    """
    candidates = []
    from_env = os.environ.get(LOG_DIR_ENV)
    if from_env:
        candidates.append(Path(from_env))
    if log_dir is not None:
        candidates.append(Path(log_dir))
    try:
        home = Path.home()
        if str(home) not in ('~', ''):
            candidates.append(home.joinpath(*LOG_DIR_DEFAULT_PARTS))
    except Exception:
        pass
    candidates.append(Path(tempfile.gettempdir()).joinpath(
        *LOG_DIR_DEFAULT_PARTS))
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return None


def derive_process_name(config_path=None, config=None):
    """This process's name, used for the log file AND the health point.

    In precedence order: `UNITRAP_LOG_NAME`, the optional `[Logger]
    name` key of the configuration, then 'logger2' plus the config stem
    when that stem is not the default — one host runs several logger
    instances, and they must not share a file or a database series.

    config_path : pathlib.Path
        Path of the configuration file.
    config : configparser.ConfigParser
        The parsed configuration, read for its optional name key.
    """
    name = os.environ.get(LOG_NAME_ENV) or ''
    if not name and config is not None:
        try:
            name = config.get('Logger', 'name', fallback='') or ''
        except configparser.Error:
            name = ''
    if not name and config_path is not None:
        config_path = Path(config_path)
        name = PROCESS_NAME_DEFAULT
        if config_path.stem != CONFIG_STEM_DEFAULT:
            name = f'{name}-{config_path.stem}'
    if not name or name in ('.', '..'):
        name = Path(sys.argv[0]).stem or PROCESS_NAME_DEFAULT
    return re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('_') or 'python'


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


def setup_process_logging(process_name=None, config_path=None, config=None,
                          log_dir=None, max_bytes=LOG_MAX_BYTES,
                          backup_count=LOG_BACKUP_COUNT):
    """Give this process a durable log file and the health counters.

    Adds a rotating file handler and a warning/error counter to the
    root logger and to the loggers that do not propagate to it.
    Returns the log file path, or None when no directory was usable.
    Idempotent, and never raises: a logger must run whether or not it
    can write a log.

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
        name = process_name or derive_process_name(config_path, config)
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
