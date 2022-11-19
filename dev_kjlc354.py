# -*- coding: utf-8 -*-
"""
This module contains drivers for the KJLC 354 Series Ion Gauge.
It implements the RS-485 protocol of the ion gauge over a serial port
to read out the pressure in Torr.
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


    def read_pressure(self):
        query = f'#{self.device["DeviceSpecificParams"]["InternalAddress"]}RD\r'.encode("ASCII")
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise LoggerError(f"Failed to query {self.device['Device']}")
        rsp = self.connection.readline()
        if rsp.decode()[0] == '?':
            raise LoggerError(f"Received an error response from {self.device['Device']}")
        elif rsp.decode()[0] == '*':
            # Convert from base ten scientific notation to floating point
            return float(rsp[4:7]) * 10 ** float(rsp[9:11])
        else:
            raise LoggerError(f"Didn't receive acknowledgement from {self.device['Device']}")

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            value = self.read_pressure()
            readings[channel_id] = value
        return readings
