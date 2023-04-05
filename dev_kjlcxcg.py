# -*- coding: utf-8 -*-
"""
Write a docstring here
"""

import serial
import logging
import board
import busio
import os
import adafruit_ads1x15.ads1015 as ADS
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
            self.i2c = busio.I2C(board.SCL, board.SDA)
            self.ads = ADS.ADS1015(self.i2c)
            self.chan = AnalogIn(self.ads, ADS.P0)
        except os.OSError:
            raise LoggerError(
                f"I2C port for {device['Address']} couldn't be opened")


    def read_pressure(self, scaling=250):
        """Read pressure."""
        return self.chan.voltage * scaling

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Pressure']:
                value = self.read_pressure()
                readings[channel_id] = value
            else:
                raise LoggerError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
