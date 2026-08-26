# -*- coding: utf-8 -*-
"""
Multi-purpose data logging software.

@author: Lothar Maisenbacher/UC Berkeley.
"""
import numpy as np
import configparser
import time
import json
import logging
import argparse
import atexit
from pathlib import Path
from types import SimpleNamespace
from ruamel.yaml import YAML

from defs import LoggerError
from amodevices.dev_exceptions import DeviceError

import urllib3
import influxdb_client
from influxdb_client.client.write_api import SYNCHRONOUS, WriteOptions, WriteType
from influxdb_client.client.exceptions import InfluxDBError

# Device modules
import dev_keysightdaq973a
import dev_smchrs012
import dev_purpleair
import dev_kjlc354
import dev_kjlc_acg
import dev_metonedr528
import dev_srsctc100
import dev_cryomechcpa1110
import dev_highfinesse
import dev_rp_lockbox
import dev_thorlabs_kpa101
import dev_thorlabs_mdt693b
import dev_thorlabs_pm100
import dev_pydase
import dev_srs_sim922

logger = logging.getLogger()

CONFIGPATH_DEFAULT = 'config.ini'

# Errors a database write can raise: catching only `InfluxDBError` would
# let a transport failure (DNS, refused connection, timeout) crash the
# polling loop.
DB_WRITE_ERRORS = (InfluxDBError, urllib3.exceptions.HTTPError)

# Interval between clock-sync heartbeat points (s); see
# `heartbeat_if_due()`
CLOCK_HEARTBEAT_INTERVAL_S = 10.0

# Client-side flush interval of the batching write api (ms). Small
# enough to keep Grafana fresh and to bound the data lost on a hard
# kill.
DB_BATCH_FLUSH_INTERVAL_MS = 200

# Maximum time `WriteApi.close()` may block draining the batch buffer at
# shutdown (ms); the influxdb-client default is 5 minutes, which would
# hang shutdown on an unreachable database.
DB_BATCH_MAX_CLOSE_WAIT_MS = 5_000

# Pace of re-initialization/reconnection attempts for a failed device (s)
RECONNECT_INTERVAL_S = 10.0

# Consecutive read failures after which a device is benched for
# reconnection: a one-off glitch costs nothing, while a dead connection
# stops producing per-cycle errors (at most a few log lines per
# RECONNECT_INTERVAL_S, even at fast update intervals)
READ_FAIL_STREAK_BACKOFF = 3

# Rate limit for cycle-overrun warnings (s): chronic overruns (device
# reads slower than the configured interval) must be visible without
# flooding the log
OVERRUN_WARN_INTERVAL_S = 60.0

def init_device(device):
    """
    Initialize the device and return an instance of the device class.

    device : dict
        Configuration dict of the device to initialize.
    """
    logger.info(
        'Trying to initialize device \'%s\' of model \'%s\'', device['Device'], device['Model'])

    device_instance = None

    # Keysight DAQ970A/973A multimeter (via VISA interface)
    if device['Model'] == 'Keysight DAQ973A':
        device_instance = dev_keysightdaq973a.Device(device)
    # SMC HRS012-AN-10-T chiller (via RS-232 port)
    if device['Model'] == 'SMC HRS012-AN-10-T':
        device_instance = dev_smchrs012.Device(device)
    # PurpleAir air quality sensor/particle counters (via web API)
    if device['Model'] == 'PurpleAir':
        device_instance = dev_purpleair.Device(device)
    # Kurt J. Lesker KJLC 354 series ion pressure gauge (via RS-485 port)
    if device['Model'] == 'KJLC 354':
        device_instance = dev_kjlc354.Device(device)
    # Kurt J. Lesker KJLC ACG series ambient capacitance manometer (via RS-232 port)
    if device['Model'] == 'KJLC ACG':
        device_instance = dev_kjlc_acg.Device(device)
    # Met One DR-528 handheld particle counter (via RS-232 port)
    if device['Model'] == 'Met One DR-528':
        device_instance = dev_metonedr528.Device(device)
    # Stanford Research Instruments CTC100 cryogenic temperature controller
    # (via USB interface/virtual serial port)
    if device['Model'] == 'SRS CTC100':
        device_instance = dev_srsctc100.Device(device)
    # Cryomech CPA1110 helium compressor
    # (using Modbus TCP protocol over ethernet interface)
    if device['Model'] == 'Cryomech CPA1110':
        device_instance = dev_cryomechcpa1110.Device(device)
    # HighFinesse wavemeter
    # (using Windows DLL API)
    if device['Model'] == 'HighFinesse':
        device_instance = dev_highfinesse.Device(device)
    # Red Pitaya lockbox (rp-lockbox)
    if device['Model'] == 'rp-lockbox':
        device_instance = dev_rp_lockbox.Device(device)
    # Thorlabs KPA101 beam position aligner
    if device['Model'] == 'Thorlabs KPA101':
        device_instance = dev_thorlabs_kpa101.Device(device)
    # Thorlabs KPA101 beam position aligner
    if device['Model'] == 'Thorlabs MDT693B':
        device_instance = dev_thorlabs_mdt693b.Device(device)
    # Thorlabs PM100 power meter
    if device['Model'] == 'Thorlabs PM100':
        device_instance = dev_thorlabs_pm100.Device(device)
    # pydase RPC server
    if device['Model'] == 'pydase':
        device_instance = dev_pydase.Device(device)
    # Stanford Research Instruments (SRS) SIM922 diode temperature monitor (through RS-232 port)
    if device['Model'] == 'SRS SIM922':
        device_instance = dev_srs_sim922.Device(device)
    # Unknown device
    if device_instance is None:
        msg = f'Unknown device model \'{device["Model"]}\''
        logger.error(msg)
        raise LoggerError(msg)

    try:
        device_instance.connect()
    except (LoggerError, DeviceError) as err:
        logger.error('Could not connect. Error: %s', err.value)

    return device_instance


