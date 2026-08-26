# -*- coding: utf-8 -*-
"""
This module contains drivers for the Stanford Research Instruments CTC100
cryogenic temperature controller using its USB interface,
which implements a virtual serial port.
The hardware driver is `amodevices.SRSCTC100`; this module adds the
logger-facing channel handling.
"""

from amodevices import SRSCTC100
from amodevices.dev_exceptions import DeviceError

class Device(SRSCTC100):

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'Temperature':
                value = self.read_temperature(chan["tags"]["CTC100ChannelName"])
                readings[channel_id] = value
            elif chan['Type'] == 'PIDSetpoint':
                value = self.read_pid_setpoint(chan["tags"]["CTC100ChannelName"])
                readings[channel_id] = value
            elif chan['Type'] == 'HeaterPower':
                value = self.read_heater_power(chan["tags"]["CTC100ChannelName"])
                readings[channel_id] = value
            elif chan['Type'] == 'Custom':
                value = self.query_custom_command(chan["tags"]["CTC100CustomCommand"])
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
