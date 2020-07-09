"""This module contains drivers for Burleigh WA-1000 and WA-1500 wavemeters connected via serial interface."""

import logging
import time
import serial

LOG = logging.getLogger(__name__)

class Wavemeter():

    def __init__(self, serialport):
        """Initialize the serial connection to the device.

        :serialport: the serial port to use
        """
        self.device_present = False
        try:
            self.serial = serial.Serial(serialport)
        except serial.SerialException as err:
            LOG.error("Could not connect tu Burleigh WA-1000/1500 wavemeter. Error: %s", err)
        else:
            self.device_present = True
            self.serial.timeout = 1
            self.serial.write("@\x0F\r\n".encode()) # Reset device to stored defaults
            time.sleep(100E-3)
            self.serial.write("@\x51\r\n".encode()) # Disable automatic broadcasts
            time.sleep(100E-3)
            self.serial.flushInput() # Flush old broadcasts

    def get_frequency(self):
        """Return the currently measured laser frequency in Hz or -1 of the device is not present"""
        if not self.device_present:
            LOG.warning("get_frequency() called for non-present Burleigh WA-1000/1500 wavemeter.")
            return -1
        self.serial.write("@\x51\r\n".encode()) # Query
        response = self.serial.read(23)
        return 1E9*float(response[1:11])

    def get_value(self, _channel):
        """Perform a pressure measurement of the specified channel and return the value

        :param _channel: Ignored, since there is only one channel
        :return: The measured value in Hz
        """
        return self.get_frequency()
