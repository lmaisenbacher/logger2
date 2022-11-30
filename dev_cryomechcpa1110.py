# -*- coding: utf-8 -*-
"""
This module contains drivers for the Cryomech CPA1110 helium compressor.
"""

import logging
from pymodbus.client import ModbusTcpClient

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
        self.client = ModbusTcpClient(
            device["Address"], timeout=device["Timeout"])
        self.device_id = device["ModbusDeviceID"]

    def read_coolant_in_temperature(self):
        """Read coolant inlet temperature."""
        values = self.client.read_input_registers(40, 1, slave=self.device_id)
        print(values.registers)
        return float(values.registers[0])/10

    def read_coolant_out_temperature(self):
        """Read coolant return temperature."""
        values = self.client.read_input_registers(41, 1, slave=self.device_id)
        print(values.registers)
        return float(values.registers[0])/10

    def read_oil_temperature(self):
        """Read oil temperature."""
        values = self.client.read_input_registers(42, 1, slave=self.device_id)
        return float(values.registers[0])/10

    def read_he_temperature(self):
        """Read helium temperature."""
        values = self.client.read_input_registers(43, 1, slave=self.device_id)
        return float(values.registers[0])/10

    def read_low_pressure(self):
        """Read low He pressure."""
        values = self.client.read_input_registers(44, 1, slave=self.device_id)
        return float(values.registers[0])/10

    def read_high_pressure(self):
        """Read high He pressure."""
        values = self.client.read_input_registers(46, 1, slave=self.device_id)
        return float(values.registers[0])/10

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'CoolantIntTemperature':
                value = self.read_coolant_in_temperature()
                readings[channel_id] = value
            elif chan['Type'] == 'CoolantOutTemperature':
                value = self.read_coolant_out_temperature()
                readings[channel_id] = value
            elif chan['Type'] == 'OilTemperature':
                value = self.read_oil_temperature()
                readings[channel_id] = value
            elif chan['Type'] == 'HeTemperature':
                value = self.read_he_temperature()
                readings[channel_id] = value
            elif chan['Type'] == 'LowPressure':
                value = self.read_low_pressure()
                readings[channel_id] = value
            elif chan['Type'] == 'HighPressure':
                value = self.read_high_pressure()
                readings[channel_id] = value
            else:
                raise LoggerError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
