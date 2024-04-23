# -*- coding: utf-8 -*-
"""
Add a short description about the functionality of this code here...
"""

import serial
import logging

from amodevices import dev_generic
from amodevices.dev_exceptions import DeviceError

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
            raise DeviceError(
                f"Serial connection on port {device['Address']} couldn't be opened")

    def query(self, command):
        """Query device with command `command` (str) and return response."""
        internal_address = self.device["DeviceSpecificParams"]["InternalAddress"]
        query = f'#{internal_address}{command}\r'.encode(encoding="ASCII")
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise DeviceError("Failed to write to device")
        rsp = self.connection.readline()
        try:
            rsp = rsp.decode(encoding="ASCII")
        except UnicodeDecodeError:
            raise DeviceError(f"Error in decoding response ('{rsp}') received")
        if rsp == '':
            raise DeviceError(
                "No response received")
        if rsp.startswith("?"):
            raise DeviceError(
                f"Received an error response: '{rsp}'")
        if not rsp.startswith(f"*{internal_address} "):
            raise DeviceError(
                f"Didn't receive correct acknowledgement (response received: '{rsp}')")
        return rsp[4:]

    def read_temperature(self):
        """Read temperature."""
        # Use the query method to send a message to the temperature monitor, but first make sure
        # you configure the method does what you want it to. It was just taken directly from the KJLC354
        # device class, which also uses serial, but there are likely differences with how messages are formatted
        # there. Be sure to corrector for any changes!
        temperature = None
        return temperature

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Temperature']:
                value = self.read_temperature()
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        return readings
