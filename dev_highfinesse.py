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
        self.wavemeter = highfinesse.Wavemeter()

    def get_frequency(self):
        """Read current laser frequency."""
        freq = self.wavemeter.get_frequency()
        # If error is encoutered, set frequency to NaN
        freq = np.nan if freq <= 0 or freq is None else freq
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
