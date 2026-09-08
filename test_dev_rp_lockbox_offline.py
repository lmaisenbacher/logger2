# -*- coding: utf-8 -*-
"""Offline test of `dev_rp_lockbox`: a fake SCPI transport answers the
driver's queries, no lockbox needed (`test_dev_rp_lockbox.py` is the
live-device script).

Run from the logger2 directory: ``python -m pytest test_dev_rp_lockbox_offline.py``
"""

import pytest

import dev_rp_lockbox
from amodevices.dev_exceptions import DeviceError
from readings import reading_fields


class FakeLockbox(dev_rp_lockbox.Device):
    """The logger's device class over a scripted SCPI transport: `answers`
    maps each query the driver sends to the lockbox's reply; `sent` records
    every query."""

    def __init__(self, device, answers):
        self.answers = answers
        self.sent = []
        super().__init__(device)

    def connect(self):
        pass

    def close(self):
        pass

    def txrx_txt(self, msg):
        self.sent.append(msg)
        return self.answers[msg]


def channel(ctype, field_key, **keys):
    return {'Type': ctype, 'field-key': field_key, 'tags': {}, **keys}


CHANNELS = {
    'Analog in 2': channel('FastAnalogIn', 'voltage', DeviceChannel=2),
    'Analog out 2': channel('FastAnalogOut', 'voltage', DeviceChannel=2),
    'Output limit min 2': channel('OutputMin', 'voltage', DeviceChannel=2),
    'Output limit max 2': channel('OutputMax', 'voltage', DeviceChannel=2),
    'Output enabled 2': channel('OutputState', 'enabled', DeviceChannel=2),
    'Aux in 1': channel('AuxAnalogIn', 'voltage', DeviceChannel=1),
    'P gain PID22': channel('PGain', 'gain', PID='22'),
    'II gain PID22': channel('IIGain', 'gain', PID='22'),
    'Setpoint PID22': channel('Setpoint', 'voltage', PID='22'),
    'Hold PID22': channel('HoldState', 'enabled', PID='22'),
    'Relock enabled PID22': channel('RelockState', 'enabled', PID='22'),
    'Relock window min PID22': channel('RelockMin', 'voltage', PID='22'),
    'Relock window max PID22': channel('RelockMax', 'voltage', PID='22'),
    'Relock step PID22': channel('RelockStepsize', 'slew', PID='22'),
    'Relock input PID22': channel('RelockInput', 'voltage', PID='22'),
    'Lock status PID22': channel('LockStatus', 'locked', PID='22'),
}

DEVICE = {
    'Device': 'Fake lockbox', 'Address': 'localhost', 'Timeout': 1.,
    'SCPIConnectionParams': {'Port': 5000}, 'Channels': CHANNELS,
}

ANSWERS = {
    'ANALOG:IN2:VOLT?': '-0.0125', 'ANALOG:OUT2:VOLT?': '0.75',
    'OUT2:LIM:MIN?': '-0.9', 'OUT2:LIM:MAX?': '0.9', 'OUTPUT2:STATE?': '1',
    'ANALOG:PIN? AIN1': '0.8125',
    'PID:IN2:OUT2:KP?': '0.5', 'PID:IN2:OUT2:KII?': '2000',
    'PID:IN2:OUT2:SETPoint?': '0.1', 'PID:IN2:OUT2:HOLD?': 'OFF',
    'PID:IN2:OUT2:REL?': 'ON', 'PID:IN2:OUT2:REL:MIN?': '0.5',
    'PID:IN2:OUT2:REL:MAX?': '1.2', 'PID:IN2:OUT2:REL:STEP?': '10',
    'PID:IN2:OUT2:REL:INP?': 'AIN1', 'PID:IN2:OUT2:LOCKED?': 'ON',
}


def fields(device, readings):
    """{channel id: {field key: value}} as the logger would write them."""
    return {channel_id: reading_fields(CHANNELS[channel_id], reading)
            for channel_id, reading in readings.items()}


