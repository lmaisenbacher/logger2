# -*- coding: utf-8 -*-
"""
Thorlabs MDT693B.
"""

import serial
import io
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
        self.ser = None
        self.sio = None

    def connect(self):
        """Open serial connection to device."""
        device = self.device
        try:
            ser = serial.Serial(
                device["Address"], timeout=device.get("Timeout"),
                **device.get('SerialConnectionParams', {}))
        except serial.SerialException:
            raise LoggerError(
                f"Serial connection with {device['Device']} couldn't be opened")
        sio = io.TextIOWrapper(
            io.BufferedRWPair(ser, ser), encoding='ASCII', newline='\r')
        self.ser = ser
        self.sio = sio

    def close(self):
        """Close serial connection to device."""
        if self.ser is not None:
            self.ser.close()

    def write(self, command):
        """Write command `command` (str) to device."""
        query = command+'\n'
        n_write_bytes = self.sio.write(command+'\n')
        if n_write_bytes != len(query):
            raise LoggerError(f"Failed to query {self.device['Device']}")
        # Flush the write buffer to send the command immediately
        self.sio.flush()

    def query(self, command):
        """Query device with command `command` (str) and return response (str)."""
        self.write(command)
        response = self.sio.readline()
        ack = self.sio.read(1)
        if ack != '>':
            raise LoggerError(f"{self.device['Device']} failed to acknowledge command")
        return response.rstrip()

    def send_command(self, command):
        """Send command `command` (str) to device and read acknowledgment."""
        self.write(command)
        ack = self.sio.read(1)
        if ack != '>':
            raise LoggerError(f"{self.device['Device']} failed to acknowledge command")

    def read_voltage(self, axis):
        """Read voltage of axis `axis` (str, either 'x', 'y', or 'z')."""
        if axis not in ['x', 'y', 'z']:
            raise LoggerError(f'Unknown axis \'{axis}\' for {self.device["Device"]}')
        command = f'{axis}voltage?'
        response = self.query(command)
        return(float(response[1:-1]))

    def set_voltage(self, axis, voltage):
        """
        Set voltage of axis `axis` (str, either 'x', 'y', or 'z') to voltage `voltage` (float, units
        of V).
        """
        if axis not in ['x', 'y', 'z']:
            raise LoggerError(f'Unknown axis \'{axis}\' for {self.device["Device"]}')
        command = f'{axis}voltage={voltage}'
        self.send_command(command)

    # def get_values(self):
    #     """Read channels."""
    #     chans = self.device['Channels']
    #     readings = {}
    #     for channel_id, chan in chans.items():
    #         if chan['Type'] in ['PV1', 'SV1']:
    #             value = self.read_temperature(chan['Type'])
    #             readings[channel_id] = value
    #         else:
    #             raise LoggerError(
    #                 f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
    #                 +f' of device \'{self.device["Device"]}\'')
    #     return readings

# device = {
#     'Device': 'Thorlabs MDT693B',
#     'Address': 'COM3',
#     'Timeout': 1,
#     'SerialConnectionParams': {
#         "baudrate":115200,
#         "bytesize":8,
#         "stopbits":1,
#         "parity":"N"
#         }
#     }
# device_instance = Device(device)
# try:
#     device_instance.connect()
#     # print(device_instance.write('echo=0'))
#     # print(device_instance.query('echo?'))
#     print(device_instance.set_voltage('y', 6))
#     print(device_instance.read_voltage('x'))
#     print(device_instance.read_voltage('y'))
#     print(device_instance.read_voltage('z'))
# except LoggerError as e:
#     print(e.value)
# finally:
#     None
#     device_instance.close()
