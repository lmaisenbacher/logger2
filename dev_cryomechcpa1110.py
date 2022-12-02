# -*- coding: utf-8 -*-
"""
This module contains drivers for the Cryomech CPA1110 helium compressor,
using the Modbus TCP protocol over ethernet interface.
"""

import logging
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

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
        self.device_id = device["ModbusDeviceID"]

    def connect(self):
        """Connect to device."""
        device = self.device
        try:
            self.client = ModbusTcpClient(
                device["Address"], timeout=device["Timeout"])
            self.client.connect()
        except ModbusException as e:
            raise LoggerError(
                f"Modbus connection on port {device['Address']} couldn't be opened: '{e}'")
        if not self.client.connected:
            raise LoggerError(
                f"Modbus connection on port {device['Address']} couldn't be opened")

    def read_float_value(self, register):
        """
        Read float value from register `register` (int).
        The value read from the register is an integer corresponding to ten times the measurement
        value, which is accounted for here by diving by 10 and thus converting to float.
        """
        try:
            values = self.client.read_input_registers(register, 1, slave=self.device_id)
        except ModbusException as e:
            raise LoggerError(f"Encountered Modbus exception when trying to read register: '{e}'")
        if values.isError():
            raise LoggerError("Encountered Modbus exception when trying to read register")
        return float(values.registers[0])/10

    def read_coolant_in_temperature(self):
        """Read coolant inlet temperature."""
        return self.read_float_value(40)

    def read_coolant_out_temperature(self):
        """Read coolant return temperature."""
        return self.read_float_value(41)

    def read_oil_temperature(self):
        """Read oil temperature."""
        return self.read_float_value(42)

    def read_he_temperature(self):
        """Read helium temperature."""
        return self.read_float_value(43)

    def read_low_pressure(self):
        """Read low He pressure."""
        return self.read_float_value(44)

    def read_high_pressure(self):
        """Read high He pressure."""
        return self.read_float_value(46)

    def set_compressor_state(self, state):
        """Switch compressor on (`state` is True) or off (`state` is False)."""
        value = 1 if state else 255
        write = client.write_register(value, 1, slave=self.device_id)

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
