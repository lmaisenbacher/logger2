# Copyright (c) 2018, Fabian Schmid, Edward Wang
# Copyright (c) 2023-2026, Lothar Maisenbacher
#
# All rights reserved.
#
# Copyright (c) 2015, Red Pitaya
"""
Module for reading out the Red Pitaya lockbox 'rp-lockbox'.
The hardware driver is `amodevices.RPLockbox`; this module adds the
logger-facing channel handling.

Channel types ('Type') and the channel keys they need:

- Per fast analog channel ('DeviceChannel': 1 or 2): `FastAnalogIn`,
  `FastAnalogOut` (V); `OutputMin`, `OutputMax` (the output limits, V);
  `GeneratorState` (1 = the signal generator on that output is enabled,
  `OUTPUT#:STATE?`; the generator adds to the PID output on the DAC, the
  PID output itself has no enable).
- Per auxiliary (slow, XADC) analog input ('DeviceChannel': 0-3):
  `AuxAnalogIn` (V).
- Per PID controller ('PID': '11', '12', '21' or '22' = input/output):
  `GlobalGain`, `PGain`, `IGain`, `IIGain`, `DGain`; `Setpoint` (V);
  `PIDEnabled` (1 = the PID + relock output is enabled - the web
  interface's switch, the setting before the external lock reset gates
  it); `HoldState`, `RelockState` (1/0); `RelockMin`, `RelockMax` (V, the
  window on the relock input inside which the PID counts as locked);
  `RelockStepsize` (V/s); `RelockInput` (the voltage on the auxiliary
  input the PID's relock feature monitors, V); `LockStatus` (below).

`LockStatus` writes its own field ('field-key', e.g. 'locked') as 1/0 on
every poll and, on the poll where the state changed (and on the first
poll after a logger start), the companion fields `LOCK_EVENT_FIELD_KEY`
('lock_event': "locked", or "unlocked: relock input 0.120 V below window
0.500-1.200 V") and `LOCK_EVENT_CODE_FIELD_KEY` ('lock_event_code': 1
locked, -1 unlocked). These are the fields the cavity pointing PID
server writes, so dashboards can share their annotation queries.
Transitions are seen at the logger's poll interval only: a lock that
drops and relocks within one interval leaves no trace here (the
lockbox's lock status DO pins are the fast signal). The lock status
needs the rp-lockbox SCPI server with the `PID:IN#:OUT#:LOCKED?` query
(newer than release 1.2.0).

The lockbox monitor service of rp-lockbox 1.3.0 (`lockbox-monitor`, which
samples the lock flags at 1 kHz on the box) adds the channel types:

- `LockDrops` (per PID): the lock drops since the previous poll under the
  channel's own field ('field-key', e.g. 'unlocks' — 0 on most polls, the
  difference of the monitor's monotonic total; a total that DECREASED
  means the monitor restarted, and the new total is taken as that poll's
  drops), with the companions `unlocks_total` (the monitor's total since
  it started), `unlocked_s` (time spent in drops since the previous poll,
  the difference of the monitor's total), `longest_unlock_s` (the longest
  drop that ended since the previous poll, from the monitor's event ring;
  0 when none) and `servo_drops` (the drops since the hold was last
  switched off — the web interface's "drops since servo on").
- `FastAnalogInRMS` (per fast analog channel): the standard deviation of
  the input over the monitor's last window (V; the input noise, in lock
  the rms error) under the channel's own field, with the companions
  `decimation` (the scope decimation the samples were averaged over,
  which sets the bandwidth) and `window_s` (the window length).
- `FastAnalogInMean` (per fast analog channel): the mean of the input
  over that window (V), a plain value.

While the monitor service is not running these channels write nothing
(reading None; one warning per outage); the other channels are not
affected. The monitor's health (`LOCK:MON?`) is asked once per poll and
gates the rest, because the SCPI server answers a monitor query with an
error while the service is down, which the client only sees as a
receive timeout.

rp-lockbox 1.4.0 gives each PID two parameter sets, selected by a digital
input or fixed. The setpoint, the gains (`GlobalGain`, `PGain`, `IGain`,
`IIGain`, `DGain`), the lock window (`RelockMin`, `RelockMax`) and the new
`Holdoff` (s: after a switch into the set, the lock status cannot go
from locked to unlocked for this time) exist once per set: their channels
take the optional key 'Set' (1, the default, or 2). Set 1 is read with
the commands of the earlier releases, so a configuration without 'Set'
works with any lockbox. Version 1.4.0 adds the channel types:

- `ParamSet` (per PID): the parameter set in use at the poll (1 or 2)
  under the channel's own field, with the companion `pset_mode` (the
  selection: 'PSET1', 'PSET2', 'HIGH2' = set 2 while the input is high,
  'HIGH1' = set 1 while it is high). The set can switch far faster than
  the poll; `ParamSetSwitches` counts the switches.
- `ParamSetSwitches` (per PID): from the FPGA's event counters, each as
  the events since the previous poll: the parameter set switches under
  the channel's own field (e.g. 'switches'), with the companions
  `holdoffs_went_outside` (holdoffs during which the relock input went
  outside the lock window: the holdoff kept a lock drop from being
  counted) and `holdoffs_ended_outside` (holdoffs that ended with it
  still outside: the lock status drops then). The first poll writes zeros
  (no baseline). The counters run since the FPGA was loaded and wrap at
  2^32; a difference beyond 2^31 is taken as a reload, and the new count
  as that poll's events. They need no monitor service.

With version 1.4.0 a `LockDrops` channel also writes `short_drops`: how
many of the poll's drops fell between two of the monitor's polls (the
monitor sees them in the FPGA's unlock counter); they are included in
its own field. An FPGA image without the counters makes the query fail
once, and the field is left out from then on. A lock event's window is
the one of the parameter set in use at the poll, named in the text when
it is set 2 ("... window 0.500-1.200 V of parameter set 2").

Booleans are logged as 0/1 integers (InfluxDB would type a Python bool
as a boolean field). Within one poll every SCPI query is sent once: the
readers share a per-poll memo, so the relock input read for a lock
event's reason is not repeated for a `RelockInput` channel of the same
PID, and one `MONitor?` per PID serves all its lock-drop fields.
"""

