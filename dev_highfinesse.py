# -*- coding: utf-8 -*-
"""
This module contains drivers for the HighFinesse wavemeters
(tested with model WS/7),
which are interfaced through a Windows DLL API.
The wavemeter software must be running on the same PC.
The hardware driver is `amodevices.HighFinesseWS` (which consumes the
'ReadOnce' and 'PulseMode' device configuration keys); this module adds
the logger-facing channel handling.

Channel type 'Frequency': the measured frequency, logged in the
DLL-native THz by default; set the channel's `Unit` to 'GHz' to log GHz
instead (keep the channel's 'unit' tag consistent with this choice).
Logged only when the wavemeter delivered a result.

The wavemeter's result STATUS rides along as a companion string field
on the same row (field key 'status'; 'status-field-key' renames it,
`null` drops it — see `readings`) — one row per wavemeter result:
'ok' beside a valid frequency, else the reason
from `HighFinesseWS.status_text` ('overexposed', 'underexposed',
'no_signal', 'no_pulse', ...; 'unknown_error' for an unmapped code,
whose raw value goes to the log) with no frequency field; nothing at
all when there is nothing new (ErrNoValue in 'ReadOnce' mode). One DLL
read per poll feeds every channel: in 'ReadOnce' mode a second read
would already return ErrNoValue.
"""

import logging
import time

import numpy as np

from amodevices import HighFinesseWS
from amodevices.dev_exceptions import DeviceError
from readings import with_status

logger = logging.getLogger(__name__)


class Device(HighFinesseWS):

    CHANNEL_TYPES = ('Frequency',)
    # Writes the result status beside every channel's value (see
    # `readings`)
    STATUS_CAPABLE = True
    # A flickering half-blocked beam changes status at the shot rate;
    # transitions are logged at most this often (the rest are counted)
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
            ctype = chan.get('Type')
            if ctype not in self.CHANNEL_TYPES:
                raise DeviceError(
                    f'Unknown channel type \'{ctype}\' for channel \'{channel_id}\''
                    +f' of device \'{device["Device"]}\' ({" or ".join(map(repr, self.CHANNEL_TYPES))})')
            unit = chan.get('Unit', 'THz')
            if unit not in ('THz', 'GHz'):
                raise DeviceError(
                    f'Unknown \'Unit\' \'{unit}\' for channel \'{channel_id}\''
                    +f' of device \'{device["Device"]}\' (\'THz\' or \'GHz\')')
        super(Device, self).__init__(device)
        # Status of the last REAL result (None before the first one) and
        # the transition-log throttle
        self._last_status = None
        self._last_raw_code = 0
        self._last_transition_log_t = float('-inf')
        self._suppressed_transitions = 0

    def read_result(self):
        """Read the wavemeter ONCE and return `(frequency_thz, status)`.

        `frequency_thz` is the result in THz, or NaN when there is none;
        `status` is 'ok' with a result, the status text of an error code
        (with the raw code kept in `self._last_raw_code`), or None when
        the wavemeter has nothing new (ErrNoValue in 'ReadOnce' mode) or
        is not present.
        """
        raw = HighFinesseWS.get_frequency(self)
        if raw is None or raw == 0:
            return np.nan, None
        if raw > 0:
            self._last_raw_code = 0
            return raw, self.STATUS_OK
        self._last_raw_code = int(raw)
        return np.nan, self.status_text(raw)

    def get_frequency(self):
        """Read current laser frequency (THz; NaN without a result).

        Consumes a result in 'ReadOnce' mode like `get_values` does — do
        not interleave the two.
        """
        return self.read_result()[0]

    def _log_transition(self, status):
        """Log a change of the result status (throttled)."""
        if not isinstance(status, str):
            return                                   # nothing new
        if status == self._last_status:
            return
        previous = self._last_status
        self._last_status = status
        now = time.monotonic()
        if now - self._last_transition_log_t < self.TRANSITION_LOG_MIN_INTERVAL_S:
            self._suppressed_transitions += 1
            return
        code = ('' if status == self.STATUS_OK
                else f' (code {self._last_raw_code})')
        suppressed = ('' if not self._suppressed_transitions
                      else f' ({self._suppressed_transitions} transitions'
                           ' not logged since the last line)')
        logger.info(
            '\'%s\': wavemeter result status %s -> %s%s%s',
            self.device['Device'], previous if previous is not None else '-',
            status, code, suppressed)
        self._last_transition_log_t = now
        self._suppressed_transitions = 0

    def get_values(self):
        """Read channels: the frequency per channel with the status text
        beside it (a plain value for a channel that switched the status
        off; NaN/None entries are dropped by the logger)."""
        self.check_pulse_mode()
        freq, status = self.read_result()
        self._log_transition(status)
        readings = {}
        for channel_id, chan in self.device['Channels'].items():
            # The DLL reports THz; 'Unit': 'GHz' logs GHz instead
            value = freq*1e3 if chan.get('Unit', 'THz') == 'GHz' else freq
            readings[channel_id] = with_status(chan, value, status)
        return readings
