# -*- coding: utf-8 -*-
"""Tests for `dev_highfinesse.Device` — the logger-facing HighFinesse
channel handling — against a fake wavemeter DLL. Runs under pytest
(`pytest test_dev_highfinesse.py`) or directly as a script.
"""

import collections
import ctypes
import logging
import math
import time

import pytest

import dev_highfinesse
from amodevices.dev_exceptions import DeviceError
from readings import channels_missing_status, reading_fields


class _Entry:
    """One DLL entry point: callable, accepts `restype`/`argtypes`."""

    def __init__(self, impl):
        self._impl = impl
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        return self._impl(*args)


class FakeDLL:
    """Stands in for wlmData.dll: every entry point exists (returning 0)
    and `GetFrequencyNum` returns the scripted results in order, 0
    (ErrNoValue) once they run out. `GetAmplitudeNum` answers from
    `amplitudes` (index -> return), `GetPowerNum` pops `powers` (0.0
    once they run out — a consumed ReadOnce flag), `GetTemperature`/
    `GetPressure` pop their lists and repeat the last value. `counts`
    tallies the calls per entry point."""

    def __init__(self, results, amplitudes=None, powers=None,
                 temperatures=None, pressures=None):
        self.results = list(results)
        self.calls = 0
        self.pulse_mode = 1
        self.amplitudes = dict(amplitudes or {})
        self.powers = list(powers or [])
        self.temperatures = list(temperatures or [])
        self.pressures = list(pressures or [])
        self.counts = collections.Counter()

    def _get_frequency(self, *args):
        self.calls += 1
        if not self.results:
            return 0.0
        return self.results.pop(0)

    def _get_amplitude(self, num, index, a):
        return self.amplitudes.get(index, 0)

    def _get_power(self, *args):
        if not self.powers:
            return 0.0
        return self.powers.pop(0)

    @staticmethod
    def _pop_repeat(values):
        if len(values) > 1:
            return values.pop(0)
        return values[0] if values else 0.0

    def __getattr__(self, name):
        impl = {
            'Instantiate': lambda *a: 1,
            'GetPulseMode': lambda *a: self.pulse_mode,
            'GetFrequencyNum': self._get_frequency,
            'GetAmplitudeNum': self._get_amplitude,
            'GetPowerNum': self._get_power,
            'GetTemperature': lambda *a: self._pop_repeat(self.temperatures),
            'GetPressure': lambda *a: self._pop_repeat(self.pressures),
        }.get(name, lambda *a: 0)

        def counted(*args, _name=name, _impl=impl):
            self.counts[_name] += 1
            return _impl(*args)
        entry = _Entry(counted)
        self.__dict__[name] = entry
        return entry


# No 'status-field-key': the status is on by default, as 'status'
CHANNEL = {'Type': 'Frequency', 'field-key': 'frequency', 'Unit': 'GHz',
           'tags': {'unit': 'GHz'}}


def _off(ctype, key, **extra):
    """A channel with the status switched off, as the lab config has it."""
    return {'Type': ctype, 'field-key': key, 'status-field-key': None, **extra}


# The lab layout: status on the frequency channel only
CHANNELS_ALL = {
    'Frequency': dict(CHANNEL),
    'Amplitude max 1': _off('AmplitudeMax1', 'amplitude_max1', tags={'unit': 'counts'}),
    'Amplitude max 2': _off('AmplitudeMax2', 'amplitude_max2', tags={'unit': 'counts'}),
    'Amplitude avg 1': _off('AmplitudeAvg1', 'amplitude_avg1', tags={'unit': 'counts'}),
    'Amplitude avg 2': _off('AmplitudeAvg2', 'amplitude_avg2', tags={'unit': 'counts'}),
    'Pulse energy': _off('Power', 'energy', tags={'unit': 'uJ'}),
    'Temperature': _off('Temperature', 'temperature',
                        tags={'sensor': 'Optical unit', 'unit': 'C'}),
    'Pressure': _off('Pressure', 'pressure',
                     tags={'sensor': 'Optical unit', 'unit': 'mbar'}),
}
AMPLITUDES = {2: 2500, 3: 2400, 4: 800, 5: 700}      # cMax1, cMax2, cAvg1, cAvg2