import logging

from amodevices import RPLockbox
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)

#: Companion fields of a `LockStatus` channel, written on a transition
LOCK_EVENT_FIELD_KEY = 'lock_event'
LOCK_EVENT_CODE_FIELD_KEY = 'lock_event_code'
#: `lock_event_code` per lock state (the pointing PID server's codes:
#: 1 = servoing, -1 = off)
LOCK_EVENT_CODES = {True: 1, False: -1}

#: Companion fields of a `LockDrops` channel
UNLOCKS_TOTAL_FIELD_KEY = 'unlocks_total'
UNLOCKED_TIME_FIELD_KEY = 'unlocked_s'
LONGEST_UNLOCK_FIELD_KEY = 'longest_unlock_s'
SERVO_DROPS_FIELD_KEY = 'servo_drops'
SHORT_DROPS_FIELD_KEY = 'short_drops'
#: Companion fields of a `FastAnalogInRMS` channel
DECIMATION_FIELD_KEY = 'decimation'
WINDOW_FIELD_KEY = 'window_s'
#: The channel types served by the lockbox monitor service
MONITOR_TYPES = ('LockDrops', 'FastAnalogInRMS', 'FastAnalogInMean')

#: Companion field of a `ParamSet` channel
PSET_MODE_FIELD_KEY = 'pset_mode'
#: The fields of a `ParamSetSwitches` channel per driver counter: None = the
#: channel's own field
SWITCH_COUNTER_FIELD_KEYS = {
    'switches': None, 'holdoffs_went_outside': 'holdoffs_went_outside',
    'holdoffs_ended_outside': 'holdoffs_ended_outside'}
#: The FPGA's event counters wrap at 2^32
COUNTER_MODULUS = 2**32


