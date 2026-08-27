# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC 354 and KJLC 352 series ion pressure
gauge, the InstruTech IGM401 and IGM402 ion pressure gauge, and the Kurt J. Lesker KJLC 300
series Pirani pressure gauge.
The hardware driver is `amodevices.KJLC354` (which documents the
`DeviceSpecificParams` options `InternalAddress`, `ReadCombinedPressure`, and
`ConfirmFilamentIsOn`); this module adds the logger-facing channel handling.
"""

from amodevices import KJLC354
from amodevices.dev_exceptions import DeviceError

class Device(KJLC354):

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
                    +f' of device \'{self.device["Device"]}\'')
        return readings