def make_device(monkeypatch, results, channels=None, pulse_mode=1, **dll_kwargs):
    dll = FakeDLL(results, **dll_kwargs)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda path: dll, raising=False)
    if channels is None:
        channels = {'Frequency': dict(CHANNEL)}
    device = dev_highfinesse.Device({
        'Device': 'Fake WS/7', 'Model': 'HighFinesse', 'ReadOnce': True,
        'PulseMode': pulse_mode, 'tags': {'sensor': 'REMPI laser'},
        'measurement': 'wavemeter', 'Channels': channels,
    })
    return device, dll


def make_all(monkeypatch, results, channels=None, **dll_kwargs):
    """A device with the lab layout and a full fake DLL."""
    dll_kwargs.setdefault('amplitudes', AMPLITUDES)
    dll_kwargs.setdefault('powers', [12.5])
    dll_kwargs.setdefault('temperatures', [23.4])
    dll_kwargs.setdefault('pressures', [1013.2])
    return make_device(monkeypatch, results,
                       channels=channels or {k: dict(v) for k, v in CHANNELS_ALL.items()},
                       **dll_kwargs)


def test_valid_result_carries_frequency_and_status(monkeypatch):
    device, dll = make_device(monkeypatch, [387.123])
    reading = device.get_values()['Frequency']
    assert reading['frequency'] == pytest.approx(387123.0)   # GHz
    assert reading['status'] == 'ok'
    assert dll.calls == 1                                    # one DLL read
    assert device.STATUS_CAPABLE


def test_error_code_gives_status_text_and_no_frequency(monkeypatch):
    device, _ = make_device(monkeypatch, [-4.0, -1.0, -999.0])
    for expected in ('overexposed', 'no_signal', 'unknown_error'):
        reading = device.get_values()['Frequency']
        assert math.isnan(reading['frequency'])
        assert reading['status'] == expected
        # What the logger writes: the status alone
        assert reading_fields(CHANNEL, reading) == {'status': expected}


def test_nothing_new_writes_nothing(monkeypatch):
    # ErrNoValue (0): NaN frequency, no status -> the logger skips the row
    device, _ = make_device(monkeypatch, [0.0])
    reading = device.get_values()['Frequency']
    assert math.isnan(reading['frequency']) and reading['status'] is None
    assert reading_fields(CHANNEL, reading) == {}


def test_status_key_rename_and_opt_out(monkeypatch):
    device, _ = make_device(monkeypatch, [387.0, -4.0], channels={
        'F': {'Type': 'Frequency', 'field-key': 'frequency',
              'status-field-key': 'wm_status'},
        'G': {'Type': 'Frequency', 'field-key': 'frequency',
              'status-field-key': None}})
    readings = device.get_values()
    assert readings['F'] == {'frequency': pytest.approx(387.0),
                             'wm_status': 'ok'}
    assert readings['G'] == pytest.approx(387.0)               # plain value
    readings = device.get_values()
    assert readings['F']['wm_status'] == 'overexposed'
    assert math.isnan(readings['G'])


def test_get_frequency_api_compat(monkeypatch):
    device, dll = make_device(monkeypatch, [387.0, -4.0])
    assert device.get_frequency() == pytest.approx(387.0)     # THz
    assert math.isnan(device.get_frequency())
    assert dll.calls == 2


def test_config_rejections(monkeypatch):
    with pytest.raises(DeviceError, match='Unknown channel type'):
        make_device(monkeypatch, [], channels={
            'X': {'Type': 'Linewidth', 'field-key': 'x'}})
    with pytest.raises(DeviceError, match='Unit'):
        make_device(monkeypatch, [], channels={
            'F': {'Type': 'Frequency', 'field-key': 'f', 'Unit': 'nm'}})
    # 'Unit' is the frequency channel's key only
    make_device(monkeypatch, [], channels={
        'A': _off('AmplitudeMax1', 'a', Unit='nm')})
    for bad in ('fast', -1, True):
        with pytest.raises(DeviceError, match='MinInterval_s'):
            make_device(monkeypatch, [], channels={
                'T': _off('Temperature', 't',
                          DeviceSpecificParams={'MinInterval_s': bad})})


