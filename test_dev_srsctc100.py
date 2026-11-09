# -*- coding: utf-8 -*-
"""Tests for `dev_srsctc100.Device` — the logger-facing CTC100 channel
handling — against a fake serial port answering with the replies of the
lab's science-chamber CTC100 (2026-11-05). Runs under pytest
(`pytest test_dev_srsctc100.py`) or directly as a script.
"""

import copy
import logging
import re
import sys

import pytest

import dev_srsctc100
from amodevices.dev_exceptions import DeviceError

NAMES = (b'X207812, X207813, X233659, X233660, HEATER1, 50KHeat, AIO 1, '
         b'AIO 2, AIO 3, AIO 4, DIO, Relays, V1, V2, V3\r\n')
OUTPUT = (b'7.892, 7.832, 7.857, 7.765, 0.000, 1.500, 0.628, 0.628, 0.628, '
          b'1.442, 0, 0, NaN, NaN, NaN\r\n')
AD_RATE = '"System.Other.A/D rate?"'
REPLIES = {'getOutputNames?': NAMES, 'getOutput?': OUTPUT,
           'HEATER1.PID.Setpoint?': b'284.000\r\n',
           '50KHeat.PID.Setpoint?': b'295.000\r\n',
           AD_RATE: b'16.7 ms\r\n'}
PERIOD_NS = 16_700_000

DEVICE = {
    'Device': 'Cryogenic temperature controller 2',
    'Model': 'SRS CTC100',
    'Address': 'COM99',
    'Channels': {
        'Temp 3 X233660': {'Type': 'Temperature',
                           'tags': {'CTC100ChannelName': 'X233660'}},
        'Temp 1 X207812': {'Type': 'Temperature',
                           'tags': {'CTC100ChannelName': 'x207812'}},
        'Heater50K': {'Type': 'HeaterPower',
                      'DeviceSpecificParams': {'MinInterval_s': 1.0},
                      'tags': {'CTC100ChannelName': '50KHeat'}},
        'Aux': {'Type': 'Temperature',
                'tags': {'CTC100ChannelName': 'AIO4'}},
        'Setpoint1': {'Type': 'PIDSetpoint',
                      'tags': {'CTC100ChannelName': 'HEATER1'}},
        'Setpoint50K': {'Type': 'PIDSetpoint',
                        'DeviceSpecificParams': {'MinInterval_s': 1.0},
                        'tags': {'CTC100ChannelName': '50KHeat'}},
    },
}


class FakeSerial:
    """A serial port whose `write` queues the scripted reply of the line
    just sent (a callable gives the next reply) and whose `readline`
    returns it (b'' when nothing is queued, as a timeout would). A Figures
    or A/D rate setting sent changes what its query replies, unless
    `settings_stick` is False."""

    SETTINGS = ((r'(\S+)\.Figures (\S+)', '{}.Figures?'),
                (r'"System\.Other\.A/D rate" = "(.+)"', AD_RATE))

    def __init__(self, replies):
        self.replies = dict(replies)
        self.sent = []
        self.incoming = b''
        self.settings_stick = True

    def write(self, data):
        command = data.decode('ascii').rstrip('\n')
        self.sent.append(command)
        for pattern, query in self.SETTINGS:
            setting = re.fullmatch(pattern, command)
            if setting is None:
                continue
            if self.settings_stick:
                self.replies[query.format(*setting.groups()[:-1])] = (
                    setting.groups()[-1].encode() + b'\r\n')
            return len(data)
        reply = self.replies.get(command, b'')
        self.incoming += reply() if callable(reply) else reply
        return len(data)

    def readline(self):
        line, sep, rest = self.incoming.partition(b'\n')
        self.incoming = rest
        return line + sep

    def reset_input_buffer(self):
        self.incoming = b''

    def close(self):
        pass


def make_device(device=DEVICE, replies=REPLIES):
    """A `dev_srsctc100.Device` connected to a `FakeSerial`, the queries
    of the connection cleared from its `sent` (`connected` has them)."""
    fake = FakeSerial(replies)
    dev = dev_srsctc100.Device(copy.deepcopy(device))
    dev.serial_connect = lambda: setattr(dev, 'ser', fake)
    dev.serial_close = lambda: None
    dev.connect()
    fake.connected = list(fake.sent)
    fake.sent.clear()
    return dev, fake