class Device(RPLockbox):

    #: Channel types read with one driver query: type -> (what the
    #: query takes - the 'DeviceChannel' or the two PID indices -, the
    #: driver method, the type the value is logged as)
    SIMPLE_READERS = {
        'FastAnalogIn':   ('channel', 'get_fast_analog_input', float),
        'FastAnalogOut':  ('channel', 'get_fast_analog_output', float),
        'OutputMin':      ('channel', 'get_output_minimum', float),
        'OutputMax':      ('channel', 'get_output_maximum', float),
        'GeneratorState': ('channel', 'get_output_state', int),
        'AuxAnalogIn':    ('channel', 'get_aux_analog_input', float),
        'GlobalGain':     ('pid', 'get_kg', float),
        'PGain':          ('pid', 'get_kp', float),
        'IGain':          ('pid', 'get_ki', float),
        'IIGain':         ('pid', 'get_kii', float),
        'DGain':          ('pid', 'get_kd', float),
        'Setpoint':       ('pid', 'get_setpoint', float),
        'HoldState':      ('pid', 'get_hold_state', int),
        'PIDEnabled':     ('pid', 'get_pid_enabled', int),
        'RelockState':    ('pid', 'get_relock_state', int),
        'RelockMin':      ('pid', 'get_relock_minimum', float),
        'RelockMax':      ('pid', 'get_relock_maximum', float),
        'RelockStepsize': ('pid', 'get_relock_stepsize', float),
        'Holdoff':        ('pid', 'get_relock_holdoff', float),
    }
    #: The channel types of a parameter set (the key 'Set')
    PSET_TYPES = ('GlobalGain', 'PGain', 'IGain', 'IIGain', 'DGain', 'Setpoint',
                  'RelockMin', 'RelockMax', 'Holdoff')
    #: Every channel type, for the configuration check
    CHANNEL_TYPES = (tuple(SIMPLE_READERS) + ('RelockInput', 'LockStatus') + MONITOR_TYPES
                     + ('ParamSet', 'ParamSetSwitches'))

    def __init__(self, device):
        for channel_id, chan in device['Channels'].items():
            where = f'channel \'{channel_id}\' of device \'{device["Device"]}\''
            if chan.get('Type') not in self.CHANNEL_TYPES:
                raise DeviceError(
                    f'Unknown channel type \'{chan.get("Type")}\' for {where}'
                    f' (one of {", ".join(self.CHANNEL_TYPES)})')
            if 'Set' in chan:
                if chan['Type'] not in self.PSET_TYPES:
                    raise DeviceError(
                        f'The key \'Set\' of {where}: a \'{chan["Type"]}\' channel has no'
                        f' parameter set (only {", ".join(self.PSET_TYPES)})')
                if chan['Set'] not in RPLockbox.PSETS or isinstance(chan['Set'], bool):
                    raise DeviceError(
                        f'Invalid parameter set {chan["Set"]!r} for {where} (1 or 2)')
        super().__init__(device)
        # Lock state per `LockStatus` channel at the previous poll (absent
        # = no poll yet)
        self._last_locked = {}
        # Per `LockDrops` channel: the monitor's totals and the newest
        # event index at the previous poll (absent = no poll yet)
        self._last_drops = {}
        # The monitor's state at the previous poll, for the outage warning
        self._monitor_was_alive = None
        # Per `ParamSetSwitches` channel: the counts at the previous poll
        self._last_counters = {}
        # False once a lockbox showed it has no parameter sets / no short
        # drop count (its FPGA image predates them): not asked again
        self._has_psets = True
        self._has_short_drops = True
        # Per-poll memo of driver query results (see the module docstring)
        self._memo = {}

    def get_device_channel(self, channel_id, chan):
        """Get device channel from channel definition `chan` for channel with ID `channel_id`"""
        device_channel = chan.get('DeviceChannel')
        if device_channel is None:
            raise DeviceError(
                'Could not get required property \'DeviceChannel\' for channel \'%s\'', channel_id)
        return device_channel

    def get_pid_channels(self, channel_id, chan):
        """
        Get PID channels (input 1 or 2, output 1 or 2) from string `pid` (e.g., '12' for input 1
        and output 2), which is stored in channel definition `chan['PID']` for channel with ID
        `channel_id`.
        """
        pid = chan.get('PID')
        if pid is None:
            raise DeviceError(
                f'Could not get required property \'PID\' for channel \'{channel_id}\'')
        try:
            pid_channels = [int(pid[0]), int(pid[1])]
        except ValueError:
            raise DeviceError(
                f'Invalid PID controller \'{pid}\' defined for channel \'{channel_id}\''
                +' (in field \'PID\'; valid values: \'11\', \'12\', \'21\', \'22\')')
        return pid_channels

    def _query(self, method, *args):
        """The driver query `method` (its name) with `args`, sent once per
        poll: a repeat within the same poll returns the memoized result."""
        key = (method, args)
        if key not in self._memo:
            self._memo[key] = getattr(self, method)(*args)
        return self._memo[key]

    def _read_relock_input(self, channel_id, chan):
        """The voltage on the auxiliary input the PID's relock feature
        monitors (the input is the lockbox's setting, not the channel's)."""
        num_in, num_out = self.get_pid_channels(channel_id, chan)
        pin = self._query('get_relock_input', num_in, num_out)
        return float(self._query('get_aux_analog_input', pin))

    def _active_pset(self, num_in, num_out):
        """The parameter set the PID uses at this poll; 1 with a lockbox
        without parameter sets (its FPGA image answers the query with an
        error, seen as a timeout: asked once)."""
        if not self._has_psets:
            return 1
        try:
            return self._query('get_active_pset', num_in, num_out)
        except DeviceError as err:
            self._has_psets = False
            logger.warning(
                '\'%s\': no parameter set in use readable (%s): lock events quote the window'
                ' of parameter set 1 until the logger restarts', self.device['Device'], err)
            return 1

    def _lock_event(self, num_in, num_out, locked):
        """The lock event text: "locked", or "unlocked: ..." with where the
        relock input sits relative to the window, both at this poll (the
        window of the parameter set in use, named when it is set 2)."""
        if locked:
            return 'locked'
        pin = self._query('get_relock_input', num_in, num_out)
        voltage = self._query('get_aux_analog_input', pin)
        pset = self._active_pset(num_in, num_out)
        vmin = self._query('get_relock_minimum', num_in, num_out, pset)
        vmax = self._query('get_relock_maximum', num_in, num_out, pset)
        window = f'window {vmin:.3f}-{vmax:.3f} V'
        if pset != 1:
            window += f' of parameter set {pset}'
        if voltage < vmin:
            return f'unlocked: relock input {voltage:.3f} V below {window}'
        if voltage > vmax:
            return f'unlocked: relock input {voltage:.3f} V above {window}'
        # The status and the voltage are separate reads; a dip that ended
        # between them shows the input back inside the window
        return f'unlocked: relock input {voltage:.3f} V, {window}'

    def _read_lock_status(self, channel_id, chan):
        """1/0 every poll; the lock-event companions on a change and on the
        first poll (see the module docstring)."""
        num_in, num_out = self.get_pid_channels(channel_id, chan)
        locked = bool(self._query('get_lock_status', num_in, num_out))
        first_poll = channel_id not in self._last_locked
        changed = first_poll or locked != self._last_locked[channel_id]
        self._last_locked[channel_id] = locked
        reading = {chan['field-key']: int(locked)}
        if not changed:
            return reading
        event = self._lock_event(num_in, num_out, locked)
        if first_poll:
            event += ' (logger started)'
        logger.info('\'%s\': %s: %s', self.device['Device'], channel_id, event)
        reading[LOCK_EVENT_FIELD_KEY] = event
        reading[LOCK_EVENT_CODE_FIELD_KEY] = LOCK_EVENT_CODES[locked]
        return reading

    def _monitor_alive(self):
        """Whether the lockbox monitor service runs (asked once per poll; the
        one monitor query that answers while it is down). Logs the outage
        and the recovery once each."""
        alive = bool(self._query('get_monitor_health')['alive'])
        if alive != self._monitor_was_alive:
            if alive:
                if self._monitor_was_alive is False:
                    logger.info('\'%s\': the lockbox monitor service is running again',
                                self.device['Device'])
            else:
                logger.warning(
                    '\'%s\': the lockbox monitor service is not running - its channels'
                    ' (%s) write nothing until it is', self.device['Device'],
                    ', '.join(MONITOR_TYPES))
            self._monitor_was_alive = alive
        return alive

    def _read_lock_drops(self, channel_id, chan):
        """The drops since the previous poll under the channel's own field,
        with the monitor's totals and the longest drop that ended since
        (see the module docstring)."""
        num_in, num_out = self.get_pid_channels(channel_id, chan)
        monitor = self._query('get_pid_monitor', num_in, num_out)
        total = int(monitor['unlocks_total'])
        unlocked_s = float(monitor['unlocked_total_s'])
        last = self._last_drops.get(channel_id)
        restarted = last is not None and total < last['total']
        if last is None:
            # No baseline yet: nothing dropped "since the previous poll"
            drops, unlocked_delta, after = 0, 0.0, 0
        elif restarted:
            # The monitor restarted: its totals began anew
            logger.info('\'%s\': %s: the lockbox monitor restarted (total %d after %d)',
                        self.device['Device'], channel_id, total, last['total'])
            drops, unlocked_delta, after = total, unlocked_s, 0
        else:
            drops = total - last['total']
            unlocked_delta = max(unlocked_s - last['unlocked_s'], 0.0)
            after = last['index']
        # The drops that ENDED since the previous poll come from the
        # monitor's ring (a drop counts at its start and lands in the ring
        # at its close, so a drop open at the previous poll is looked up
        # too); the cursor is the newest index seen
        longest, index = 0.0, after
        if last is None or restarted or drops > 0 or last['open']:
            events = self._query('get_unlock_events', num_in, num_out, after)
            if last is not None:
                longest = max((event['duration_s'] for event in events), default=0.0)
            index = max((event['index'] for event in events), default=after)
        short_total = self._short_drop_total(num_in, num_out)
        self._last_drops[channel_id] = {
            'total': total, 'unlocked_s': unlocked_s, 'index': index,
            'open': bool(monitor['drop_open']), 'short': short_total}
        reading = {
            chan['field-key']: drops,
            UNLOCKS_TOTAL_FIELD_KEY: total,
            UNLOCKED_TIME_FIELD_KEY: unlocked_delta,
            LONGEST_UNLOCK_FIELD_KEY: longest,
            SERVO_DROPS_FIELD_KEY: int(monitor['unlocks_since_servo']),
        }
        if short_total is not None:
            # Of the poll's drops, the short ones, by the same baseline rule
            if last is None or last['short'] is None:
                reading[SHORT_DROPS_FIELD_KEY] = 0
            elif restarted:
                reading[SHORT_DROPS_FIELD_KEY] = short_total
            else:
                reading[SHORT_DROPS_FIELD_KEY] = max(short_total - last['short'], 0)
        return reading

    def _short_drop_total(self, num_in, num_out):
        """The monitor's count of short drops since it started, or None with
        an FPGA image without the event counters (its error is seen as a
        timeout: asked once)."""
        if not self._has_short_drops:
            return None
        try:
            return int(self._query('get_short_unlock_count', num_in, num_out))
        except DeviceError as err:
            self._has_short_drops = False
            logger.warning(
                '\'%s\': no short drop count readable (%s): the lock drop channels write no'
                ' \'%s\' until the logger restarts', self.device['Device'], err,
                SHORT_DROPS_FIELD_KEY)
            return None

    def _read_param_set(self, channel_id, chan):
        """The parameter set in use, with the selection mode."""
        num_in, num_out = self.get_pid_channels(channel_id, chan)
        return {
            chan['field-key']: int(self._query('get_active_pset', num_in, num_out)),
            PSET_MODE_FIELD_KEY: self._query('get_pset_mode', num_in, num_out),
        }

    def _read_switch_counters(self, channel_id, chan):
        """The FPGA's events since the previous poll (see the module
        docstring)."""
        num_in, num_out = self.get_pid_channels(channel_id, chan)
        counts = self._query('get_counters', num_in, num_out)
        last = self._last_counters.get(channel_id)
        self._last_counters[channel_id] = dict(counts)
        if last is None:
            # No baseline yet: no events "since the previous poll"
            events = {counter: 0 for counter in SWITCH_COUNTER_FIELD_KEYS}
        else:
            events = {counter: (counts[counter] - last[counter]) % COUNTER_MODULUS
                      for counter in SWITCH_COUNTER_FIELD_KEYS}
            if any(n >= COUNTER_MODULUS // 2 for n in events.values()):
                # Not a wrap: the FPGA was loaded again, all counts began anew
                logger.info('\'%s\': %s: the event counters began anew (FPGA reloaded): %s',
                            self.device['Device'], channel_id, counts)
                events = {counter: counts[counter] for counter in SWITCH_COUNTER_FIELD_KEYS}
        return {field_key or chan['field-key']: events[counter]
                for counter, field_key in SWITCH_COUNTER_FIELD_KEYS.items()}

    def _read_input_rms(self, channel_id, chan):
        """The input's standard deviation over the monitor's last window,
        with the decimation and the window length as companions."""
        stats = self._query('get_fast_analog_input_stats',
                            self.get_device_channel(channel_id, chan))
        if stats['age_s'] < 0:
            # No window yet (the monitor just started)
            return None
        return {
            chan['field-key']: float(stats['sd_v']),
            DECIMATION_FIELD_KEY: int(stats['decimation']),
            WINDOW_FIELD_KEY: float(stats['window_s']),
        }

    def _read_input_mean(self, channel_id, chan):
        """The input's mean over the monitor's last window."""
        stats = self._query('get_fast_analog_input_stats',
                            self.get_device_channel(channel_id, chan))
        if stats['age_s'] < 0:
            return None
        return float(stats['mean_v'])

    def get_values(self):
        """Read every channel once (one poll)."""
        self._memo.clear()
        readings = {}
        monitor_alive = None
        for channel_id, chan in self.device['Channels'].items():
            ctype = chan['Type']
            if ctype in self.SIMPLE_READERS:
                source, method, as_type = self.SIMPLE_READERS[ctype]
                args = ([self.get_device_channel(channel_id, chan)]
                        if source == 'channel'
                        else self.get_pid_channels(channel_id, chan))
                if ctype in self.PSET_TYPES:
                    args.append(chan.get('Set', 1))
                readings[channel_id] = as_type(self._query(method, *args))
            elif ctype == 'RelockInput':
                readings[channel_id] = self._read_relock_input(channel_id, chan)
            elif ctype == 'LockStatus':
                readings[channel_id] = self._read_lock_status(channel_id, chan)
            elif ctype == 'ParamSet':
                readings[channel_id] = self._read_param_set(channel_id, chan)
            elif ctype == 'ParamSetSwitches':
                readings[channel_id] = self._read_switch_counters(channel_id, chan)
            elif ctype in MONITOR_TYPES:
                if monitor_alive is None:
                    monitor_alive = self._monitor_alive()
                if not monitor_alive:
                    readings[channel_id] = None
                elif ctype == 'LockDrops':
                    readings[channel_id] = self._read_lock_drops(channel_id, chan)
                elif ctype == 'FastAnalogInRMS':
                    readings[channel_id] = self._read_input_rms(channel_id, chan)
                else:
                    readings[channel_id] = self._read_input_mean(channel_id, chan)
            else:
                raise DeviceError(
                    f'Unknown channel type \'{ctype}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
