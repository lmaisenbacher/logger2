# -*- coding: utf-8 -*-
"""Tests for `health`: the rotating log file and the per-process
health point. No database is contacted and no log file leaves the
temporary directory. Runs under pytest.
"""

import configparser
import logging
import logging.handlers
import math
import threading
import time
from pathlib import Path

import pytest

import health


@pytest.fixture(autouse=True)
def isolate_logging():
    """Restore every logger's handlers and levels after each test.

    Without this a leaked file handler follows the session into the
    other test modules and, on Windows, keeps a log file open.
    """
    names = ('', *health._NON_PROPAGATING_LOGGERS)
    before = {name: (list(logging.getLogger(name).handlers),
                     logging.getLogger(name).level)
              for name in names}
    yield
    health.teardown_process_logging()
    for name, (handlers, level) in before.items():
        target = logging.getLogger(name)
        target.handlers[:] = handlers
        target.setLevel(level)


@pytest.fixture
def process_health():
    """A fresh `ProcessHealth`, so tests never touch the singleton."""
    return health.ProcessHealth(interval_s=0.01)


class FakeWriter:
    """Stands in for a `BufferedWriter` in the counter sums."""

    def __init__(self, written=0, dropped=0):
        self.n_written = written
        self.n_dropped = dropped


class Rejected(Exception):
    """An `ApiException`-shaped rejection: retrying cannot help."""

    def __init__(self, status=422):
        super().__init__('unprocessable entity')
        self.status = status


def read_log(path):
    """The log file's text, after draining the listener queue."""
    if health._LOG_LISTENER is not None:
        health._LOG_LISTENER.stop()      # flushes; the test is done with it
        health._LOG_LISTENER = None
    return Path(path).read_text(encoding='utf-8')


def make_config(name=None):
    config = configparser.ConfigParser()
    if name is not None:
        config['Logger'] = {'name': name}
    return config


# -- the log file ----------------------------------------------------


