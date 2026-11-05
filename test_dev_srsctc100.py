# -*- coding: utf-8 -*-
"""Tests for `dev_srsctc100.Device` — the logger-facing CTC100 channel
handling — against a fake serial port answering with the replies of the
lab's science-chamber CTC100 (2026-11-05). Runs under pytest
(`pytest test_dev_srsctc100.py`) or directly as a script.
"""

import copy
import sys

import pytest

import dev_srsctc100
from amodevices.dev_exceptions import DeviceError

NAMES = (b'X207812, X207813, X233659, X233660, HEATER1, 50KHeat, AIO 1, '
         b'AIO 2, AIO 3, AIO 4, DIO, Relays, V1, V2, V3\r\n')
OUTPUT = (b'7.892, 7.832, 7.857, 7.765, 0.000, 1.500, 0.628, 0.628, 0.628, '
          b'1.442, 0, 0, NaN, NaN, NaN\r\n')
REPLIES = {'getOutputNames?': NAMES, 'getOutput?': OUTPUT,
           'HEATER1.PID.Setpoint?': b'284.000\r\n',
           '50KHeat.PID.Setpoint?': b'295.000\r\n'}

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
    just sent and whose `readline` returns it (b'' when nothing is
    queued, as a timeout would)."""

    def __init__(self, replies):
        self.replies = dict(replies)
        self.sent = []
        self.incoming = b''

    def write(self, data):
        command = data.decode('ascii').rstrip('\n')
        self.sent.append(command)
        self.incoming += self.replies.get(command, b'')
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
    """A `dev_srsctc100.Device` connected to a `FakeSerial`."""
    fake = FakeSerial(replies)
    dev = dev_srsctc100.Device(copy.deepcopy(device))
    dev.serial_connect = lambda: setattr(dev, 'ser', fake)
    dev.serial_close = lambda: None
    dev.connect()
    return dev, fake


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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-v']))
