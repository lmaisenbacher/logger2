# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC Carbon XCG series pressure gauge, read
out through a custom Arduino controller (see the `amodevices.KJLCXCG` hardware driver, which this
module wraps with the logger-facing channel handling).

NOTE: this driver is deliberately NOT wired into the `Model` mapping of
`logger.py` — the XCG Arduino controller box is currently out of
service. To use it, add a `Model` entry in `init_device`.
"""

from amodevices import KJLCXCG
from amodevices.dev_exceptions import DeviceError

class Device(KJLCXCG):

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Pressure']:
                value = self.read_pressure()
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        return readings
