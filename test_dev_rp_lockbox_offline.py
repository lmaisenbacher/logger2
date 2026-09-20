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
    'Generator output 2': channel('GeneratorState', 'enabled', DeviceChannel=2),
    'Aux in 1': channel('AuxAnalogIn', 'voltage', DeviceChannel=1),
    'P gain PID22': channel('PGain', 'gain', PID='22'),
    'II gain PID22': channel('IIGain', 'gain', PID='22'),
    'Setpoint PID22': channel('Setpoint', 'voltage', PID='22'),
    'Hold PID22': channel('HoldState', 'enabled', PID='22'),
    'PID output PID22': channel('PIDEnabled', 'enabled', PID='22'),
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
    'PID:IN2:OUT2:ENAB?': 'ON',
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
    for channel_id, expected in (('Generator output 2', 1), ('Hold PID22', 0),
                                 ('PID output PID22', 1),
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


# The lockbox monitor service's channel types (rp-lockbox 1.3.0)

MONITOR_CHANNELS = {
    'Analog in 2': channel('FastAnalogIn', 'voltage', DeviceChannel=2),
    'Lock drops PID22': channel('LockDrops', 'unlocks', PID='22'),
    'Input noise 2': channel('FastAnalogInRMS', 'voltage', DeviceChannel=2),
    'Input mean 2': channel('FastAnalogInMean', 'voltage', DeviceChannel=2),
}

MONITOR_DEVICE = dict(DEVICE, Channels=MONITOR_CHANNELS)

MONITOR_ANSWERS = {
    'ANALOG:IN2:VOLT?': '-0.0125',
    'LOCK:MON?': '1,98765.4,1.000,2.3,4,10.0',
    # locked,lock_age_s,servo_on,servo_age_s,unlocks_total,unlocked_total_s,
    # unlocks_since_servo,unlocked_since_servo_s,longest_since_servo_s,
    # drop_open,last_unlock_age_s,last_unlock_s,raw_unlock_edges
    'PID:IN2:OUT2:MON?': '1,4321.2,1,5000.1,17,3.2,3,0.9,0.4,0,1500.2,0.4,20',
    'PID:IN2:OUT2:UNL:EVEN? 0': '2,16,100.5,0.003,17,30.1,0.010',
    'ANALOG:IN2:STAT?': '0.25,0.0021,0.241,0.259,1.074,0.31,1024',
}


def monitor_fields(box, readings):
    return {channel_id: reading_fields(MONITOR_CHANNELS[channel_id], reading)
            for channel_id, reading in readings.items()}


def test_monitor_channels_on_the_first_poll():
    box = FakeLockbox(MONITOR_DEVICE, dict(MONITOR_ANSWERS))
    got = monitor_fields(box, box.get_values())
    # No baseline yet: nothing dropped "since the previous poll", the totals
    # and the servo count as they are
    assert got['Lock drops PID22'] == {
        'unlocks': 0, 'unlocks_total': 17, 'unlocked_s': 0.0, 'longest_unlock_s': 0.0,
        'servo_drops': 3}
    assert got['Input noise 2'] == {'voltage': 0.0021, 'decimation': 1024, 'window_s': 1.074}
    assert got['Input mean 2'] == {'voltage': 0.25}
    assert got['Analog in 2'] == {'voltage': -0.0125}
    # The ring was read once to place the cursor at the newest drop
    assert box.sent.count('PID:IN2:OUT2:UNL:EVEN? 0') == 1
    assert box._last_drops['Lock drops PID22']['index'] == 17
    # One MONitor? and one STATs? serve every field of the poll
    assert box.sent.count('PID:IN2:OUT2:MON?') == 1
    assert box.sent.count('ANALOG:IN2:STAT?') == 1
    assert box.sent.count('LOCK:MON?') == 1


def test_lock_drops_are_the_difference_between_polls():
    answers = dict(MONITOR_ANSWERS)
    box = FakeLockbox(MONITOR_DEVICE, answers)
    box.get_values()
    # Two drops since: the total 19, 0.15 s more unlocked, both in the ring
    answers['PID:IN2:OUT2:MON?'] = '1,10.0,1,5200.1,19,3.35,5,1.05,0.12,0,2.0,0.120,22'
    answers['PID:IN2:OUT2:UNL:EVEN? 17'] = '2,18,5.0,0.006,19,2.0,0.120'
    got = monitor_fields(box, box.get_values())['Lock drops PID22']
    assert got['unlocks'] == 2
    assert got['unlocks_total'] == 19
    assert got['unlocked_s'] == pytest.approx(0.15)
    assert got['longest_unlock_s'] == pytest.approx(0.120)
    assert got['servo_drops'] == 5
    # Nothing new: zeros, and the ring is not read
    sent_before = len(box.sent)
    got = monitor_fields(box, box.get_values())['Lock drops PID22']
    assert got == {'unlocks': 0, 'unlocks_total': 19, 'unlocked_s': 0.0,
                   'longest_unlock_s': 0.0, 'servo_drops': 5}
    assert not any(q.startswith('PID:IN2:OUT2:UNL:EVEN?') for q in box.sent[sent_before:])


def test_an_open_drop_is_looked_up_at_the_next_poll():
    answers = dict(MONITOR_ANSWERS)
    box = FakeLockbox(MONITOR_DEVICE, answers)
    box.get_values()
    # A drop began (total 18) and is still open: not in the ring yet
    answers['PID:IN2:OUT2:MON?'] = '0,0.4,1,5100.0,18,3.6,4,1.3,0.4,1,0.4,0.4,21'
    answers['PID:IN2:OUT2:UNL:EVEN? 17'] = '0'
    got = monitor_fields(box, box.get_values())['Lock drops PID22']
    assert got['unlocks'] == 1
    assert got['longest_unlock_s'] == 0.0
    # Closed since: no new drop, but its duration arrives now
    answers['PID:IN2:OUT2:MON?'] = '1,1.5,1,5101.0,18,3.65,4,1.35,0.45,0,1.9,0.45,21'
    answers['PID:IN2:OUT2:UNL:EVEN? 17'] = '1,18,1.9,0.45'
    got = monitor_fields(box, box.get_values())['Lock drops PID22']
    assert got['unlocks'] == 0
    assert got['longest_unlock_s'] == pytest.approx(0.45)
    assert box._last_drops['Lock drops PID22']['index'] == 18


def test_a_monitor_restart_counts_its_new_total(caplog):
    answers = dict(MONITOR_ANSWERS)
    box = FakeLockbox(MONITOR_DEVICE, answers)
    box.get_values()
    # The service restarted: the totals began anew (2 drops, 0.02 s)
    answers['PID:IN2:OUT2:MON?'] = '1,30.0,1,40.0,2,0.02,2,0.02,0.015,0,10.0,0.015,2'
    answers['PID:IN2:OUT2:UNL:EVEN? 0'] = '2,1,20.0,0.005,2,10.0,0.015'
    with caplog.at_level('INFO', logger='dev_rp_lockbox'):
        got = monitor_fields(box, box.get_values())['Lock drops PID22']
    assert got['unlocks'] == 2
    assert got['unlocks_total'] == 2
    assert got['unlocked_s'] == pytest.approx(0.02)
    assert got['longest_unlock_s'] == pytest.approx(0.015)
    assert 'the lockbox monitor restarted (total 2 after 17)' in caplog.text
    assert box._last_drops['Lock drops PID22']['index'] == 2


def test_a_stopped_monitor_writes_nothing_and_spares_the_other_channels(caplog):
    answers = dict(MONITOR_ANSWERS)
    answers['LOCK:MON?'] = '0,-1,0,0,0,0'
    box = FakeLockbox(MONITOR_DEVICE, answers)
    with caplog.at_level('WARNING', logger='dev_rp_lockbox'):
        readings = box.get_values()
    assert readings['Analog in 2'] == -0.0125
    for channel_id in ('Lock drops PID22', 'Input noise 2', 'Input mean 2'):
        assert readings[channel_id] is None
        assert reading_fields(MONITOR_CHANNELS[channel_id], None) == {}
    # The health is asked once; no monitor query is sent (each would time out)
    assert box.sent.count('LOCK:MON?') == 1
    assert not any(q.startswith(('PID:IN2:OUT2:MON', 'ANALOG:IN2:STAT')) for q in box.sent)
    assert 'the lockbox monitor service is not running' in caplog.text
    # Warned once per outage, and the recovery is noted
    caplog.clear()
    with caplog.at_level('INFO', logger='dev_rp_lockbox'):
        box.get_values()
        answers['LOCK:MON?'] = MONITOR_ANSWERS['LOCK:MON?']
        box.get_values()
    assert caplog.text.count('is not running') == 0
    assert 'running again' in caplog.text


def test_input_statistics_without_a_window_yet():
    answers = dict(MONITOR_ANSWERS)
    answers['ANALOG:IN2:STAT?'] = '0,0,0,0,0,-1,1024'
    box = FakeLockbox(MONITOR_DEVICE, answers)
    readings = box.get_values()
    assert readings['Input noise 2'] is None
    assert readings['Input mean 2'] is None


def test_driver_parsing_of_the_monitor_replies():
    box = FakeLockbox(MONITOR_DEVICE, dict(MONITOR_ANSWERS))
    monitor = box.get_pid_monitor(2, 2)
    assert monitor['locked'] is True and monitor['servo_on'] is True
    assert monitor['drop_open'] is False
    assert monitor['unlocks_total'] == 17 and type(monitor['unlocks_total']) is int
    assert monitor['last_unlock_age_s'] == pytest.approx(1500.2)
    assert box.get_unlock_events(2, 2) == [
        {'index': 16, 'age_s': 100.5, 'duration_s': 0.003},
        {'index': 17, 'age_s': 30.1, 'duration_s': 0.010}]
    health = box.get_monitor_health()
    assert health == {'alive': True, 'uptime_s': 98765.4, 'period_ms': 1.0, 'max_gap_ms': 2.3,
                      'late_polls': 4, 'merge_ms': 10.0}
    stats = box.get_fast_analog_input_stats(2)
    assert stats['sd_v'] == pytest.approx(0.0021) and stats['decimation'] == 1024
    # A monitor that is down answers with an error, which arrives as an
    # empty or short reply
    box.answers['PID:IN2:OUT2:MON?'] = ''
    with pytest.raises(DeviceError, match='lockbox monitor service may not be running'):
        box.get_pid_monitor(2, 2)
    with pytest.raises(DeviceError, match='Invalid input statistics decimation'):
        box.set_fast_analog_input_stats_decimation(4096)
