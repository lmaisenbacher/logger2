# -*- coding: utf-8 -*-
"""
Thorlabs MDT693B 3-axis piezo controller.
"""

import logging

from amodevices import ThorlabsMDT693B
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger()

class Device(ThorlabsMDT693B):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Vout']:
                value = self.read_voltage(chan['Axis'])
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
