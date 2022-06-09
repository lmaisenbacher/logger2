from ssl import PROTOCOL_TLSv1
import serial
import logging
import json

import dev_generic

from defs import LoggerError

"""A Class to represent devices using serial communication. This is intended for single function communication between an SMC HRS012-AN-10-T
and Raspberry Pi."""

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
                device["Address"], baudrate=device["SerialBaudrate"],
                bytesize=device["SerialBytesize"], parity=device["SerialParity"],
                stopbits=device["SerialStopbits"], timeout=device["Timeout"])
        except serial.SerialException:
            raise LoggerError(
                f"Serial connection with {device['Device']} couldn't be opened")

    def generate_query(self, meas_type):
        return f'\x02{self.device["SMCAddress"]}R{meas_type}\x03'.encode("ASCII")

    def read_float(self, meas_type):
        query = self.generate_query(meas_type)
        n_write_bytes = self.connection.write(query)
        if n_write_bytes != len(query):
            raise LoggerError(f"Failed to query {self.device['Device']}")
        rsp = self.connection.readline()
        if rsp.decode()[3] != '\x06':
            raise LoggerError(f"Didn't Receive Acknowledgement from {self.device['Device']}") 
        return float(rsp[7:12]) / 10

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['PV1', 'SV1']:
                value = self.read_float(chan['Type'])
                readings[channel_id] = value
            else:
                raise LoggerError(f"Malformed ASCII Query to {self.device['Device']}: {chan['Type']}")
        return readings
        # self.visa_write(f'ROUT:SCAN (@{self.device_channels_str})')        
        # data = self.visa_query('READ?', return_ascii=True)
        # # print(self.visa_query(f'VOLT:DC:NPLC? (@{self.device_channels_str})'))
        # # print(self.visa_query(f'VOLT:DC:APER:ENAB? (@{self.device_channels_str})'))
        # # print(self.visa_query(f'VOLT:DC:APER? (@{self.device_channels_str})'))
        # values = data[::2]
        # chans_read = data[1::2].astype(int)
        # # print(values)
        # # print(chans_read)
        # if not np.all(np.isin(chans_read, self.device_channels)):
        #     msg = (
        #         'Returned measurements (channel(s) {}) do not match requested channel(s) ({})'
        #         .format(','.join([f'{elem:d}' for elem in chans_read]), self.device_channels_str))
        #     raise LoggerError(msg)
        # readings = {
        #     channel_id: value for channel_id, value in zip(self.device['Channels'].keys(), values)}
        # return readings
