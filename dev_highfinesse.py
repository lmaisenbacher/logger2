# -*- coding: utf-8 -*-
"""
This module contains drivers for the HighFinesse wavemeters
(tested with models WS Ultimate 2 MC and WS/7),
which are interfaced through a Windows DLL API.
The wavemeter software must be running on the same PC.
"""

import numpy as np
import logging

from amodevices import dev_generic
from amodevices.dev_exceptions import DeviceError

import highfinesse

logger = logging.getLogger()

class Device(dev_generic.Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)
        # 'ReadOnce' (default False): report each wavemeter measurement only
        # once — repeat polls before a new measurement completes yield NaN
        # (dropped by the logger), so a pulsed laser gets one point per shot
        self.wavemeter = highfinesse.Wavemeter(read_once=device.get('ReadOnce', False))
        # 'PulseMode' (optional): expected measurement mode of the wavemeter
        # software (0 = CW, nonzero = a pulsed mode, numbering per manual
        # section 4.1.2.4). Checked, not set: the logger must not override a
        # mode an operator chose in the wavemeter GUI, only refuse to log in
        # the wrong one. The raised error benches the device, so logging
        # starts by itself once the mode is corrected.
        expected_mode = device.get('PulseMode')
        actual_mode = self.wavemeter.get_pulse_mode()
        logger.info(
            'Wavemeter software measurement mode: %s (0 = CW, nonzero = pulsed)', actual_mode)
        if expected_mode is not None and actual_mode != expected_mode:
            raise DeviceError(
                f'Wavemeter software is in measurement mode {actual_mode}, but mode '
                f'{expected_mode} is expected (\'PulseMode\' in device configuration). '
                f'Select the correct mode in the \'Pulse\' group of the wavemeter software.')

    def get_frequency(self):
        """Read current laser frequency."""
        freq = self.wavemeter.get_frequency()
        # If error is encountered (or the value was already read in
        # read-once mode), set frequency to NaN
        freq = np.nan if freq is None or freq <= 0 else freq
        return freq

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'Frequency':
                value = self.get_frequency()
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
