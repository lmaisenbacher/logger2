# -*- coding: utf-8 -*-
"""
Stanford Research Instruments (SRS) SIM922 diode temperature monitor.
"""

import logging

from amodevices import SRSSIM922
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger()

class Device(SRSSIM922):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)

    def get_values(self):
        """Read channels"""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'Temperature' \
                    and (device_channel := chan['DeviceChannel']) in range(1, 5):
                value = self.read_temperature(device_channel)
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        return readings