def with_params(**params):
    """`DEVICE` with 'DeviceSpecificParams' `params` on its channel
    'Temp 3 X233660'."""
    device = copy.deepcopy(DEVICE)
    device['Channels']['Temp 3 X233660']['DeviceSpecificParams'] = params
    return device


def outputs(*values):
    """A callable giving `getOutput?` replies with Cernox 3 (X233660) at
    `values` in turn, the rest as in `OUTPUT`."""
    replies = iter(values)

    def reply():
        cells = OUTPUT.decode().rstrip().split(', ')
        cells[3] = next(replies)
        return (', '.join(cells) + '\r\n').encode()
    return reply


@pytest.fixture
def wall(monkeypatch):
    """`time.time_ns` (the queries' send times) set by the test."""
    now = [1_000_000_000]
    monkeypatch.setattr(dev_srsctc100.time, 'time_ns', lambda: now[0])
    return now


@pytest.fixture
def clock(monkeypatch):
    """A monotonic clock the test advances."""
    now = [100.0]
    monkeypatch.setattr(dev_srsctc100.time, 'monotonic', lambda: now[0])
    return now


def test_one_query_for_every_output_channel(clock):
    dev, fake = make_device()
    readings = dev.get_values()
    assert readings['Temp 3 X233660'] == 7.765
    # Case and spaces as the CTC100 itself ignores them
    assert readings['Temp 1 X207812'] == 7.892
    assert readings['Aux'] == 1.442
    assert readings['Heater50K'] == 1.5
    assert readings['Setpoint1'] == 284.0
    assert readings['Setpoint50K'] == 295.0
    assert fake.sent == ['getOutputNames?', 'getOutput?',
                         'HEATER1.PID.Setpoint?', '50KHeat.PID.Setpoint?']
    fake.sent.clear()
    dev.get_values()
    # The names once per connection; the setpoint without an interval
    # every poll
    assert fake.sent == ['getOutput?', 'HEATER1.PID.Setpoint?']


def test_setpoint_read_at_most_every_min_interval(clock):
    dev, fake = make_device()
    polls = []
    for k in range(25):
        clock[0] = 100.0 + k * 0.1
        polls.append('Setpoint50K' in dev.get_values())
    # Read at 0, 1.0 and 2.0 s of the 2.5 s
    assert sum(polls) == 3
    assert polls[0] and polls[10] and polls[20]
    assert fake.sent.count('50KHeat.PID.Setpoint?') == 3
    assert fake.sent.count('getOutput?') == 25


def test_heater_power_at_most_every_min_interval(clock):
    # Taken from the getOutput? the temperatures need anyway: fewer
    # points, no extra query
    dev, fake = make_device()
    polls = []
    for k in range(25):
        clock[0] = 100.0 + k * 0.1
        readings = dev.get_values()
        polls.append('Heater50K' in readings)
        assert 'Temp 3 X233660' in readings
    assert sum(polls) == 3
    assert polls[0] and polls[10] and polls[20]
    assert fake.sent.count('getOutput?') == 25


def test_no_getoutput_when_no_output_channel_due(clock):
    device = copy.deepcopy(DEVICE)
    device['Channels'] = {name: device['Channels'][name]
                          for name in ('Heater50K', 'Setpoint50K')}
    dev, fake = make_device(device)
    assert dev.get_values() == {'Heater50K': 1.5, 'Setpoint50K': 295.0}
    clock[0] += 0.1
    assert dev.get_values() == {}
    assert fake.sent == ['getOutputNames?', 'getOutput?',
                         '50KHeat.PID.Setpoint?']


def test_unknown_ctc100_channel_refused(clock):
    device = copy.deepcopy(DEVICE)
    device['Channels']['Temp 3 X233660']['tags']['CTC100ChannelName'] = 'X999'
    dev, _ = make_device(device)
    with pytest.raises(DeviceError, match='No CTC100 channel \'X999\''):
        dev.get_values()


def test_unknown_type_refused_at_startup():
    device = copy.deepcopy(DEVICE)
    device['Channels']['Setpoint1']['Type'] = 'Setpoint'
    with pytest.raises(DeviceError, match='Unknown channel type'):
        dev_srsctc100.Device(device)


