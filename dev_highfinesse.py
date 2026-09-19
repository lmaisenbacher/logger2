# -*- coding: utf-8 -*-
"""
This module contains drivers for the HighFinesse wavemeters
(tested with model WS/7),
which are interfaced through a Windows DLL API.
The wavemeter software must be running on the same PC.
The hardware driver is `amodevices.HighFinesseWS` (which consumes the
'ReadOnce' and 'PulseMode' device configuration keys); this module adds
the logger-facing channel handling.

Channel types, one per quantity the DLL offers:

- 'Frequency': the measured frequency, logged in the DLL-native THz by
  default; set the channel's `Unit` to 'GHz' to log GHz instead (keep
  the channel's 'unit' tag consistent with this choice). Logged only
  when the wavemeter delivered a result.
- 'AmplitudeMax1', 'AmplitudeMax2', 'AmplitudeAvg1', 'AmplitudeAvg2':
  the interference pattern's maximum and the average height of its
  fringes on CCD array 1 and 2, in counts of the array's ADC (the
  automatic exposure aims at a maximum of about 1000-3000 counts).
  Logged with every result, valid or not — the maximum of an
  overexposed shot is the interesting number. (The minima are not
  offered: a dark array's minimum is legitimately 0, which the DLL's
  return contract cannot tell from "no value".)
- 'Power': the wavemeter's own reading of the light it received — the
  pulse energy (µJ) in a pulsed mode, the power (µW) in CW. Relative,
  never calibrated, and measured behind the coupling fiber; logged
  only when the wavemeter delivered a value.
- 'Temperature', 'Pressure': the optical unit's temperature (°C) and
  air pressure (mbar), read at most every 'MinInterval_s' seconds of
  the channel's 'DeviceSpecificParams' (default 60; slow housekeeping).
  Put them under their own 'sensor' tag ("Optical unit"). A unit
  without a pressure sensor answers with a code (logged once), and
  nothing is written.

Every DLL quantity is read at most once per poll, whatever the number
of channels: the frequency read consumes the result in 'ReadOnce' mode
(a second read would already return ErrNoValue), and the amplitudes
and the power are read only on a poll that saw a new result — first
the frequency, then the amplitudes (state readouts, unaffected by
'ReadOnce'), then the power (subject to 'ReadOnce' like the frequency;
whether the two share the flag is not stated by the manual — if the
power stays 0 on every result, the log shows one "energy status - ->
no_value (code 0)" line and the 'Power' channel should be dropped).

The wavemeter's result STATUS rides along as a companion string field
on the same row (field key 'status'; 'status-field-key' renames it,
`null` drops it — see `readings`) — one row per wavemeter result:
'ok' beside a valid frequency, else the reason
from `HighFinesseWS.status_text` ('overexposed', 'underexposed',
'no_signal', 'no_pulse', ...; 'unknown_error' for an unmapped code,
whose raw value goes to the log) with no frequency field; nothing at
all when there is nothing new (ErrNoValue in 'ReadOnce' mode). Status
policy for the other channels: the amplitude channels can carry the
same result status, the 'Power' channel carries `GetPowerNum`'s own
one, and the environment channels have none (they REQUIRE
`"status-field-key": null`). Write the status on the 'Frequency'
channel only, i.e. set `"status-field-key": null` on every other
channel: a consumer that pins the frequency series by measurement,
field and sensor (the ion-detection GUI does, pivoting `frequency`
with `status`) sees a SECOND `status` series under the same sensor as
soon as another channel with a different tag set (its 'unit') writes
that key, and refuses the ambiguous channel — every frequency cell
blank. The status of a shot is on its frequency row at the identical
timestamp anyway. A channel that must carry a status of its own uses a
RENAMED key ("amplitude_status"); `_check_status_series` refuses the
ambiguous layout at startup.

Readings dict: the 'Frequency' channel (and any channel with a status
key) is present on every poll — NaN and no status when there is
nothing new, which the logger drops — while a channel that switched the
status off is present only on polls where it has something. The logger
counts and thins its per-channel INFO lines per reading it is handed;
eight channels at 50 Hz would otherwise fill the log with "not written"
lines.
"""

import logging
import numbers
import time

import numpy as np

from amodevices import HighFinesseWS
from amodevices.dev_exceptions import DeviceError
from amodevices.highfinesse_ws.highfinesse_ws import (
    amplitude_error_name, amplitude_value, cAvg1, cAvg2, classify_result,
    cMax1, cMax2, environment_error_name, environment_value)
from readings import status_field_key, with_status

logger = logging.getLogger(__name__)


