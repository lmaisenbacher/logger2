# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC 354 and KJLC 352 series ion pressure gauge
and the InstruTech IGM401 and IGM402 ion pressure gauge
(InstruTech seems to be the original manufacturer), and the Kurt J. Lesker KJLC 300 series Pirani
pressure gauge.
The KJLC352 and IGM402 gauges are combined gauges and have to capability to additionally read out
(two, but only the first is used here) Pirani gauges. To read out this pressure values when the ion
gauge is off, set `ReadCombinedPressure` in `DeviceSpecificParams` to True in the device
configuration file (but to False when using a KJLC 354 or IGM401 gauge). By default,
`ReadCombinedPressure` is set to False.
Some models of the ion gauges support reading out the status of the filament, which can activated
here by setting `ConfirmFilamentIsOn` in `DeviceSpecificParams` to True. If the filament is not on,
the pressure is not read out in this case. This will, however, also prevent a valid pressure
reading from the Pirani gauge of a combined gauge if its filament is off.
On the other hand, some older gauges do not support this feature, and it should be switched off.
By default, `ConfirmFilamentIsOn` is set to False.
It uses the two-wire RS-485 interface of the gauge
(not to be confused with a RS-232 interface, which uses the same 9-pin sub-D connector)
to read out the pressure in units of Torr.

Note the pin assignment of the RS-485 interface on the ion gauges:
DATA- on pin 6, DATA+ on pin 9, and ground on pin 4.
For the 300 series Pirani gauge, the pin assignment is:
DATA- on pin 2, DATA+ on pin 1, and ground on pin 4.
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

    def read_pressure(self):
        """Read pressure."""
        rsp = self.connection.readline()
        # Use checksum to see if message has been corrupted
        if rsp == '':
            raise LoggerError(
                "No response received")
        if len(rsp) != 9:
            raise LoggerError(
                f"Send string is of length {len(rsp)}"
            )
        checksum = rsp[8]
        if sum(int(byte) for byte in rsp) & 0xFF != checksum:
            raise LoggerError(
                f"Invalid checksum: {rsp}"
            )
        if rsp[3] != 0x00:
            raise LoggerError(f"Received an error response: '{rsp[3]}")
        sensor_type = rsp[7]
        # Convert pressure from ADC integer output to Torr
        pressure = int(rsp[4:6]) * (0xF0 & sensor_type) * \
            10 ** (0x0F & sensor_type) / 32e3
        return pressure

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
                    + f' of device \'{self.device["Device"]}\'')
        return readings
