# -*- coding: utf-8 -*-
"""Tests for `dev_highfinesse.Device` — the logger-facing HighFinesse
channel handling — against a fake wavemeter DLL. Runs under pytest
(`pytest test_dev_highfinesse.py`) or directly as a script.
"""

import ctypes
import logging
import math
import time

import pytest

import dev_highfinesse
from amodevices.dev_exceptions import DeviceError
from readings import reading_fields


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
    (ErrNoValue) once they run out."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.pulse_mode = 1

    def _get_frequency(self, *args):
        self.calls += 1
        if not self.results:
            return 0.0
        return self.results.pop(0)

    def __getattr__(self, name):
        impl = {
            'Instantiate': lambda *a: 1,
            'GetPulseMode': lambda *a: self.pulse_mode,
            'GetFrequencyNum': self._get_frequency,
        }.get(name, lambda *a: 0)
        entry = _Entry(impl)
        self.__dict__[name] = entry
        return entry


# No 'status-field-key': the status is on by default, as 'status'
CHANNEL = {'Type': 'Frequency', 'field-key': 'frequency', 'Unit': 'GHz',
           'tags': {'unit': 'GHz'}}


def make_device(monkeypatch, results, channels=None, pulse_mode=1):
    dll = FakeDLL(results)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda path: dll, raising=False)
    if channels is None:
        channels = {'Frequency': dict(CHANNEL)}
    device = dev_highfinesse.Device({
        'Device': 'Fake WS/7', 'Model': 'HighFinesse', 'ReadOnce': True,
        'PulseMode': pulse_mode, 'tags': {}, 'measurement': 'wavemeter',
        'Channels': channels,
    })
    return device, dll


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
            'X': {'Type': 'Power', 'field-key': 'x'}})
    with pytest.raises(DeviceError, match='Unit'):
        make_device(monkeypatch, [], channels={
            'F': {'Type': 'Frequency', 'field-key': 'f', 'Unit': 'nm'}})


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


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
