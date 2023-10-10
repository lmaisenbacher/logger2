# -*- coding: utf-8 -*-
"""
Thorlabs KPA101 beam position aligner.
"""

import logging

from amodevices import ThorlabsKPA101
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger()

class Device(ThorlabsKPA101):

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
            if chan['Type'] == 'XDiff':
                readings[channel_id] = self.xdiff
            elif chan['Type'] == 'YDiff':
                readings[channel_id] = self.ydiff
            elif chan['Type'] == 'Sum':
                readings[channel_id] = self.sum
            elif chan['Type'] == 'XPos':
                readings[channel_id] = self.xpos
            elif chan['Type'] == 'YPos':
                readings[channel_id] = self.ypos
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
