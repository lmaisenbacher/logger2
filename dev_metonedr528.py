# -*- coding: utf-8 -*-
"""
This module contains drivers for the Met One DR-528 handheld particle counter.
It implements a serial (RS-232) communication to read out particle counts for eight sizes,
air temperature, and relative humidity.
"""

import serial
import logging

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
            self.connection = serial.Serial(
                device["Address"], timeout=device["Timeout"],
                **device.get('SerialConnectionParams', {}))
        except serial.SerialException:
            raise LoggerError(
                f"Serial connection with {device['Device']} couldn't be opened")

    def to_readings(self, message):
        """Converts raw message string from device into floating point values by channel order."""
        keys = [str(key, 'utf-8') for key in message[-2].split(b',')[1:11]]
        vals = [float(val) for val in message[-1].split(b',')[1:11]]
        return dict(filter(lambda k: k[0] in self.device["Channels"].keys(), zip(keys, vals)))

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        n_write_bytes = self.connection.write(f'4\r'.encode("ASCII"))
        if n_write_bytes != 2:
            raise LoggerError(f"Failed to query {self.device['Device']}")
        message = self.connection.readlines()
        if len(message) < 1:
            raise LoggerError(f"Didn't receive acknowledgement from {self.device['Device']}")
        else:
            readings = self.to_readings(message)
        return readings
