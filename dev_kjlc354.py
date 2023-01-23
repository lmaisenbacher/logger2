# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC 354 series ion pressure gauge
and the InstruTech IGM401 ion pressure gauge
(InstruTech seems to be the original manufacturer).
It uses the two-wire RS-485 interface of the gauge
(not to be confused with a RS-232 interface, which uses the same 9-pin sub-D connector)
to read out the pressure in units of Torr.

Note the pin assignment of the RS-485 interface on the gauge:
DATA- on pin 6, DATA+ on pin 9, and ground on pin 4.
This might be different than the pin assignment of your RS-485 adapter
(such as e.g. those from StarTech) and a custom cable might needs to be used.
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
                f"Serial connection on port {device['Address']} couldn't be opened")

    def query(self, command):
        """Query device with command `command` (str) and return response."""
        internal_address = self.device["DeviceSpecificParams"]["InternalAddress"]
        query = f'#{internal_address}{command}\r'.encode("ASCII")
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise LoggerError("Failed to write to device")
        rsp = self.connection.readline()
        try:
            rsp = rsp.decode()
        except UnicodeDecodeError:
            raise LoggerError(f"Error in decoding response ('{rsp}') received")
        if rsp == '':
            raise LoggerError(
                "No response received")
        if rsp.startswith("?"):
            raise LoggerError(
                f"Received an error response: '{rsp}'")
        if not rsp.startswith(f"*{internal_address} "):
            raise LoggerError(
                f"Didn't receive correct acknowledgement (response received: '{rsp}')")
        return rsp[4:]

    def read_pressure(self):
        """Read pressure."""
        # Check whether filament is powered up and gauge is reading
        rsp = self.query("IGS")
        if rsp.startswith("0"):
            raise LoggerError(
                "Filament is not powered up, no pressure reading available")
        # Read pressure
        rsp = self.query("RDS")
        return float(rsp)

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
