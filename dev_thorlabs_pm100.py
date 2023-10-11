# -*- coding: utf-8 -*-
"""
Thorlabs PM100 power meter.
"""

import logging

from amodevices import ThorlabsPM100
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger()

class Device(ThorlabsPM100):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)

        # Set power unit to watt (W)
        self.power.unit = 'W'

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'PowerUnit':
                readings[channel_id] = self.power.unit
            elif chan['Type'] == 'PowerAutoRange':
                readings[channel_id] = self.power.auto_range
            elif chan['Type'] == 'Power':
                readings[channel_id] = self.power.value
            elif chan['Type'] == 'Wavelength':
                readings[channel_id] = self.wavelength
            elif chan['Type'] == 'BeamDiameter':
                readings[channel_id] = self.beam_diameter
            elif chan['Type'] == 'NumAverages':
                readings[channel_id] = self.num_averages
            elif chan['Type'] == 'ZeroMagnitude':
                readings[channel_id] = self.zero_magnitude
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
