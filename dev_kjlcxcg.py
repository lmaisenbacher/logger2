# -*- coding: utf-8 -*-
"""
Write a docstring here
"""

import logging
import os
import adafruit_ads1x15.ads1015 as ADS
from adafruit_ads1x15.analog_in import AnalogIn
from adafruit_extended_bus import ExtendedI2C as I2C


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
            self.channels =self.device["Channels"]    
            for name, info in self.channels:
                if info["I2CAddress"] not in self.ads:
                    self.ads[info["I2CAddress"]] = ADS.ADS1115(I2C(), address=int(info["I2CAddress"], 16), data_rate=860)
                info["ChanObj"] = ({name: AnalogIn(self.ads["I2CAddress"], info["Pins"]["Signal"], info["Pins"]["Reference"])})         
        except os.OSError:
            raise LoggerError(
                f"I2C port for {device['Address']} couldn't be opened")


    def read_pressure(self, chan_obj, scaling):
        """Read pressure."""
        return chan_obj.voltage * scaling

    def get_values(self):
        """Read channels."""
        readings = {}
        for name, info in self.channels:
            if info['Type'] in ['Pressure']:
                try:
                    value = self.read_pressure(info['ChanObj'], info['Scaling'])
                    readings[name] = value
                except os.OSError:
                    raise LoggerError(
                        f"I2C port ({info['I2CAddress']}) for {name} couldn't be opened")
            else:
                raise LoggerError(
                    f'Unknown channel type \'{info["Type"]}\' for channel \'{name}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