class Device(HighFinesseWS):

    # Channel type -> GetAmplitudeNum index
    AMPLITUDE_TYPES = {'AmplitudeMax1': cMax1, 'AmplitudeMax2': cMax2,
                       'AmplitudeAvg1': cAvg1, 'AmplitudeAvg2': cAvg2}
    # Channel type -> driver method; read at most every 'MinInterval_s'
    ENVIRONMENT_TYPES = {'Temperature': 'get_temperature',
                         'Pressure': 'get_pressure'}
    CHANNEL_TYPES = ('Frequency', *AMPLITUDE_TYPES, 'Power', *ENVIRONMENT_TYPES)
    DEFAULT_MIN_INTERVAL_S = 60.0
    # Writes the result status beside every channel's value (see
    # `readings`)
    STATUS_CAPABLE = True
    # A flickering half-blocked beam changes status at the shot rate;
    # transitions are logged at most this often per quantity (the rest
    # are counted)
    TRANSITION_LOG_MIN_INTERVAL_S = 1.0

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        # Fail fast on a bad channel configuration — a typo must surface at
        # startup, not as silently misscaled data (validated before
        # `HighFinesseWS.__init__` loads the DLL)
        for channel_id, chan in device['Channels'].items():
            where = f'channel \'{channel_id}\' of device \'{device["Device"]}\''
            ctype = chan.get('Type')
            if ctype not in self.CHANNEL_TYPES:
                raise DeviceError(
                    f'Unknown channel type \'{ctype}\' for {where}'
                    f' ({" or ".join(map(repr, self.CHANNEL_TYPES))})')
            if ctype == 'Frequency':
                unit = chan.get('Unit', 'THz')
                if unit not in ('THz', 'GHz'):
                    raise DeviceError(
                        f'Unknown \'Unit\' \'{unit}\' for {where} (\'THz\' or \'GHz\')')
            if ctype in self.ENVIRONMENT_TYPES:
                if status_field_key(chan) is not None:
                    raise DeviceError(
                        f'Channel type \'{ctype}\' of {where} reports no result'
                        ' status: set \'status-field-key\' to null')
                interval = self._min_interval(chan)
                if (not isinstance(interval, numbers.Real)
                        or isinstance(interval, bool) or interval < 0):
                    raise DeviceError(
                        f'\'MinInterval_s\' of {where} must be a non-negative'
                        f' number of seconds, not {interval!r}')
        self._check_status_series(device)
        super(Device, self).__init__(device)
        # Per-poll memo of the DLL reads (`_query`)
        self._memo = {}
        # Environment channel ID -> monotonic time of its last read
        self._env_last_read = {}
        # Transition-log state per quantity label (`_log_transition`)
        self._transitions = {}
        # Raw code of the last frequency result (0 for a valid one)
        self._last_raw_code = 0

    @classmethod
    def _min_interval(cls, chan):
        """The channel's 'MinInterval_s' (its 'DeviceSpecificParams'),
        or the default."""
        return chan.get('DeviceSpecificParams', {}).get(
            'MinInterval_s', cls.DEFAULT_MIN_INTERVAL_S)

    @staticmethod
    def _check_status_series(device):
        """Refuse two channels that write the same status field under
        the same 'sensor' tag but with DIFFERENT tag sets: a consumer
        pinning the status series by sensor would find two of them
        and could use neither (see the module docstring)."""
        seen = {}          # (status key, sensor) -> (tag set, channel ID)
        for channel_id, chan in device['Channels'].items():
            key = status_field_key(chan)
            if key is None:
                continue
            tags = dict(device.get('tags', {}))
            tags.update(chan.get('tags', {}))
            ident = (key, tags.get('sensor'))
            tag_set = frozenset(tags.items())
            if ident in seen and seen[ident][0] != tag_set:
                other = seen[ident][1]
                raise DeviceError(
                    f'Channels \'{other}\' and \'{channel_id}\' of device'
                    f' \'{device["Device"]}\' both write the status field'
                    f' \'{key}\' under sensor {ident[1]!r} with different tags:'
                    ' a consumer pinning the series by sensor cannot tell them'
                    ' apart. Set \'status-field-key\' to null on all but the'
                    ' frequency channel, or rename it')
            seen.setdefault(ident, (tag_set, channel_id))

    def _query(self, method, *args):
        """The driver query `method` (its name) with `args`, sent once per
        poll: a repeat within the same poll returns the memoized result."""
        key = (method, args)
        if key not in self._memo:
            self._memo[key] = getattr(self, method)(*args)
        return self._memo[key]

    def read_result(self):
        """Read the wavemeter ONCE and return `(frequency_thz, status)`.

        `frequency_thz` is the result in THz, or NaN when there is none;
        `status` is 'ok' with a result, the status text of an error code
        (with the raw code kept in `self._last_raw_code`), or None when
        the wavemeter has nothing new (ErrNoValue in 'ReadOnce' mode) or
        is not present.
        """
        raw = HighFinesseWS.get_frequency(self)
        value, status = classify_result(raw)
        if status is not None:
            self._last_raw_code = 0 if status == self.STATUS_OK else int(raw)
        return value, status

    def get_frequency(self):
        """Read current laser frequency (THz; NaN without a result).

        Consumes a result in 'ReadOnce' mode like `get_values` does — do
        not interleave the two.
        """
        return self.read_result()[0]

    def _log_transition(self, label, status, code=0):
        """Log a change of the `label` quantity's status (throttled per
        label); `code` is the raw DLL code behind a non-'ok' status."""
        if not isinstance(status, str):
            return                                   # nothing new
        state = self._transitions.setdefault(
            label, {'status': None, 'log_t': float('-inf'), 'suppressed': 0})
        if status == state['status']:
            return
        previous = state['status']
        state['status'] = status
        now = time.monotonic()
        if now - state['log_t'] < self.TRANSITION_LOG_MIN_INTERVAL_S:
            state['suppressed'] += 1
            return
        code_text = ('' if status == self.STATUS_OK
                     else f' (code {int(code)})')
        suppressed = ('' if not state['suppressed']
                      else f' ({state["suppressed"]} transitions'
                           ' not logged since the last line)')
        logger.info(
            '\'%s\': wavemeter %s status %s -> %s%s%s',
            self.device['Device'], label, previous if previous is not None else '-',
            status, code_text, suppressed)
        state['log_t'] = now
        state['suppressed'] = 0

    def _amplitude(self, ctype):
        """One amplitude of this poll's result (counts, NaN for a DLL
        error code) and the raw return."""
        raw = self._query('get_amplitude', self.AMPLITUDE_TYPES[ctype])
        return amplitude_value(raw), raw

    def _power(self):
        """This poll's power/energy reading as `(value, status)`, the
        status being `GetPowerNum`'s own ('ok', 'no_pulse', ...; None
        when the DLL had no value)."""
        raw = self._query('get_power')
        value, status = classify_result(raw)
        # 0 on a poll with a result is worth a line: it is what a
        # ReadOnce flag shared with the frequency read looks like
        self._log_transition('energy', status if status is not None else 'no_value',
                             0 if raw is None else raw)
        return value, status

    def _environment(self, channel_id, chan):
        """The environment channel's value when its interval is due
        (NaN for a DLL code), None when it is not."""
        now = time.monotonic()
        last = self._env_last_read.get(channel_id)
        if last is not None and now - last < self._min_interval(chan):
            return None
        self._env_last_read[channel_id] = now
        ctype = chan['Type']
        raw = self._query(self.ENVIRONMENT_TYPES[ctype])
        value = environment_value(raw)
        if np.isfinite(value):
            status = self.STATUS_OK
        else:
            status = 'not_present' if raw is None else environment_error_name(raw)
        self._log_transition(ctype.lower(), status, 0 if raw is None else raw)
        return value

    def get_values(self):
        """Read channels: one DLL pass per poll (see the module
        docstring) — the frequency with the status text beside it, the
        amplitudes and the power on a poll with a new result, the
        environment when due; a plain value for a channel that switched
        the status off, present only when it has something (NaN/None
        entries are dropped by the logger)."""
        self.check_pulse_mode()
        self._memo.clear()
        freq, status = self.read_result()
        self._log_transition('result', status, self._last_raw_code)
        new = status is not None
        readings = {}
        amplitude_codes = []
        for channel_id, chan in self.device['Channels'].items():
            ctype = chan['Type']
            if ctype == 'Frequency':
                # The DLL reports THz; 'Unit': 'GHz' logs GHz instead
                value = freq*1e3 if chan.get('Unit', 'THz') == 'GHz' else freq
                readings[channel_id] = with_status(chan, value, status)
            elif ctype in self.AMPLITUDE_TYPES:
                if new:
                    value, raw = self._amplitude(ctype)
                    amplitude_codes.append(raw)
                    readings[channel_id] = with_status(chan, value, status)
                elif status_field_key(chan) is not None:
                    readings[channel_id] = with_status(chan, np.nan, None)
            elif ctype == 'Power':
                if new:
                    value, pstatus = self._power()
                    readings[channel_id] = with_status(chan, value, pstatus)
                elif status_field_key(chan) is not None:
                    readings[channel_id] = with_status(chan, np.nan, None)
            else:
                value = self._environment(channel_id, chan)
                if value is not None:
                    readings[channel_id] = value
        if amplitude_codes:
            # One label for the four amplitudes: 'ok' when all came
            # back, else the first failure (0 = no value)
            bad = [raw for raw in amplitude_codes if raw is None or raw <= 0]
            if not bad:
                status, code = self.STATUS_OK, 0
            elif bad[0] is None or bad[0] == 0:
                status, code = 'no_value', 0
            else:
                status, code = amplitude_error_name(bad[0]), bad[0]
            self._log_transition('amplitude', status, code)
        return readings
