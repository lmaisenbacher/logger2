# -*- coding: utf-8 -*-
"""Tests for `health`: the rotating log file and the per-process
health point. No database is contacted and no log file leaves the
temporary directory. Runs under pytest.
"""

import configparser
import gc
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


def test_default_dir_on_windows_is_under_the_drive_root(monkeypatch):
    """Not the account's profile: a service running as LocalSystem has
    C:\\WINDOWS\\system32\\config\\systemprofile for one, and a folder
    under the drive root is writable by every account, which the name
    lock needs to see across a service and a terminal."""
    monkeypatch.setenv('SystemDrive', 'D:')
    assert health.default_log_dir(windows=True) == Path('D:/') / 'logs' / 'unitrap'


def test_default_dir_on_linux_is_home_logs_unitrap(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    assert health.default_log_dir(windows=False) == tmp_path / 'logs' / 'unitrap'


def test_resolves_to_the_default_when_nothing_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv(health.LOG_DIR_ENV, raising=False)
    monkeypatch.setattr(health, 'default_log_dir',
                        lambda windows=None: tmp_path / 'default')
    assert health.resolve_log_dir() == tmp_path / 'default'


def test_env_override_beats_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv(health.LOG_DIR_ENV, str(tmp_path / 'chosen'))
    monkeypatch.setattr(health, 'default_log_dir',
                        lambda windows=None: tmp_path / 'default')
    assert health.resolve_log_dir() == tmp_path / 'chosen'


def test_falls_back_when_directory_unusable_and_says_so(tmp_path, monkeypatch,
                                                        caplog):
    """A process whose directory cannot be made still logs somewhere,
    and the warning names what failed: silently landing in a fallback
    is how a service ends up logging where nobody looks."""
    blocker = tmp_path / 'a-file'
    blocker.write_text('not a directory', encoding='utf-8')
    monkeypatch.setenv(health.LOG_DIR_ENV, str(blocker / 'sub'))
    monkeypatch.setattr(health, 'default_log_dir',
                        lambda windows=None: blocker / 'other')
    monkeypatch.setattr(Path, 'home', classmethod(
        lambda cls: (_ for _ in ()).throw(RuntimeError('no home'))))
    with caplog.at_level(logging.WARNING):
        directory = health.resolve_log_dir()
    assert directory is not None and directory.is_dir()
    assert 'could not be used' in caplog.text
    assert health.LOG_DIR_ENV in caplog.text
    assert 'the default directory' in caplog.text


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
    process_health.note_cycle_overrun_ms(3.)
    fields = process_health._build_point()['fields']
    floats = ('uptime_s', 'cycle_overrun_ms', 'gc_pause_ms', 'gil_lag_ms',
              'cpu_percent')
    ints = ('cycle_overruns_total', 'n_warnings', 'n_errors',
            'n_written', 'n_dropped', 'gc_gen2', 'n_threads')
    assert set(fields) == set(floats) | set(ints)
    for key in floats:
        assert type(fields[key]) is float, key
    for key in ints:
        assert type(fields[key]) is int, key


def test_non_finite_float_is_dropped_not_written(process_health):
    process_health._overrun_max_ms = float('nan')
    fields = process_health._build_point()['fields']
    assert 'cycle_overrun_ms' not in fields
    assert math.isfinite(fields['uptime_s'])


def test_cycle_fields_are_the_set_interval_and_the_mean_period(process_health):
    """A logger set to 1 s that skips every other slot reads interval
    1, cycle 2; the window resets per point."""
    process_health.note_cycle(1.0, None)          # the first start: no period
    process_health.note_cycle(1.0, 2.0)
    process_health.note_cycle(1.0, 2.0)
    fields = process_health._build_point()['fields']
    assert fields['period_set_s'] == 1.0 and fields['period_actual_s'] == 2.0
    process_health.note_cycle(1.0, 1.0)
    assert process_health._build_point()['fields']['period_actual_s'] == 1.0


def test_no_cycle_fields_before_the_first_cycle(process_health):
    fields = process_health._build_point()['fields']
    assert 'period_set_s' not in fields and 'period_actual_s' not in fields


def test_a_loop_that_did_not_start_a_cycle_reads_its_last_mean_or_its_age(
        process_health, monkeypatch):
    clock = [1000.]
    monkeypatch.setattr(health.time, 'monotonic', lambda: clock[0])
    process_health.note_cycle(30.0, None)
    process_health.note_cycle(30.0, 30.0)
    assert process_health._build_point()['fields']['period_actual_s'] == 30.0
    clock[0] += 10.                                # slower than the health interval
    assert process_health._build_point()['fields']['period_actual_s'] == 30.0
    clock[0] += 80.                                # wedged: 90 s since the start
    assert process_health._build_point()['fields']['period_actual_s'] == 90.0


def test_overrun_is_the_max_then_resets(process_health):
    for overrun in (12., 840., 3.):
        process_health.note_cycle_overrun_ms(overrun)
    assert process_health._build_point()['fields']['cycle_overrun_ms'] == 840.
    assert process_health._build_point()['fields']['cycle_overrun_ms'] == 0.


def test_overrun_count_is_cumulative(process_health):
    """Cumulative survives a lost write and makes restarts visible;
    Grafana takes the difference."""
    for _ in range(3):
        process_health.note_cycle_overrun_ms(1.)
    assert process_health._build_point()[
        'fields']['cycle_overruns_total'] == 3
    process_health.note_cycle_overrun_ms(1.)
    assert process_health._build_point()[
        'fields']['cycle_overruns_total'] == 4


def test_gc_pause_is_the_max_then_resets_and_counts_gen2(process_health):
    """A real collection through the hook, installed by `start` and
    removed by `stop`."""
    process_health.start(lambda batch: None)
    try:
        assert process_health._gc_callback in gc.callbacks
        gc.collect(2)
        fields = process_health._build_point()['fields']
    finally:
        process_health.stop()
    assert process_health._gc_callback not in gc.callbacks
    assert fields['gc_pause_ms'] > 0.
    assert fields['gc_gen2'] >= 1
    later = process_health._build_point()['fields']
    assert later['gc_pause_ms'] == 0.
    assert later['gc_gen2'] == 0


def test_a_collection_inside_the_point_lock_cannot_deadlock(process_health):
    """The collector's callback runs in the thread that allocated past
    the threshold, which can be the thread building the point INSIDE
    `_lock`; a callback that took `_lock` would block its own holder
    forever. Forced two ways: a collection from inside the locked
    region, and automatic ones with the threshold at one allocation."""
    process_health.start(lambda batch: None)
    thresholds = gc.get_threshold()
    try:
        original = process_health._cycle_fields

        def collecting(now):
            gc.collect(2)
            return original(now)
        process_health._cycle_fields = collecting
        done = threading.Event()

        def build():
            process_health._build_point()
            process_health._cycle_fields = original
            gc.set_threshold(1, 1, 1)
            for _ in range(300):
                process_health._build_point()
            done.set()
        t = threading.Thread(target=build, daemon=True)
        t.start()
        assert done.wait(10.), 'the point builder deadlocked on its own lock'
    finally:
        gc.set_threshold(*thresholds)
        process_health.stop()


def test_sentinel_lag_is_the_max_then_resets(process_health):
    for lag in (0.002, 0.031, 0.0005):
        process_health.note_sentinel_lag_s(lag)
    assert process_health._build_point()['fields']['gil_lag_ms'] == pytest.approx(31.)
    assert process_health._build_point()['fields']['gil_lag_ms'] == 0.


def test_the_emitter_is_the_sentinel_between_points(process_health):
    seen = []
    process_health.start(seen.append)
    try:
        time.sleep(0.15)
    finally:
        process_health.stop()
    values = [b[0]['fields']['gil_lag_ms'] for b in seen]
    assert values and all(0. <= v < 1e3 for v in values)


def test_cpu_percent_is_the_interval_mean(process_health):
    time.sleep(0.05)
    first = process_health._build_point()['fields']
    assert 0. <= first['cpu_percent'] < 150.
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.1:
        sum(range(1000))
    assert process_health._build_point()['fields']['cpu_percent'] > 20.


def test_a_stalled_write_is_not_followed_by_a_burst():
    """After a write longer than the interval the next point waits a
    full interval: a database timing out must not turn the emitter
    into a back-to-back requester."""
    ph = health.ProcessHealth(interval_s=0.05)
    starts = []

    def write(batch):
        starts.append(time.monotonic())
        if len(starts) == 1:
            time.sleep(0.2)
    ph.start(write)
    try:
        time.sleep(0.5)
    finally:
        ph.stop()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert gaps, 'the emitter wrote once at most'
    assert gaps[0] >= 0.2 + 0.04, gaps
    assert all(g >= 0.04 for g in gaps[1:]), gaps


def test_a_logger_writes_no_loop_lag(process_health):
    """`loop_lag_ms` is a server's event-loop wake-up delay; a plain
    polling loop has no event loop, so the field is absent rather than
    zero - a zero would read as "the loop was free"."""
    process_health.note_cycle_overrun_ms(500.)
    assert 'loop_lag_ms' not in process_health._build_point()['fields']


def test_overrun_never_raises_into_the_poll_loop(process_health):
    """It is called from the cycle that just overran."""
    process_health.note_cycle_overrun_ms(None)
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
        process_health.note_cycle_overrun_ms(float(i % 7))
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


# -- the software fields and the start event -------------------------


def test_software_fields_are_strings_on_every_point(process_health):
    """Strings beside the numbers: new keys, so no type conflict with
    what the database holds, but pinned like every other field."""
    process_health.set_software({'software_version': '1.0.0',
                                 'software_commit': 'abc1234+dirty',
                                 'pydase_version': 0.1})
    for _ in range(2):
        fields = process_health._build_point()['fields']
        assert fields['software_version'] == '1.0.0'
        assert fields['software_commit'] == 'abc1234+dirty'
        assert fields['pydase_version'] == '0.1'
        for key in ('software_version', 'software_commit', 'pydase_version'):
            assert type(fields[key]) is str, key


def test_no_software_registered_means_no_software_fields(process_health):
    assert process_health.software_fields() == {}
    fields = process_health._build_point()['fields']
    assert not any(key.endswith('_version') for key in fields)


def test_software_fields_returns_a_copy(process_health):
    process_health.set_software({'software_version': '1.0.0'})
    process_health.software_fields()['software_version'] = 'tampered'
    assert process_health.software_fields() == {'software_version': '1.0.0'}


def test_no_event_before_start(process_health):
    fields = process_health._build_point()['fields']
    assert 'event' not in fields and 'event_code' not in fields


def test_first_point_is_immediate_and_carries_started():
    """A restarted logger shows up on the dashboard within a second,
    and its first point is the start annotation: no second write path."""
    slow = health.ProcessHealth(interval_s=10.)
    points = []
    done = threading.Event()

    def write(batch):
        points.extend(batch)
        done.set()

    t0 = time.monotonic()
    slow.start(write)
    try:
        assert done.wait(1.), 'no point within a second'
    finally:
        slow.stop()
    assert time.monotonic() - t0 < 1.
    fields = points[0]['fields']
    assert fields['event'] == health.EVENT_STARTED
    assert type(fields['event']) is str
    assert fields['event_code'] == health.EVENT_CODE_STARTED
    assert type(fields['event_code']) is int


def test_later_points_carry_no_event(process_health):
    points = []
    done = threading.Event()

    def write(batch):
        points.extend(batch)
        if len(points) >= 3:
            done.set()

    process_health.start(write)
    try:
        assert done.wait(5.)
    finally:
        process_health.stop()
    assert 'event' in points[0]['fields']
    assert all('event' not in p['fields'] and 'event_code' not in p['fields']
               for p in points[1:])


def test_started_event_waits_for_a_successful_write(process_health):
    """A database still booting after a lab-wide power cycle is exactly
    when the start annotation matters; the event rides every point
    until one gets through."""
    points = []
    done = threading.Event()

    def write(batch):
        points.extend(batch)
        if len(points) >= 4:
            done.set()
        if len(points) <= 2:
            raise OSError('connection refused')

    process_health.start(write)
    try:
        assert done.wait(5.)
    finally:
        process_health.stop()
    assert [('event' in p['fields']) for p in points[:4]] == [
        True, True, True, False]


def test_rejected_first_point_drops_the_event(process_health):
    """A rejection retrying cannot fix must not keep the event, or the
    numbers never get their chance."""
    points = []

    def write(batch):
        points.extend(batch)
        if len(points) == 1:
            raise Rejected(422)

    process_health._write = write
    process_health._pending_event = (health.EVENT_STARTED,
                                     health.EVENT_CODE_STARTED)
    process_health._emit_once()
    process_health._emit_once()
    assert 'event' in points[0]['fields']
    assert 'event' not in points[1]['fields']
    assert process_health._enabled


def test_capture_software_versions_wires_the_singleton(monkeypatch, caplog):
    """The startup line names what the logger runs, and the health
    points carry the same. The commit is faked: the result must not
    depend on the developer's checkout."""
    monkeypatch.setattr(health.fleet_version, 'capture_commit',
                        lambda root: 'abc1234')
    with caplog.at_level(logging.INFO):
        fields = health.capture_software_versions(
            'logger-demo', app_version='9.9.9')
    try:
        assert fields['software_version'] == '9.9.9'
        assert fields['software_commit'] == 'abc1234'
        assert health.PROCESS_HEALTH.software_fields() == fields
        assert 'logger-demo 9.9.9 (abc1234)' in caplog.text
        point = health.PROCESS_HEALTH._build_point()['fields']
        assert point['software_version'] == '9.9.9'
    finally:
        health.PROCESS_HEALTH.set_software({})


def test_default_version_comes_from_the_pyproject(monkeypatch):
    """logger2's number lives in pyproject.toml, not in a script."""
    import tomllib
    monkeypatch.setattr(health.fleet_version, 'capture_commit',
                        lambda root: None)
    with open(health.REPO_ROOT / 'pyproject.toml', 'rb') as f:
        expected = tomllib.load(f)['project']['version']
    fields = health.capture_software_versions('logger-demo')
    try:
        assert fields['software_version'] == expected
    finally:
        health.PROCESS_HEALTH.set_software({})


def test_a_failing_capture_never_stops_a_start(monkeypatch, caplog):
    def explode(*args, **kwargs):
        raise RuntimeError('boom')

    monkeypatch.setattr(health.fleet_version, 'capture_versions', explode)
    with caplog.at_level(logging.WARNING):
        assert health.capture_software_versions(
            'demo', app_version='1.0.0') == {}
    assert 'Could not capture the software version' in caplog.text
    assert health.PROCESS_HEALTH.software_fields() == {}
