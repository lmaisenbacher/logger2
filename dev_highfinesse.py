# -*- coding: utf-8 -*-
"""
This module contains drivers for the HighFinesse wavemeters
(tested with models WS Ultimate 2 MC and WS/7),
which are interfaced through a Windows DLL API.
The wavemeter software must be running on the same PC.
"""

import logging

import dev_generic
import highfinesse

from defs import LoggerError

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
        return self.wavemeter.get_frequency()

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'Frequency':
                value = self.get_frequency()
                readings[channel_id] = value
            else:
                raise LoggerError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
