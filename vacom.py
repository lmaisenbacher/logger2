# -*- coding: utf-8 -*-

"""This module contains drivers for the following equipment from Vacom

COLDION CU-100 cold cathode ionization gauge controller

"""

import time
import serial

class CU100(object):

    def __init__(self, port='/dev/ttyUSB0', baudrate=19200):
        """Initialize internal variables and serial connection

        :param port: The COM port to open. See the documentation for
            `pyserial <http://pyserial.sourceforge.net/>`_ for an explanation
            of the possible value. The default value is '/dev/ttyUSB0'.
        :type port: str or int
        :param baudrate: 9600, 19200, 38400, 57600, 115200 where 19200 is the default
        :type baudrate: int
        """
        # The serial connection should be setup with the following parameters:
        # 1 start bit, 8 data bits, No parity bit, 1 stop bit, no hardware
        # handshake. These are all default for Serial and therefore not input
        # below
        self.serial = serial.Serial(port=port, baudrate=baudrate, timeout=1)

    def pressure_gauge(self):
        """Return the pressure measured by the gauge

        :return: (pressure value in mbar, (0, "NONE"))
        """
        command = [0xA5, 0x50, 0x00, 0x00, 0x20, 0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        self.serial.write(self.command_with_checksum(command))
        time.sleep(0.1)
        response = self.serial.read(self.serial.inWaiting())
        value = float(response[6:22].decode("ASCII").rstrip("\0"))

        return value, (0, "NONE")

    def crc16_update(self, crc, a):
        """Cyclical calculation of a CRC-16 checksum.
        
        :param crc: The previous checksum value
        :param a: The byte to be added to the checksum
        :returns: The new CRC-16 checksum
        """
        crc = crc^a

        for i in range(8):
            if (crc & 1):
                crc = (crc >> 1)^0xA001
            else:
                crc = crc >> 1
        return crc

    def command_with_checksum(self, command):
        """Adds a CRC-16 checksum to a command.

        :param command: The command as an array of bytes
        :returns: The command including checksum as an array of bytes
        """
        crc = 0xFFFF
        for element in command:
            crc = self.crc16_update(crc, element)

        newCommand = list(command)
        newCommand.append(crc&0xFF)
        newCommand.append(crc>>8)

        return newCommand

    def get_value(self, channel):
        """Perform a pressure measurement of the specified channel and return the value

        :param channel: Ignored, since there is only one channel
        :return: The measured value in mBar
        """
        return self.pressure_gauge()[0]