def test_import_installs_no_handlers():
    """`logger.py` calls `logging.basicConfig` before this module's
    setup; an import-time root handler would make that a silent no-op
    and change the console format."""
    import subprocess
    import sys
    repo = str(Path(__file__).resolve().parent)
    result = subprocess.run(
        [sys.executable, '-c',
         'import sys, logging; sys.path.insert(0, %r);'
         ' import health;'
         ' print(len(logging.getLogger().handlers))' % repo],
        capture_output=True, text=True, cwd=repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == '0', result.stdout


def test_env_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    path = health.setup_process_logging('demo')
    assert path == tmp_path / 'demo.log'
    logging.getLogger('dev_kjlc354').warning('gauge did not answer')
    assert 'gauge did not answer' in read_log(path)


def test_default_dir_is_home_logs_unitrap(tmp_path, monkeypatch):
    monkeypatch.delenv(health.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    assert health.resolve_log_dir() == tmp_path / 'logs' / 'unitrap'


def test_falls_back_when_directory_unusable(tmp_path, monkeypatch):
    """A service without a usable profile still logs somewhere."""
    blocker = tmp_path / 'a-file'
    blocker.write_text('not a directory', encoding='utf-8')
    monkeypatch.setenv(health.LOG_DIR_ENV, str(blocker / 'sub'))
    monkeypatch.setattr(Path, 'home', classmethod(
        lambda cls: (_ for _ in ()).throw(RuntimeError('no home'))))
    directory = health.resolve_log_dir()
    assert directory is not None and directory.is_dir()


def test_console_handler_survives(tmp_path, monkeypatch):
    """Install AFTER basicConfig, the way `logger.py` does."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    console = logging.StreamHandler()
    logging.getLogger().addHandler(console)
    level_before = logging.getLogger().level
    health.setup_process_logging('demo')
    assert console in logging.getLogger().handlers
    assert logging.getLogger().level == level_before


def test_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    first = health.setup_process_logging('demo')
    second = health.setup_process_logging('demo')
    assert first == second
    queue_handlers = [h for h in logging.getLogger().handlers
                      if isinstance(h, logging.handlers.QueueHandler)]
    counters = [h for h in logging.getLogger().handlers
                if isinstance(h, health._LogCounter)]
    assert len(queue_handlers) == 1 and len(counters) == 1


def test_pydase_records_captured_exactly_once(tmp_path, monkeypatch):
    """`dev_pydase` uses pydase clients, whose logger does not
    propagate, so root alone would miss them."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    path = health.setup_process_logging('demo')
    logging.getLogger('pydase').warning('client lost the connection')
    assert read_log(path).count('client lost the connection') == 1


def test_propagating_logger_is_not_double_attached(tmp_path, monkeypatch):
    """Attaching to a logger that still propagates would write twice."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    logging.getLogger('pydase').propagate = True
    path = health.setup_process_logging('demo')
    assert not any(isinstance(h, logging.handlers.QueueHandler)
                   for h in logging.getLogger('pydase').handlers)
    logging.getLogger('pydase').warning('one line only')
    assert read_log(path).count('one line only') == 1


def test_ansi_is_stripped_without_touching_the_record(tmp_path, monkeypatch):
    """pydase colors some messages before any handler sees them."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    path = health.setup_process_logging('demo')
    colored = '\x1b[36mws7\x1b[0m'
    records = []
    logging.getLogger('pydase').addHandler(
        type('Capture', (logging.Handler,),
             {'emit': lambda self, record: records.append(record)})())
    logging.getLogger('pydase').warning('Device [%s] gone', colored)
    text = read_log(path)
    assert 'Device [ws7] gone' in text
    assert '\x1b' not in text
    # The console must keep its colors: handlers share one record
    assert colored in records[0].args


def test_non_ascii_message(tmp_path, monkeypatch):
    """The fleet's messages are full of em dashes; cp1252 would raise."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    path = health.setup_process_logging('demo')
    logging.getLogger('logger').warning('Cycle overran by 840 ms — skipping')
    assert '—' in read_log(path)


def test_rotation_bounds_the_files(tmp_path, monkeypatch):
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    health.setup_process_logging('demo', max_bytes=400, backup_count=2)
    for i in range(200):
        logging.getLogger('logger').warning('filling the file %03d', i)
    if health._LOG_LISTENER is not None:
        health._LOG_LISTENER.stop()
        health._LOG_LISTENER = None
    assert len(list(tmp_path.glob('demo.log*'))) <= 3


def test_rollover_failure_is_contained_and_not_retried(tmp_path, monkeypatch):
    """On Windows a second handle on the file makes the rename fail.

    The stdlib would then retry the rollover on every subsequent
    record; here it cools down and keeps appending.
    """
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    health.setup_process_logging('demo', max_bytes=200, backup_count=1)
    handler = health._FILE_HANDLER
    attempts = []

    def failing_rotate(*args, **kwargs):
        attempts.append(1)
        raise PermissionError('another process holds the file')

    monkeypatch.setattr(logging.handlers.RotatingFileHandler,
                        'doRollover', failing_rotate)
    for i in range(50):
        handler.emit(logging.LogRecord(
            'logger', logging.WARNING, __file__, 1,
            'record %03d that overflows the tiny file' % i, (), None))
    assert len(attempts) == 1, 'the failed rollover must not retry per record'
    assert 'record 049' in Path(handler.baseFilename).read_text(
        encoding='utf-8')


# -- the process name ------------------------------------------------

CONFIG = Path('/home/unitrap/Coding/unitrap-logger2-configs/wavemeter/config.ini')


def test_name_comes_from_the_logger_section():
    assert health.process_name_from_config(
        make_config('logger-wmeter-1064'), CONFIG) == 'logger-wmeter-1064'


@pytest.mark.parametrize('config', [
    make_config(),
    make_config(''),
    make_config('   '),
    None,
])
def test_missing_name_refuses_to_start(config):
    """The name is mandatory: a fallback would be a silent wrong answer,
    and an optional key is exactly what gets skipped when in doubt -
    which is how ten loggers once shared the name 'logger2'."""
    with pytest.raises(health.ProcessNameError) as excinfo:
        health.process_name_from_config(config, CONFIG)
    message = str(excinfo.value)
    assert str(CONFIG) in message
    assert "'name'" in message and '[Logger]' in message


@pytest.mark.parametrize('bad', [
    'logger wmeter', 'wmeter/1', 'wmeter:1', '-wmeter', '.hidden', 'a\tb',
])
def test_malformed_name_refuses_to_start(bad):
    """A name becomes a file name and a database tag."""
    with pytest.raises(health.ProcessNameError):
        health.process_name_from_config(make_config(bad), CONFIG)


@pytest.mark.parametrize('good', [
    'logger-cavity-temperature-monitor', 'logger-wmeter-dye',
    'logger-rp-cryocooler', 'logger-dr-528', 'a', 'x.y_z',
])
def test_service_style_names_are_accepted(good):
    assert health.process_name_from_config(make_config(good), CONFIG) == good


def test_name_is_stripped():
    assert health.process_name_from_config(
        make_config(' logger-purpleair '), CONFIG) == 'logger-purpleair'


# -- the name lock ---------------------------------------------------

HOLD_LOCK = '''
import sys, time
sys.path.insert(0, sys.argv[1])
import health
health.claim_process_name(sys.argv[2], log_dir=sys.argv[3])
print("held", flush=True)
time.sleep(float(sys.argv[4]))
'''


def hold_lock_in_subprocess(name, log_dir, seconds=30.):
    """Start a second process that claims `name` and holds it; returns
    once the claim is confirmed."""
    import subprocess
    import sys
    repo = str(Path(__file__).resolve().parent)
    proc = subprocess.Popen(
        [sys.executable, '-c', HOLD_LOCK, repo, name, str(log_dir),
         str(seconds)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = proc.stdout.readline()
    assert line.strip() == 'held', proc.stderr.read()
    return proc


def test_claim_is_idempotent_within_a_process(tmp_path, monkeypatch):
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    health.claim_process_name('logger-demo')
    health.claim_process_name('logger-demo')
    assert (tmp_path / 'logger-demo.lock').exists()


def test_second_process_with_the_same_name_is_refused(tmp_path, monkeypatch):
    """The copy-paste hazard: a cloned config with the name unchanged."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(health, 'PROCESS_NAME_CLAIM_TIMEOUT_S', 0.3)
    holder = hold_lock_in_subprocess('logger-demo', tmp_path)
    try:
        with pytest.raises(health.ProcessNameTakenError) as excinfo:
            health.claim_process_name('logger-demo')
        assert 'logger-demo' in str(excinfo.value)
    finally:
        holder.kill()
        holder.wait()


def test_a_different_name_is_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    holder = hold_lock_in_subprocess('logger-demo', tmp_path)
    try:
        health.claim_process_name('logger-other')
    finally:
        holder.kill()
        holder.wait()


def test_a_restart_right_after_a_crash_gets_the_name(tmp_path, monkeypatch):
    """The operating system frees a dead holder's lock a few
    milliseconds after the process is gone, and the service wrappers
    restart a crashed process at once; the claim must wait that out
    rather than refuse the restart."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    holder = hold_lock_in_subprocess('logger-demo', tmp_path)
    holder.kill()
    holder.wait()
    health.claim_process_name('logger-demo')


# -- the health point ------------------------------------------------


def test_point_shape(process_health):
    process_health.set_process_name('logger2-vacuum')
    point = process_health._build_point()
    assert point['measurement'] == health.SERVER_HEALTH_MEASUREMENT
    assert point['tags'] == {
        'process': 'logger2-vacuum', 'host': process_health.host,
        'device': 'logger2-vacuum', 'sensor': 'Health'}
    assert set(point) == {'measurement', 'tags', 'fields', 'time'}


def test_timestamp_is_explicit_and_now(process_health):
    before = time.time_ns()
    point = process_health._build_point()
    assert before <= point['time'] <= time.time_ns()


def test_field_types_are_exact(process_health):
    """The servers write this same measurement, and InfluxDB pins a
    field's type per measurement: one int where a float went before
    rejects the whole request."""
    process_health.attach_counter(health._LogCounter())
    process_health.register_db_writer(FakeWriter(7, 1))
    process_health.note_cycle_overrun(3.)
    fields = process_health._build_point()['fields']
    floats = ('uptime_s', 'loop_lag_ms')
    ints = ('cycle_overruns_total', 'n_warnings', 'n_errors',
            'n_written', 'n_dropped')
    assert set(fields) == set(floats) | set(ints)
    for key in floats:
        assert type(fields[key]) is float, key
    for key in ints:
        assert type(fields[key]) is int, key


def test_non_finite_float_is_dropped_not_written(process_health):
    process_health._lag_max_ms = float('nan')
    fields = process_health._build_point()['fields']
    assert 'loop_lag_ms' not in fields
    assert math.isfinite(fields['uptime_s'])


def test_overrun_is_the_max_then_resets(process_health):
    for overrun in (12., 840., 3.):
        process_health.note_cycle_overrun(overrun)
    assert process_health._build_point()['fields']['loop_lag_ms'] == 840.
    assert process_health._build_point()['fields']['loop_lag_ms'] == 0.


def test_overrun_count_is_cumulative(process_health):
    """Cumulative survives a lost write and makes restarts visible;
    Grafana takes the difference."""
    for _ in range(3):
        process_health.note_cycle_overrun(1.)
    assert process_health._build_point()[
        'fields']['cycle_overruns_total'] == 3
    process_health.note_cycle_overrun(1.)
    assert process_health._build_point()[
        'fields']['cycle_overruns_total'] == 4


def test_overrun_never_raises_into_the_poll_loop(process_health):
    """It is called from the cycle that just overran."""
    process_health.note_cycle_overrun(None)
    assert process_health._enabled


def test_db_writer_counts_are_summed(process_health):
    process_health.register_db_writer(FakeWriter(10, 1))
    process_health.register_db_writer(FakeWriter(5, 2))
    fields = process_health._build_point()['fields']
    assert (fields['n_written'], fields['n_dropped']) == (15, 3)


def test_no_db_writer_means_no_write_fields(process_health):
    """Synchronous mode registers none."""
    fields = process_health._build_point()['fields']
    assert 'n_written' not in fields and 'n_dropped' not in fields


def test_setup_wires_the_singleton(tmp_path, monkeypatch):
    """Without this the health point carries no warning or error
    counts, which is most of what makes it worth reading."""
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path))
    health.setup_process_logging('demo')
    assert health.PROCESS_HEALTH._counter is health._LOG_COUNTER
    assert health.PROCESS_HEALTH.process == 'demo'
    fields = health.PROCESS_HEALTH._build_point()['fields']
    assert 'n_warnings' in fields and 'n_errors' in fields


def test_state_stays_bounded(process_health):
    """The bug class that only shows after days of uptime."""
    for i in range(100000):
        process_health.note_cycle_overrun(float(i % 7))
        if i % 1000 == 0:
            process_health._build_point()
    assert not any(isinstance(v, (list, dict, set)) and len(v) > 8
                   for v in vars(process_health).values())


# -- the emitter -----------------------------------------------------


def test_emits_one_point_per_interval(process_health):
    points = []
    done = threading.Event()

    def write(batch):
        points.extend(batch)
        if len(points) >= 3:
            done.set()

    process_health.start(write)
    try:
        assert done.wait(5.), 'the emitter produced %d points' % len(points)
    finally:
        process_health.stop()
    assert all(p['measurement'] == health.SERVER_HEALTH_MEASUREMENT
               for p in points)


def test_each_write_carries_exactly_one_point(process_health):
    batches = []
    process_health.start(batches.append)
    try:
        time.sleep(0.1)
    finally:
        process_health.stop()
    assert batches, 'the emitter never wrote'
    assert all(len(b) == 1 for b in batches)


def test_start_is_idempotent(process_health):
    """One process emits one series, however many devices it polls."""
    process_health.start(lambda batch: None)
    first = process_health._thread
    process_health.start(lambda batch: None)
    try:
        assert process_health._thread is first
    finally:
        process_health.stop()


def test_stop_is_idempotent(process_health):
    process_health.start(lambda batch: None)
    process_health.stop()
    process_health.stop()
    assert process_health._thread is None


def test_write_failure_does_not_stop_the_emitter(process_health):
    """A database outage is transient; the diagnostic must come back."""
    calls = []
    done = threading.Event()

    def write(batch):
        calls.append(1)
        if len(calls) >= 4:
            done.set()
        raise OSError('connection refused')

    process_health.start(write)
    try:
        assert done.wait(5.)
    finally:
        process_health.stop()
    assert process_health._enabled


def test_rejection_disables_after_the_limit(process_health, caplog):
    """A field-type conflict cannot be fixed by retrying, and repeating
    it every interval would be a permanent error stream."""
    calls = []

    def write(batch):
        calls.append(1)
        raise Rejected(422)

    with caplog.at_level(logging.ERROR):
        process_health.start(write)
        deadline = time.monotonic() + 5.
        while process_health._enabled and time.monotonic() < deadline:
            time.sleep(0.01)
        process_health.stop()
    assert not process_health._enabled
    assert len(calls) == health.SERVER_HEALTH_REJECT_LIMIT
    assert 'disabled' in caplog.text


@pytest.mark.parametrize('status', [429, 500, 503])
def test_retryable_status_never_disables(process_health, status):
    """Only a 4xx below 429 is a rejection; a rate limit or a server
    error is the database's problem, not the point's."""
    for _ in range(health.SERVER_HEALTH_REJECT_LIMIT * 3):
        process_health._on_write_failed(Rejected(status))
    assert process_health._enabled


def test_transport_failure_never_disables(process_health):
    """No `status` attribute at all: a socket error, a DNS failure."""
    for _ in range(health.SERVER_HEALTH_REJECT_LIMIT * 3):
        process_health._on_write_failed(OSError('connection refused'))
    assert process_health._enabled


def test_a_success_clears_the_rejection_streak(process_health):
    """Only CONSECUTIVE rejections disable."""
    calls = []

    def write(batch):
        calls.append(1)
        if len(calls) % 2:
            raise Rejected(422)

    process_health._write = write
    for _ in range(10):
        process_health._emit_once()
    assert process_health._enabled


def test_a_raising_write_function_is_contained(process_health):
    process_health._write = lambda batch: (_ for _ in ()).throw(
        ValueError('the client is closed'))
    process_health._emit_once()
    assert process_health._enabled


def test_a_raising_point_builder_is_contained(process_health):
    """The emitter thread must survive anything, or the telemetry ends
    silently at the first surprise."""
    process_health._build_point = lambda: (_ for _ in ()).throw(
        RuntimeError('unbuildable'))
    process_health.start(lambda batch: None)
    try:
        time.sleep(0.1)
        assert process_health._thread is not None
        assert process_health._thread.is_alive()
    finally:
        process_health.stop()


def test_warnings_are_logged_once(process_health, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(20):
            process_health._on_write_failed(OSError('connection refused'))
    assert caplog.text.count('Could not write the process health point') == 1
