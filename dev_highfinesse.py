# -*- coding: utf-8 -*-
"""
This module contains drivers for the HighFinesse wavemeters
(tested with model WS/7),
which are interfaced through a Windows DLL API.
The wavemeter software must be running on the same PC.
The hardware driver is `amodevices.HighFinesseWS` (which consumes the
'ReadOnce' and 'PulseMode' device configuration keys); this module adds
the logger-facing channel handling.
A 'Frequency' channel logs in the DLL-native THz by default; set the
channel's `Unit` to 'GHz' to log GHz instead (keep the channel's
'unit' tag consistent with this choice).
"""

import numpy as np

from amodevices import HighFinesseWS
from amodevices.dev_exceptions import DeviceError

class Device(HighFinesseWS):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        # Fail fast on a bad per-channel 'Unit' — a typo must surface at
        # startup, not as silently misscaled data (validated before
        # `HighFinesseWS.__init__` loads the DLL)
        for channel_id, chan in device['Channels'].items():
            unit = chan.get('Unit', 'THz')
            if unit not in ('THz', 'GHz'):
                raise DeviceError(
                    f'Unknown \'Unit\' \'{unit}\' for channel \'{channel_id}\''
                    +f' of device \'{device["Device"]}\' (\'THz\' or \'GHz\')')
        super(Device, self).__init__(device)

    def get_frequency(self):
        """Read current laser frequency."""
        freq = super(Device, self).get_frequency()
        # If error is encountered (or the value was already read in
        # read-once mode), set frequency to NaN
        freq = np.nan if freq is None or freq <= 0 else freq
        return freq

    def get_values(self):
        """Read channels."""
        self.check_pulse_mode()
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'Frequency':
                value = self.get_frequency()
                # The DLL reports THz; 'Unit': 'GHz' logs GHz instead
                # (NaN stays NaN and is dropped by the logger)
                if chan.get('Unit', 'THz') == 'GHz':
                    value = value*1e3
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