def _setup_logging(level=logging.INFO):
    """Configure the application logging setup."""
    logging.basicConfig(
        level=level,
        format='%(asctime)s.%(msecs)03d | %(levelname)-8s | '
               '%(name)s:%(funcName)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )


if __name__ == "__main__":
    # Parse input arguments
    parser = argparse.ArgumentParser(
        description='logger2 (https://github.com/lmaisenbacher/logger2)')
    parser.add_argument(
        '-c', '--config', dest='configpath', help='Path to configuration file', required=False,
        default=CONFIGPATH_DEFAULT)
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='Log every reading (disable log thinning) and enable '
             'DEBUG-level output')
    args = parser.parse_args()
    VERBOSE = args.verbose

    _setup_logging(logging.DEBUG if VERBOSE else logging.INFO)

    config_path = Path(args.configpath).absolute()

    # Read config file
    logger.info('Reading configuration from file \'%s\'', config_path)
    CONF = configparser.ConfigParser()
    files_read = CONF.read(config_path)
    if str(config_path) not in files_read:
        msg = f'Could not read configuration file \'{config_path}\''
        logger.error(msg)
        raise LoggerError(msg)

    DB_URL = CONF["Database"]["url"]
    DB_BUCKET = CONF["Database"]["bucket"]
    DB_ORG = CONF["Database"]["org"]
    DB_TOKEN = CONF["Database"]["token"]
    UPDATE_INTERVAL = float(CONF["Update"]["interval"])
    TIMEOUT = int(CONF["Devices"]["timeout"])
    # 'synchronous' (default): one blocking HTTP request per cycle.
    # 'batching': buffer client-side and flush every
    # DB_BATCH_FLUSH_INTERVAL_MS on influxdb-client's worker threads —
    # a slow or unreachable database then never blocks device polling.
    DB_WRITE_MODE = CONF["Database"].get("write_mode", "synchronous").lower()
    if DB_WRITE_MODE not in ("synchronous", "batching"):
        msg = f'Unknown [Database] write_mode \'{DB_WRITE_MODE}\''
        logger.error(msg)
        raise LoggerError(msg)

    device_config_path = Path(CONF["Devices"]["configpath"])
    # If path to `devices.json` is relative, use directory of `config.ini`
    if not device_config_path.is_absolute():
        device_config_path = config_path.parent.joinpath(device_config_path)

    # Set up database connection
    client = influxdb_client.InfluxDBClient(
        url=DB_URL,
        token=DB_TOKEN,
        org=DB_ORG
    )
    # Heartbeat writes always use a synchronous api (see
    # `heartbeat_if_due`); it doubles as the data path in synchronous
    # mode.
    write_api_sync = client.write_api(write_options=SYNCHRONOUS)
    if DB_WRITE_MODE == 'batching':

        def _on_write_error(conf, data, exception):
            logger.warning(
                'Could not write batch to InfluxDB database: %s', exception)

        def _on_write_retry(conf, data, exception):
            logger.warning(
                'Retrying InfluxDB batch write after error: %s', exception)

        # No lock around `write()`: unlike the pydase servers, logger2
        # is single-threaded, so only one thread ever pushes into the
        # batching api's buffer.
        write_api = client.write_api(
            write_options=WriteOptions(
                write_type=WriteType.batching,
                flush_interval=DB_BATCH_FLUSH_INTERVAL_MS,
                max_close_wait=DB_BATCH_MAX_CLOSE_WAIT_MS),
            error_callback=_on_write_error,
            retry_callback=_on_write_retry)
    else:
        write_api = write_api_sync

    _db_closed = SimpleNamespace(done=False)

    def _close_db():
        """Drain and close the database connection (idempotent).
        `WriteApi.flush()` is a no-op stub in influxdb-client 1.50.0,
        so closing is the only way to drain the batch buffer — bounded
        by DB_BATCH_MAX_CLOSE_WAIT_MS."""
        if _db_closed.done:
            return
        _db_closed.done = True
        try:
            write_api.close()
            if write_api_sync is not write_api:
                write_api_sync.close()
            client.close()
        except Exception as e:
            logger.warning('Error closing database connection: %s', e)

    # Best-effort backstop; the main loop closes explicitly on exit
    atexit.register(_close_db)

    # Per-cycle log thinning: the service wrapper redirects output to a
    # file that is never rotated, so per-reading lines at the update
    # interval reach GB over time. The first LOG_FIRST_N readings of each channel (and
    # polls of each device) are logged in full — the service log shows
    # the startup working — then only every LOG_EVERY_Mth, announced at
    # the transition. Warnings and errors are never thinned.
    LOG_FIRST_N = 10
    LOG_EVERY_M = 100
    log_counts = {}

    def log_this_reading(key):
        """Count reading `key` and decide whether to log it; announce
        the thinning once, in place of the first suppressed reading.
        With `-v`, every reading is logged."""
        count = log_counts[key] = log_counts.get(key, 0) + 1
        if VERBOSE:
            return True, count
        if count == LOG_FIRST_N + 1:
            logger.info(
                'First %d readings of \'%s\' logged — from here only '
                'every %dth is shown (run with -v to log every reading)',
                LOG_FIRST_N, key, LOG_EVERY_M)
        return count <= LOG_FIRST_N or count % LOG_EVERY_M == 0, count

    # Clock-sync heartbeat, as in the pydase servers: every
    # CLOCK_HEARTBEAT_INTERVAL_S, one point per device carrying this
    # host's clock in the `client_time_ns` field and — deliberately —
    # NO explicit timestamp, so the database stamps `_time` at
    # ingestion with ITS clock. `_time − client_time_ns` is then the
    # host-vs-database clock offset (plus one-way network latency),
    # e.g. for a Grafana panel that catches an unsynced PC and for
    # re-shifting its data offline. Written even when a device read
    # fails: a heartbeat means "logger running and database reachable",
    # independent of data.
    heartbeats = {}

    def heartbeat_if_due(device):
        """Write the clock-sync heartbeat for `device` if its interval
        has elapsed. Always a SYNCHRONOUS write: the semantics (written
        == database reachable, ingestion-stamped `_time`) do not
        survive buffering or client-side retries. On failure the point
        is discarded and the next attempt waits a full interval —
        unlike the pydase servers, which retry at the next call, the
        write here runs ON the polling thread, and an unreachable
        database would otherwise stall every cycle on the heartbeat's
        connection attempts instead of one cycle per interval."""
        hb = heartbeats.setdefault(device['Device'], SimpleNamespace(
            last=float('-inf'), interval_s=CLOCK_HEARTBEAT_INTERVAL_S))
        if time.monotonic() - hb.last < hb.interval_s:
            return
        hb.last = time.monotonic()
        json_body = [{
            'measurement': device['measurement'],
            'fields': {'client_time_ns': time.time_ns()},
            'tags': {
                'device': device['Device'],
                **device['tags'],
                'sensor': 'Clock sync',
            },
        }]
        try:
            write_api_sync.write(DB_BUCKET, DB_ORG, json_body)
        except DB_WRITE_ERRORS as e:
            logger.warning(
                'Could not write clock-sync heartbeat to InfluxDB '
                'database: %s', e)

    def build_point(device, channel_id, value, time_ns):
        """
        Build the InfluxDB point for a new measured value, or return
        None for a non-finite value.

        device : dict
            Configuration dict of the device.
        channel_id : str
            ID of the measurement channel.
        value : float
            Measured value.
        time_ns : int
            Timestamp of the poll (ns since epoch), recorded into the
            point — stamped at read time, so a buffered or retried
            write cannot skew the time series.
        """
        channel = device['Channels'][channel_id]
        tags = {
            'device': device['Device'],
            **device['tags']
        }
        tags.update(channel.get("tags", {}))
        if 'Multiplier' in channel:
            value *= channel['Multiplier']
        if 'Converter' in channel and channel['Converter'].get('Type') == 'polynomial':
            coeffs_dict = channel['Converter'].get('Coefficients', {})
            coeffs = np.array(list(coeffs_dict.values()))
            exponents = np.array(list(coeffs_dict.keys())).astype(int)
            value = np.sum(coeffs*value**exponents)
        unit_str = ' '+tags.get('unit') if tags.get('unit') is not None else ''
        show, count = log_this_reading(f'{device["Device"]}/{channel_id}')
        if isinstance(value, (float, np.floating)) and not np.isfinite(value):
            # InfluxDB's line protocol has no NaN/Inf representation —
            # influxdb-client would silently drop the field anyway, so
            # skip the write explicitly and say so.
            if show:
                logger.info('Channel \'%s\': %s%s — not written '
                            '(reading %d)', channel_id, value, unit_str,
                            count)
            return None
        if show:
            logger.info('Channel \'%s\': %s%s (reading %d)',
                        channel_id, value, unit_str, count)
        return {
            'measurement': device['measurement'],
            'fields': {channel['field-key']: value},
            'tags': tags,
            'time': int(time_ns),
        }

    logger.info('Reading device configuration from file \'%s\'',
                device_config_path)
    device_config_reader = CONF["Devices"].get("configreader", "JSON")
    try:
        with open(device_config_path) as device_config:
            if device_config_reader == 'JSON':
                devices = json.load(device_config)
            if device_config_reader == 'YAML':
                yaml = YAML(typ='safe')
                devices = yaml.load(device_config)
    except FileNotFoundError as e:
        msg = f'Could not read device configuration file \'{device_config_path}\': {e}'
        logger.error(msg)
        raise LoggerError(msg)
    # One supervision record per device. The loop must survive ANY
    # device failure — this is an unattended fleet logger, and the
    # other devices' data plus the heartbeat beat purity: device
    # modules can raise raw transport exceptions (not just
    # DeviceError/LoggerError), and a crash here takes down every
    # device of this logger. A device whose initialization, connection,
    # or reads fail is benched and retried every RECONNECT_INTERVAL_S;
    # a service restart is not the reconnect mechanism.
    device_states = []
    for device in devices:
        try:
            instance = init_device(device)
        except Exception:
            logger.exception(
                'Could not initialize device \'%s\'', device['Device'])
            instance = None
        device_states.append(SimpleNamespace(
            instance=instance, alive=instance is not None,
            fail_streak=0, bench_count=0, last_attempt=float('-inf')))

    def ensure_device(state, device):
        """Return the device instance to read this cycle, or None.

        Re-runs a failed initialization and reconnects a benched
        device, at most every RECONNECT_INTERVAL_S; in between, the
        device is skipped (its heartbeat is unaffected — it needs only
        the config dict).
        """
        if state.instance is not None and state.alive:
            return state.instance
        if time.monotonic() - state.last_attempt < RECONNECT_INTERVAL_S:
            return None
        state.last_attempt = time.monotonic()
        if state.instance is not None and state.bench_count >= 2:
            # Benched again without a successful read in between: the
            # first reconnect did not help (some modules open their
            # transport in __init__ and their connect() is a no-op) —
            # escalate to a full re-initialization.
            try:
                state.instance.close()
            except Exception:
                pass
            state.instance = None
        if state.instance is None:
            try:
                state.instance = init_device(device)
            except Exception:
                logger.exception(
                    'Could not initialize device \'%s\'',
                    device['Device'])
                state.instance = None
                return None
            state.alive = True
            state.fail_streak = 0
            logger.info('Initialized device \'%s\'', device['Device'])
            return state.instance
        # Benched: reconnect. Close first — drivers generally do not
        # close the old handle in connect(), and a still-held serial
        # port would refuse to re-open.
        try:
            state.instance.close()
        except Exception:
            pass
        try:
            state.instance.connect()
        except Exception as e:
            logger.warning('Could not reconnect device \'%s\': %s',
                           device['Device'], e)
            return None
        state.alive = True
        state.fail_streak = 0
        logger.info('Reconnected device \'%s\'', device['Device'])
        return state.instance

    next_cycle = time.monotonic()
    last_overrun_warning = float("-inf")
    while True:

        try:

            points = []
            # Per-device wall time of this cycle — names the culprit
            # in the overrun warning
            device_times = {}
            for device, state in zip(devices, device_states):
                t_device = time.monotonic()
                device_times[device['Device']] = 0.0
                # Before the device read (and independent of the
                # device's health): the heartbeat means "logger up +
                # database reachable"
                heartbeat_if_due(device)
                instance = ensure_device(state, device)
                if instance is None:
                    device_times[device['Device']] = (
                        time.monotonic() - t_device)
                    continue
                show, count = log_this_reading(device['Device'])
                if show:
                    if device.get('Address') is not None:
                        logger.info(
                            'Reading device: \'%s\' at \'%s\' (poll %d)',
                            device['Device'], device['Address'], count)
                    else:
                        logger.info('Reading device: \'%s\' (poll %d)',
                                    device['Device'], count)
                if device.get('ParallelReadout', True):
                    try:
                        readings = instance.get_values()
                        # One timestamp per poll: all channels of a
                        # device read share it
                        time_ns = time.time_ns()
                        new_points = [
                            point
                            for channel_id, value in readings.items()
                            if (point := build_point(
                                device, channel_id, value, time_ns))
                            is not None]
                    except (LoggerError, DeviceError) as err:
                        logger.error(
                            'Could not get measurement values. Error: %s', err.value)
                        state.fail_streak += 1
                    except Exception:
                        logger.exception(
                            'Unexpected error reading device \'%s\' — '
                            'device-module bug or lost connection',
                            device['Device'])
                        state.fail_streak += 1
                    else:
                        state.fail_streak = 0
                        # Only a successful READ proves the device
                        # healthy again (a no-op connect() does not)
                        state.bench_count = 0
                        points.extend(new_points)
                    if state.fail_streak >= READ_FAIL_STREAK_BACKOFF:
                        state.alive = False
                        state.bench_count += 1
                        state.last_attempt = time.monotonic()
                        logger.warning(
                            'Device \'%s\': %d consecutive read '
                            'failures — pausing reads, reconnecting '
                            'every %.0f s', device['Device'],
                            state.fail_streak, RECONNECT_INTERVAL_S)
                # else:
                #     for current_channel in current_device["Channels"]:
                #         try:
                #             measured_value = current_device["Object"].get_value(
                #                 current_channel["DeviceChannel"])
                #         except (ValueError, IOError) as err:
                #             LOG.error("Could not get measurement value. Error: %s", err)
                #             continue
                #         write_value(current_device, current_channel, measured_value)
                device_times[device['Device']] = time.monotonic() - t_device

            if points:
                # Batching mode: this only pushes into the client-side
                # buffer (microseconds) — the HTTP requests happen on
                # influxdb-client's worker threads, so a slow database
                # never stalls the polling. Synchronous mode: ONE
                # request per cycle instead of one per channel.
                try:
                    write_api.write(DB_BUCKET, DB_ORG, points)
                except DB_WRITE_ERRORS as e:
                    logger.warning(f'Could not write to database: {e}')

            # Fixed-rate schedule: cycles run on an absolute time grid,
            # so the configured interval is the true sampling period —
            # sleeping a fixed amount AFTER the cycle's work would
            # stretch the period by the device read time (serial
            # instruments cost hundreds of ms per cycle). A cycle that
            # overruns its slot skips the missed slots (no catch-up
            # bursts); overruns are logged, rate-limited.
            next_cycle += UPDATE_INTERVAL
            now = time.monotonic()
            if now > next_cycle:
                overrun = now - next_cycle
                skipped = int(overrun // UPDATE_INTERVAL) + 1
                next_cycle += skipped * UPDATE_INTERVAL
                if now - last_overrun_warning >= OVERRUN_WARN_INTERVAL_S:
                    last_overrun_warning = now
                    breakdown = ', '.join(
                        f'\'{name}\' {dt:.2f} s' for name, dt in sorted(
                            device_times.items(), key=lambda kv: -kv[1]))
                    logger.warning(
                        'Cycle overran the %.3g s interval by %.0f ms — '
                        'skipping %d slot(s); per-device cycle time: %s',
                        UPDATE_INTERVAL, overrun * 1e3, skipped, breakdown)
            time.sleep(max(0.0, next_cycle - time.monotonic()))

        except KeyboardInterrupt:
            break

    _close_db()