def test_every_channel_type_reads_its_value():
    box = FakeLockbox(DEVICE, dict(ANSWERS))
    got = fields(box, box.get_values())
    assert got['Analog in 2'] == {'voltage': -0.0125}
    assert got['Analog out 2'] == {'voltage': 0.75}
    assert got['Output limit min 2'] == {'voltage': -0.9}
    assert got['Output limit max 2'] == {'voltage': 0.9}
    assert got['Aux in 1'] == {'voltage': 0.8125}
    assert got['P gain PID22'] == {'gain': 0.5}
    assert got['II gain PID22'] == {'gain': 2000.0}
    assert got['Setpoint PID22'] == {'voltage': 0.1}
    assert got['Relock window min PID22'] == {'voltage': 0.5}
    assert got['Relock window max PID22'] == {'voltage': 1.2}
    assert got['Relock step PID22'] == {'slew': 10.0}
    # The relock input follows the lockbox's own input selection (AIN1)
    assert got['Relock input PID22'] == {'voltage': 0.8125}


def test_booleans_are_logged_as_integers():
    box = FakeLockbox(DEVICE, dict(ANSWERS))
    got = fields(box, box.get_values())
    for channel_id, expected in (('Output enabled 2', 1), ('Hold PID22', 0),
                                 ('Relock enabled PID22', 1),
                                 ('Lock status PID22', 1)):
        value = got[channel_id][CHANNELS[channel_id]['field-key']]
        assert value == expected and type(value) is int, channel_id


def test_lock_events_on_transitions_only():
    answers = dict(ANSWERS)
    box = FakeLockbox(DEVICE, answers)
    # First poll: the state at logger start is an event of its own
    got = fields(box, box.get_values())['Lock status PID22']
    assert got == {'locked': 1, 'lock_event': 'locked (logger started)',
                   'lock_event_code': 1}
    # Unchanged: the value only
    assert fields(box, box.get_values())['Lock status PID22'] == {'locked': 1}
    # The lock drops, the relock input is below the window
    answers['PID:IN2:OUT2:LOCKED?'] = 'OFF'
    answers['ANALOG:PIN? AIN1'] = '0.12'
    got = fields(box, box.get_values())['Lock status PID22']
    assert got == {
        'locked': 0,
        'lock_event': 'unlocked: relock input 0.120 V below window 0.500-1.200 V',
        'lock_event_code': -1}
    assert fields(box, box.get_values())['Lock status PID22'] == {'locked': 0}
    # Above the window
    answers['ANALOG:PIN? AIN1'] = '1.5'
    box._last_locked.clear()          # force an event without a state change
    got = fields(box, box.get_values())['Lock status PID22']
    assert got['lock_event'] == (
        'unlocked: relock input 1.500 V above window 0.500-1.200 V (logger started)')
    # Relocked
    answers['PID:IN2:OUT2:LOCKED?'] = 'ON'
    got = fields(box, box.get_values())['Lock status PID22']
    assert got == {'locked': 1, 'lock_event': 'locked', 'lock_event_code': 1}


def test_each_query_is_sent_once_per_poll():
    answers = dict(ANSWERS)
    answers['PID:IN2:OUT2:LOCKED?'] = 'OFF'      # the event reason reads the input + window
    box = FakeLockbox(DEVICE, answers)
    box.get_values()
    assert len(box.sent) == len(set(box.sent)), box.sent
    for query in ('ANALOG:PIN? AIN1', 'PID:IN2:OUT2:REL:INP?',
                  'PID:IN2:OUT2:REL:MIN?', 'PID:IN2:OUT2:REL:MAX?'):
        assert box.sent.count(query) == 1
    # A new poll sends them again
    box.get_values()
    assert box.sent.count('PID:IN2:OUT2:LOCKED?') == 2


def test_unknown_channel_type_is_refused_at_startup():
    device = dict(DEVICE, Channels={'Bad': channel('Gain', 'gain', PID='22')})
    with pytest.raises(DeviceError, match='Unknown channel type'):
        FakeLockbox(device, {})
