# -*- coding: utf-8 -*-
"""
This module contains drivers for the SMC HRS012-AN-10-T chiller.
It implements the simple communication protocol of the chiller using its RS-232 interface
to read out the temperature setpoint ('SV1') and the returning temperature ('PV1')
of the cooling liquid.
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

    def generate_query(self, command):
        """Generate serial query to request value for command `command` (str)."""
        return (
            f'\x02{self.device["DeviceSpecificParams"]["InternalAddress"]}R{command}\x03'
            .encode("ASCII"))

    def read_temperature(self, meas_type):
        """Read temperature of type `meas_type` (str), either 'SV1' or 'PV1'."""
        query = self.generate_query(meas_type)
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise LoggerError(f"Failed to query {self.device['Device']}")
        rsp = self.connection.readline()
        if rsp.decode()[3] != '\x06':
            raise LoggerError(f"Didn't receive acknowledgement from {self.device['Device']}")
        # Convert to degree Celsius
        return float(rsp[7:12]) / 10

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['PV1', 'SV1']:
                value = self.read_temperature(chan['Type'])
                readings[channel_id] = value
            else:
                raise LoggerError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
