
# -*- coding: utf-8 -*-
"""
Write a docstring here
"""

import logging
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn


import dev_generic

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
        try:
            self.ads = {}
            self.channels = self.device["Channels"]
            for channel_id, channel in self.channels.items():
                if channel["I2CAddress"] not in self.ads.keys():
                    self.ads[channel["I2CAddress"]] = ADS.ADS1115(busio.I2C(board.SCL, board.SDA), address=int(channel["I2CAddress"], 16), data_rate=860)
                channel["ChanObj"] = AnalogIn(self.ads[channel["I2CAddress"]], channel["Pins"]["Signal"], channel["Pins"]["Reference"])
        except OSError:
            raise LoggerError(
                f"I2C port for {device['Address']} couldn't be opened")


    def read_pressure(self, chan_obj):
        """Read pressure."""
        return chan_obj.voltage

    def get_values(self):
        """Read channels."""
        readings = {}
        for channel_id, channel in self.channels.items():
            if channel['Type'] in ['Pressure']:
                try:
                    value = self.read_pressure(channel['ChanObj'])
                    readings[channel_id] = value
                except OSError:
                    raise LoggerError(
                        f"I2C port ({channel['I2CAddress']}) for {channel_id} couldn't be opened")
            else:
                raise LoggerError(
                    f'Unknown channel type \'{channel["Type"]}\' for channel \'{name}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