@pytest.mark.parametrize('interval', [-1, 'fast', True])
def test_bad_min_interval_refused_at_startup(interval):
    device = copy.deepcopy(DEVICE)
    device['Channels']['Setpoint50K']['DeviceSpecificParams'][
        'MinInterval_s'] = interval
    with pytest.raises(DeviceError, match='MinInterval_s'):
        dev_srsctc100.Device(device)


def test_failed_setpoint_read_retried_next_poll(clock):
    dev, fake = make_device(replies={**REPLIES, '50KHeat.PID.Setpoint?': b''})
    with pytest.raises(DeviceError, match='No response'):
        dev.get_values()
    fake.replies['50KHeat.PID.Setpoint?'] = b'295.000\r\n'
    clock[0] += 0.1
    assert dev.get_values()['Setpoint50K'] == 295.0


def test_figures_set_at_connect_where_they_differ(caplog):
    device = with_params(Figures=6)
    device['Channels']['Temp 1 X207812']['DeviceSpecificParams'] = {
        'Figures': 6}
    replies = {**REPLIES, 'X233660.Figures?': b'Default\r\n',
               'x207812.Figures?': b'6\r\n'}
    with caplog.at_level(logging.INFO, logger='dev_srsctc100'):
        dev, fake = make_device(device, replies)
    assert fake.connected == [AD_RATE, 'X233660.Figures?', 'X233660.Figures 6',
                              'X233660.Figures?', 'x207812.Figures?']
    assert 'set from Default to 6' in caplog.text
    # Again at every connection: nothing left to set
    dev.connect()
    assert fake.sent == [AD_RATE, 'X233660.Figures?', 'x207812.Figures?']


def test_figures_not_taken_warned_and_channel_read(clock, caplog):
    replies = {**REPLIES, 'X233660.Figures?': b'3\r\n'}
    fake = FakeSerial(replies)
    fake.settings_stick = False
    dev = dev_srsctc100.Device(with_params(Figures=6))
    dev.serial_connect = lambda: setattr(dev, 'ser', fake)
    dev.serial_close = lambda: None
    dev.connect()
    assert 'reads 3 after setting it to 6' in caplog.text
    assert dev.get_values()['Temp 3 X233660'] == 7.765


def test_figures_unanswered_warned_and_channel_read(clock, caplog):
    dev, _ = make_device(with_params(Figures=6))
    assert 'could not set the Figures' in caplog.text
    assert dev.get_values()['Temp 3 X233660'] == 7.765


def test_unchanged_value_left_out_until_max_unchanged(clock):
    dev, fake = make_device(with_params(MaxUnchanged_s=1.0), {
        **REPLIES, 'getOutput?': outputs(*(['7.765123'] * 13 + ['7.765124']))})
    returned = []
    for k in range(14):
        clock[0] = 100.0 + k * 0.1
        readings = dev.get_values()
        returned.append(readings.get('Temp 3 X233660'))
        # The other channels as before
        assert readings['Temp 1 X207812'] == 7.892
    # The first, again once 1 s has passed, and the changed one at once
    assert [k for k, value in enumerate(returned) if value is not None] == [
        0, 10, 13]
    assert returned[13] == 7.765124
    assert fake.sent.count('getOutput?') == 14


def test_nan_counts_as_unchanged(clock):
    dev, _ = make_device(with_params(MaxUnchanged_s=1.0), {
        **REPLIES, 'getOutput?': outputs('NaN', 'NaN', '7.765123')})
    returned = []
    for k in range(3):
        clock[0] = 100.0 + k * 0.1
        returned.append('Temp 3 X233660' in dev.get_values())
    assert returned == [True, False, True]


