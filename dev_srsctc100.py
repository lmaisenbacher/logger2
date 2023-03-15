# -*- coding: utf-8 -*-
"""
This module contains drivers for the Stanford Research Instruments CTC100
cryogenic temperature controller using its USB interface,
which implements a virtual serial port.
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
        query = f"{command}\n".encode(encoding="ASCII")
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise LoggerError("Failed to write to device")
        rsp = self.connection.readline()
        try:
            rsp = rsp.decode(encoding="ASCII")
        except UnicodeDecodeError:
            raise LoggerError(f"Error in decoding response ('{rsp}') received")
        if rsp == '':
            raise LoggerError(
                "No response received")
        if not rsp.endswith("\r\n"):
            raise LoggerError("Response does not end with '\r\n' as expected")
        return rsp.rstrip()

    def read_temperature(self, name):
        """Read temperature of channel with name `name` (str)."""
        rsp = self.query(f"{name}?")
        return float(rsp)

    def read_pid_setpoint(self, name):
        """Read PID temperature setpoint of channel with name `name` (str)."""
        rsp = self.query(f"{name}.PID.Setpoint?")
        return float(rsp)

    def read_heater_power(self, name):
        """Read heater power of channel with name `name` (str)."""
        rsp = self.query(f"{name}?")
        return float(rsp)

    def query_custom_command(self, command):
        """Send custom command `command` (str) and read response."""
        rsp = self.query(f"{command}")
        return float(rsp)

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
                raise LoggerError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
