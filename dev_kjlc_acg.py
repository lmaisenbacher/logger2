# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC ACG series capacitance manometer.
It uses the two-wire RS-232 interface of the gauge. Eight-byte long messages are sent
every 10 ms to the computer, containing the status, errors, and pressure readings.

Note the pin assignment of the RS-232 interface on the ion gauges:
Rx on pin 14, Tx on pin 13, and ground on pin 12.
This might be different than the pin assignment of your RS-232 adapter
(such as e.g. those from StarTech) and a custom cable might needs to be used.
"""
import serial
import logging

from amodevices import dev_generic
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger()

class Device(dev_generic.Device):

    MANTISSA_LIST = [1.0, 1.1, 2.0, 2.5, 5.0]

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
            raise DeviceError(
                f"Serial connection on port {device['Address']} couldn't be opened")

    def align_msg(self, byte_string, length=8):
        """
        Find and return a message within a given byte string that starts with the byte 0x07,
        has a specified length, and ends with a checksum that matches the sum of the message bytes.

        Parameters:
            byte_string (bytes): The byte string to search for the message.
            length (int, optional): The expected length of the message. Default is 8.

        Returns:
            bytes or None: If a valid message is found, it returns the message as a bytes object.
                        If no valid message is found, it returns None.
        """
        for i in range(len(byte_string) - length):
            checksum = sum(byte_string[i+1: i + length]) & 0xFF
            if byte_string[i] == 0x07 and checksum == int(byte_string[i + length]):
                return byte_string[i: i + length]
        return None

    def get_message(self):
        """
        Reads a message from the device's connection buffer, aligns it, and returns it.

        This method reads data from the device's connection buffer and attempts to
        extract a message by aligning it from the end of the received data. The final 17
        bytes in the buffer are checked for a message.

        Returns:
            bytes: An 8-byte message from the device.
        """
        buf = self.connection.read_all()
        message = self.align_msg(buf[-17:])
        if message is None:
            raise DeviceError("No message could be found in the buffer")
        return message

    def read_pressure(self):
        """
        Reads and calculates pressure data from a received message.

        This method retrieves a message from the device using the `get_message` method
        and extracts pressure-related data from it. See the ACG Pressure gauge manual
        to learn more about this conversion.

        Returns:
            float: The calculated pressure value in Torr.
        """
        message = self.get_message()
        self.check_errors(message)
        pressure_bytes = message[4:6]
        # FSR mantissa is stored in the last four bits of byte seven
        fsr_mantissa = self.MANTISSA_LIST[int(message[7]) & 0x0F]
        fsr_exp = int(message[7] & 0xF0) - 3
        return int.from_bytes(pressure_bytes, byteorder='big') / 3.2e4 * fsr_mantissa * 10 ** fsr_exp

    def check_errors(self, message):
        """
        Check for errors in the received message.

        Parameters:
            message (bytes): The received message.

        Returns:
            list: A list representing the status of specific setpoints; 1 for triggered, zero otherwise.
            Indices correspond to which setpoint has been triggered.
            """
        error_byte = message[3]
        if error_byte & 0b11100111 != 0:
            raise DeviceError(f"Error Message Received: {error_byte}")
        return [error_byte & 0b00010000 >> 4, error_byte & 0b00001000 >> 3]

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Pressure']:
                value = self.read_pressure()
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        return readings