def test_values_stamped_in_their_conversion(clock, wall):
    dev, _ = make_device(with_params(MaxUnchanged_s=1.0), {
        **REPLIES, 'getOutput?': outputs('7.765123', '7.765123', '7.765124')})
    sent = [1_000_000_000, 1_006_000_000, 1_012_000_000]
    times = []
    for k in range(3):
        clock[0] = 100.0 + k * 0.006
        wall[0] = sent[k]
        dev.get_values()
        times.append(dict(dev.reading_times_ns))
    # First poll: ended within one A/D period before the query
    assert times[0]['Temp 3 X233660'] == sent[0] - PERIOD_NS
    # Unchanged: left out, so no time; Cernox 1, unchanged too but not
    # left out, from its last period before the query
    assert 'Temp 3 X233660' not in times[1]
    assert times[1]['Temp 1 X207812'] == sent[1] - PERIOD_NS
    # New since the previous query: ended between the two
    assert times[2]['Temp 3 X233660'] == (
        (sent[1] + sent[2]) // 2 - PERIOD_NS // 2)


def test_previous_query_older_than_a_period_bounds_nothing(clock, wall):
    dev, _ = make_device(replies={
        **REPLIES, 'getOutput?': outputs('7.765123', '7.765124')})
    dev.get_values()
    wall[0] += 1_000_000_000
    clock[0] += 1.0
    dev.get_values()
    assert dev.reading_times_ns['Temp 3 X233660'] == wall[0] - PERIOD_NS


def test_unknown_ad_rate_stamps_at_the_query(clock, wall, caplog):
    replies = dict(REPLIES)
    del replies[AD_RATE]
    dev, _ = make_device(replies=replies)
    assert 'could not read the A/D rate' in caplog.text
    dev.get_values()
    assert dev.reading_times_ns['Temp 3 X233660'] == wall[0]
    # No time for a channel read by a query of its own: the poll's
    assert 'Setpoint1' not in dev.reading_times_ns


def ad_rate_device(rate_ms):
    """`DEVICE` with 'ADRate_ms' `rate_ms`."""
    device = copy.deepcopy(DEVICE)
    device['DeviceSpecificParams'] = {'ADRate_ms': rate_ms}
    return device


@pytest.fixture
def no_settle(monkeypatch):
    monkeypatch.setattr(dev_srsctc100.Device, 'AD_RATE_SETTLE_S', 0.0)


def test_ad_rate_set_at_connect_where_it_differs(clock, wall, no_settle,
                                                 caplog):
    replies = {**REPLIES, AD_RATE: b'100 ms\r\n'}
    with caplog.at_level(logging.INFO, logger='dev_srsctc100'):
        dev, fake = make_device(ad_rate_device(16.7), replies)
    assert fake.connected == [AD_RATE, '"System.Other.A/D rate" = "16.7 ms"',
                              AD_RATE]
    assert 'A/D rate set from 100 to 16.7 ms' in caplog.text
    # The stamps take the rate read back
    dev.get_values()
    assert dev.reading_times_ns['Temp 3 X233660'] == wall[0] - PERIOD_NS
    # Again at every connection: nothing left to set
    fake.sent.clear()
    dev.connect()
    assert fake.sent == [AD_RATE]


def test_ad_rate_not_taken_warned_and_rate_read_used(clock, wall, no_settle,
                                                     caplog):
    fake = FakeSerial({**REPLIES, AD_RATE: b'100 ms\r\n'})
    fake.settings_stick = False
    dev = dev_srsctc100.Device(ad_rate_device(16.7))
    dev.serial_connect = lambda: setattr(dev, 'ser', fake)
    dev.serial_close = lambda: None
    dev.connect()
    assert 'reads 100 ms after setting it to 16.7 ms' in caplog.text
    dev.get_values()
    assert dev.reading_times_ns['Temp 3 X233660'] == wall[0] - 100_000_000


@pytest.mark.parametrize('rate', [5, 2000, '16.7 ms', True])
def test_bad_ad_rate_refused_at_startup(rate):
    with pytest.raises(DeviceError, match='ADRate_ms'):
        dev_srsctc100.Device(ad_rate_device(rate))


@pytest.mark.parametrize('params, match', [
    ({'Figures': 7}, 'integer from 0 to 6'),
    ({'Figures': True}, 'integer from 0 to 6'),
    ({'MaxUnchanged_s': -1}, 'non-negative'),
])
def test_bad_output_params_refused_at_startup(params, match):
    with pytest.raises(DeviceError, match=match):
        dev_srsctc100.Device(with_params(**params))


def test_output_params_refused_on_a_setpoint():
    device = copy.deepcopy(DEVICE)
    device['Channels']['Setpoint1']['DeviceSpecificParams'] = {'Figures': 6}
    with pytest.raises(DeviceError, match='only for a Temperature'):
        dev_srsctc100.Device(device)


def test_two_figures_for_one_ctc100_channel_refused():
    device = with_params(Figures=6)
    device['Channels']['Again'] = {
        'Type': 'Temperature', 'DeviceSpecificParams': {'Figures': 3},
        'tags': {'CTC100ChannelName': 'x233660'}}
    with pytest.raises(DeviceError, match='another channel sets 6'):
        dev_srsctc100.Device(device)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
