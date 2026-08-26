# Copyright (c) 2018, Fabian Schmid, Edward Wang
# Copyright (c) 2023-2026, Lothar Maisenbacher
#
# All rights reserved.
#
# Copyright (c) 2015, Red Pitaya
"""
Module for reading out the Red Pitaya lockbox 'rp-lockbox'.
The hardware driver is `amodevices.RPLockbox`; this module adds the
logger-facing channel handling.
"""

from amodevices import RPLockbox
from amodevices.dev_exceptions import DeviceError

class Device(RPLockbox):

    def get_device_channel(self, channel_id, chan):
        """Get device channel from channel definition `chan` for channel with ID `channel_id`"""
        device_channel = chan.get('DeviceChannel')
        if device_channel is None:
            raise DeviceError(
                'Could not get required propertry \'DeviceChannel\' for channel \'%s\'', channel_id)
        return device_channel

    def get_pid_channels(self, channel_id, chan):
        """
        Get PID channels (input 1 or 2, output 1 or 2) from string `pid` (e.g., '12' for input 1
        and output 2), which is stored in channel definition `chan['PID']` for channel with ID
        `channel_id`.
        """
        pid = chan.get('PID')
        if pid is None:
            raise DeviceError(
                f'Could not get required propertry \'PID\' for channel \'{channel_id}\'')
        try:
            pid_channels = [int(pid[0]), int(pid[1])]
        except ValueError:
            raise DeviceError(
                f'Invalid PID controller \'{pid}\' defined for channel \'{channel_id}\''
                +' (in field \'PID\'; valid values: \'11\', \'12\', \'21\', \'22\')')
        return pid_channels

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'FastAnalogIn':
                device_channel = self.get_device_channel(channel_id, chan)
                value = self.get_fast_analog_input(device_channel)
                readings[channel_id] = value
            elif chan['Type'] == 'FastAnalogOut':
                device_channel = self.get_device_channel(channel_id, chan)
                value = self.get_fast_analog_output(device_channel)
                readings[channel_id] = value
            elif chan['Type'] == 'GlobalGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kg(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'PGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kp(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'IGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_ki(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'IIGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kii(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'DGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kd(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