def test_transitions_are_logged_once_per_second(monkeypatch, caplog):
    device, _ = make_device(monkeypatch, [387.0, -4.0, 387.0, -4.0, 0.0, 0.0])
    clock = [1000.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    with caplog.at_level(logging.INFO, logger='dev_highfinesse'):
        device.get_values()                    # first result: '- -> ok'
        device.get_values()                    # ok -> overexposed (< 1 s: counted)
        device.get_values()                    # overexposed -> ok (counted)
        clock[0] += 2.0
        device.get_values()                    # ok -> overexposed: logged + count
        device.get_values()                    # nothing new: no transition
        device.get_values()
    lines = [r.getMessage() for r in caplog.records
             if 'result status' in r.getMessage()]
    assert len(lines) == 2
    assert lines[0].endswith('- -> ok')
    assert 'ok -> overexposed (code -4) (2 transitions not logged' in lines[1]


def test_line_protocol_writes_both_fields_on_one_row():
    # The client quotes the string field; both fields share the row
    from influxdb_client import Point
    fields = reading_fields(CHANNEL, {'frequency': 387123.0, 'status': 'ok'})
    line = Point.from_dict({
        'measurement': 'wavemeter', 'tags': {'unit': 'GHz'},
        'fields': fields, 'time': 1}).to_line_protocol()
    assert 'frequency=387123' in line and 'status="ok"' in line


# ---------------------------------------------------------------------------
# The diagnostics: amplitudes, energy, environment
# ---------------------------------------------------------------------------

def _written(readings, channels=CHANNELS_ALL):
    """What the logger would write per channel of this poll."""
    return {cid: reading_fields(channels[cid], reading)
            for cid, reading in readings.items()}


def test_one_dll_pass_feeds_every_channel(monkeypatch):
    device, dll = make_all(monkeypatch, [387.0])
    written = _written(device.get_values())
    assert written == {
        'Frequency': {'frequency': pytest.approx(387000.0), 'status': 'ok'},
        'Amplitude max 1': {'amplitude_max1': 2500.0},
        'Amplitude max 2': {'amplitude_max2': 2400.0},
        'Amplitude avg 1': {'amplitude_avg1': 800.0},
        'Amplitude avg 2': {'amplitude_avg2': 700.0},
        'Pulse energy': {'energy': 12.5},
        'Temperature': {'temperature': 23.4},
        'Pressure': {'pressure': 1013.2},
    }
    assert dll.counts['GetFrequencyNum'] == 1
    assert dll.counts['GetAmplitudeNum'] == 4
    assert dll.counts['GetPowerNum'] == 1
    assert dll.counts['GetTemperature'] == 1 and dll.counts['GetPressure'] == 1
    # Amplitudes are floats: InfluxDB fixes the field type at first write
    assert isinstance(written['Amplitude max 1']['amplitude_max1'], float)


def test_channels_of_one_quantity_share_the_read(monkeypatch):
    channels = {k: dict(v) for k, v in CHANNELS_ALL.items()}
    channels['Again'] = _off('AmplitudeMax1', 'amplitude_max1_again')
    device, dll = make_all(monkeypatch, [387.0], channels=channels)
    readings = device.get_values()
    assert readings['Again'] == 2500.0
    assert dll.counts['GetAmplitudeNum'] == 4              # memoized


def test_diagnostics_only_on_polls_with_a_result(monkeypatch):
    device, dll = make_all(monkeypatch, [387.0, 0.0, 0.0, -4.0, 0.0])
    keys = []
    for _ in range(5):
        readings = device.get_values()
        keys.append(sorted(readings))
    # A result (valid or an error code) brings the amplitudes and the
    # power; nothing new leaves only the status-carrying frequency
    # entry (NaN, dropped by the logger) — no "not written" log lines
    # for the seven opt-out channels 40 times a second
    full = sorted(CHANNELS_ALL)
    assert keys[0] == full                                  # + environment (first poll)
    assert keys[1] == ['Frequency'] and keys[2] == ['Frequency']
    assert keys[3] == sorted(k for k in CHANNELS_ALL
                             if k not in ('Temperature', 'Pressure'))
    assert keys[4] == ['Frequency']
    assert dll.counts['GetAmplitudeNum'] == 8 and dll.counts['GetPowerNum'] == 2


def test_overexposed_shot_keeps_its_amplitude(monkeypatch):
    # The maximum of a saturated pattern is the point of logging it
    device, _ = make_all(monkeypatch, [-4.0], amplitudes={2: 4095, 3: 4000, 4: 3000, 5: 2900})
    written = _written(device.get_values())
    assert written['Frequency'] == {'status': 'overexposed'}
    assert written['Amplitude max 1'] == {'amplitude_max1': 4095.0}
    # With a status key of its own (renamed, so it stays pinnable) the
    # amplitude row carries the shot's status beside the value
    channels = {k: dict(v) for k, v in CHANNELS_ALL.items()}
    channels['Amplitude max 1']['status-field-key'] = 'amplitude_status'
    device, _ = make_all(monkeypatch, [-4.0], channels=channels,
                         amplitudes={2: 4095, 3: 4000, 4: 3000, 5: 2900})
    written = _written(device.get_values(), channels)
    assert written['Amplitude max 1'] == {'amplitude_max1': 4095.0,
                                          'amplitude_status': 'overexposed'}


def test_amplitude_codes_are_dropped_and_logged_once(monkeypatch, caplog):
    device, _ = make_all(monkeypatch, [387.0, 387.0, 387.0],
                         amplitudes={2: -6, 3: 2400, 4: 0, 5: 700})
    with caplog.at_level(logging.INFO, logger='dev_highfinesse'):
        for _ in range(3):
            written = _written(device.get_values())
            assert written['Amplitude max 1'] == {}
            assert written['Amplitude avg 1'] == {}
            assert written['Amplitude max 2'] == {'amplitude_max2': 2400.0}
    lines = [r.getMessage() for r in caplog.records if 'amplitude status' in r.getMessage()]
    assert lines == ['\'Fake WS/7\': wavemeter amplitude status - -> ResERR_NotAvailable (code -6)']


def test_consumed_power_logs_no_value_once(monkeypatch, caplog):
    # GetPowerNum 0 on every result: a ReadOnce flag shared with the
    # frequency read — the log says so once, the energy is never written
    device, _ = make_all(monkeypatch, [387.0] * 5, powers=[])
    with caplog.at_level(logging.INFO, logger='dev_highfinesse'):
        for _ in range(5):
            written = _written(device.get_values())
            assert written['Pulse energy'] == {}
            assert written['Frequency']['frequency'] == pytest.approx(387000.0)
    lines = [r.getMessage() for r in caplog.records if 'energy status' in r.getMessage()]
    assert lines == ['\'Fake WS/7\': wavemeter energy status - -> no_value (code 0)']


def test_power_error_code(monkeypatch):
    device, _ = make_all(monkeypatch, [387.0, 387.0], powers=[-8.0, 12.5])
    assert _written(device.get_values())['Pulse energy'] == {}
    assert _written(device.get_values())['Pulse energy'] == {'energy': 12.5}
    # With a status key: GetPowerNum's OWN status, not the frequency's
    channels = {k: dict(v) for k, v in CHANNELS_ALL.items()}
    channels['Pulse energy']['status-field-key'] = 'energy_status'
    device, _ = make_all(monkeypatch, [387.0], channels=channels, powers=[-8.0])
    written = _written(device.get_values(), channels)
    assert written['Pulse energy'] == {'energy_status': 'no_pulse'}
    assert written['Frequency']['status'] == 'ok'


def test_environment_rate_limited_per_channel(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    channels = {
        'Frequency': dict(CHANNEL),
        'T': _off('Temperature', 't', tags={'sensor': 'Optical unit'}),
        'T fast': _off('Temperature', 't_fast', tags={'sensor': 'Optical unit'},
                       DeviceSpecificParams={'MinInterval_s': 2}),
        'P': _off('Pressure', 'p', tags={'sensor': 'Optical unit'}),
    }
    device, dll = make_all(monkeypatch, [0.0] * 10, channels=channels,
                           temperatures=[23.4], pressures=[1013.2])
    readings = device.get_values()                         # t = 0: all due
    assert readings['T'] == 23.4 and readings['T fast'] == 23.4 and readings['P'] == 1013.2
    assert dll.counts['GetTemperature'] == 1               # both channels, one call
    clock[0] = 1001.0                                      # nothing due
    readings = device.get_values()
    assert not {'T', 'T fast', 'P'} & set(readings)
    clock[0] = 1002.0                                      # the 2 s channel
    readings = device.get_values()
    assert readings['T fast'] == 23.4 and 'T' not in readings and 'P' not in readings
    assert dll.counts['GetTemperature'] == 2
    clock[0] = 1059.99                                     # the default 60 s: not yet
    readings = device.get_values()
    assert 'T' not in readings and 'P' not in readings
    clock[0] = 1060.0
    readings = device.get_values()
    assert readings['T'] == 23.4 and readings['P'] == 1013.2 and 'T fast' not in readings
    assert dll.counts['GetPressure'] == 2
    assert 'Frequency' in readings                         # always present


def test_environment_codes_dropped_and_logged(monkeypatch, caplog):
    device, _ = make_all(monkeypatch, [0.0, 0.0], temperatures=[-1000.0, 23.4],
                         pressures=[-1006.0])
    clock = [1000.0]
    monkeypatch.setattr(time, 'monotonic', lambda: clock[0])
    with caplog.at_level(logging.INFO, logger='dev_highfinesse'):
        written = _written(device.get_values())
        assert written['Temperature'] == {} and written['Pressure'] == {}
        clock[0] += 60.0
        written = _written(device.get_values())
        assert written['Temperature'] == {'temperature': 23.4}
        assert written['Pressure'] == {}
    lines = [r.getMessage() for r in caplog.records
             if 'temperature status' in r.getMessage() or 'pressure status' in r.getMessage()]
    assert lines == [
        '\'Fake WS/7\': wavemeter temperature status - -> ErrTempNotMeasured (code -1000)',
        '\'Fake WS/7\': wavemeter pressure status - -> ErrTempNotAvailable (code -1006)',
        '\'Fake WS/7\': wavemeter temperature status ErrTempNotMeasured -> ok',
    ]


def test_environment_requires_the_status_opt_out(monkeypatch):
    with pytest.raises(DeviceError, match='status-field-key'):
        make_device(monkeypatch, [], channels={
            'T': {'Type': 'Temperature', 'field-key': 't'}})
    device, _ = make_all(monkeypatch, [0.0])
    readings = device.get_values()
    assert readings['Temperature'] == 23.4                 # a plain value
    assert channels_missing_status(device.device, readings, device) == []


def test_status_series_guard(monkeypatch):
    def channels(amplitude_status, sensor=None):
        chan = {'Type': 'AmplitudeMax1', 'field-key': 'amplitude_max1',
                'tags': {'unit': 'counts'}}
        if amplitude_status != 'status':
            chan['status-field-key'] = amplitude_status
        if sensor is not None:
            chan['tags']['sensor'] = sensor
        return {'Frequency': dict(CHANNEL), 'A': chan}
    # The same key under the same sensor with a different unit tag: two
    # series a consumer pinning by sensor cannot tell apart
    with pytest.raises(DeviceError, match='both write the status field'):
        make_device(monkeypatch, [], channels=channels('status'))
    make_device(monkeypatch, [], channels=channels(None))                  # opted out
    make_device(monkeypatch, [], channels=channels('amplitude_status'))    # renamed
    make_device(monkeypatch, [], channels=channels('status', sensor='Other'))


def test_line_protocol_of_the_diagnostics():
    from influxdb_client import Point
    def line(cid, reading):
        return Point.from_dict({
            'measurement': 'wavemeter', 'tags': CHANNELS_ALL[cid].get('tags', {}),
            'fields': reading_fields(CHANNELS_ALL[cid], reading), 'time': 1,
        }).to_line_protocol()
    amplitude = line('Amplitude max 1', 2500.0)
    assert 'amplitude_max1=2500' in amplitude and 'amplitude_max1=2500i' not in amplitude
    assert 'energy=12.5' in line('Pulse energy', 12.5)
    assert 'sensor=Optical\\ unit' in line('Temperature', 23.4)
    assert 'temperature=23.4' in line('Temperature', 23.4)


def test_get_levels_compat(monkeypatch):
    device, _ = make_all(monkeypatch, [])
    assert device.get_levels() == (2500, 2400)


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
