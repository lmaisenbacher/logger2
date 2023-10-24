# -*- coding: utf-8 -*-
"""
This module contains drivers for the Keysight DAQ970A/973A multimeter connected via VISA.
"""

import logging
import numpy as np

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

        self.init_visa()
        self.device_channels, self.device_channels_str = self.configure_channels()

    def set_averaging_time(self, averaging_time, device_channels=None):
        """
        Sets the averaging time for DC voltage measurements for the given device channels.

        averaging_time : float
            The number of power line cycles to integrate.
            Possible values:
            0.001, 0.002, 0.006, 0.02, 0.06, 0.2, 1, 2, 10, 20, 100, 200.
        device_channels : list-like, default: None
            Device channels affected. If set to None, all channels are used.
        """
        if not self.device_present:
            return
        if device_channels is not None:
            device_channels_str = ','.join([f'{elem:d}' for elem in device_channels])
            self.visa_write(f'VOLT:DC:APER:ENAB ON, (@{device_channels_str})')
            self.visa_write(
                f'VOLT:DC:APER {averaging_time:f}, (@{device_channels_str})')
        else:
            self.visa_write('VOLT:DC:APER:ENAB ON')
            self.visa_write(
                f'VOLT:DC:APER {averaging_time:f}')

    # def set_num_line_cycles(self, num_plc, device_channels=None):
    #     """
    #     Sets the number of power line cycles to integrate over for DC voltage measurements
    #     for the given device channels.

    #     num_plc : float
    #         The number of power line cycles to integrate.
    #         Possible values:
    #         0.001, 0.002, 0.006, 0.02, 0.06, 0.2, 1, 2, 10, 20, 100, 200.
    #     device_channels : list-like, default: None
    #         Device channels affected. If set to None, all channels are used.
    #     """
    #     if not self.device_present:
    #         return
    #     # if device_channels is not None:
    #     #     device_channels_str = ','.join([f'{elem:d}' for elem in device_channels])
    #     #     self.visa_write(f'VOLT:DC:NPLC {num_plc:f}, (@{device_channels_str})')
    #     # else:
    #     #     self.visa_write(f'VOLT:DC:NPLC {num_plc:f}')

    def configure_channels(self):
        """Configure channels."""
        chans = self.device['Channels']
        device_channels = []
        for channel_id, chan in chans.items():
            device_channels.append(chan['DeviceChannel'])
            device_specific_params = chan.get('DeviceSpecificParams', {})
            # ch_range = device_specific_params.get('Range')
            chRange = device_specific_params.get('Range')
            if chan['Type'] == 'DCV':
                # DC voltage
                # 6 1/2 digits resolution
                chRes = chRange*1e-6
                if (num_plc := chan["DeviceSpecificParams"].get('NPLC')) is not None:
                    self.visa_write(f'VOLT:DC:NPLC {num_plc:f},(@{chan["DeviceChannel"]})')
                self.visa_write(
                    f'CONF:VOLT:DC {chRange:.12f},{chRes:.18f},(@{chan["DeviceChannel"]})')
            if chan['Type'] == 'ACV':
                # AC voltage
                # 6 1/2 digits resolution
                chRes = chRange*1e-6
                cmd = f'CONF:VOLT:AC {chRange:.12f},{chRes:.18f},(@{chan["DeviceChannel"]})'
                self.visa_write(cmd)
            if chan['Type'] == 'RES':
                # Resistance
                # 6 1/2 digits resolution
                chRes = chRange*1e-6
                cmd = f'CONF:RES {chRange:.12f},{chRes:.18f},(@{chan["DeviceChannel"]})'
                self.visa_write(cmd)
#         # Temperature (J type thermocouple)
#         if np.any(self.chans['Type']=='TEMPJ'):
#             channelList = ','.join(['{:d}'.format(self.to_int(elem)) for elem in self.chans[self.chans['Type']=='TEMPJ']['DeviceChannel'].values])
#             self.DAQclass.VISAwrite(self.connID, 'CONF:TEMP TC,J,(@{})'.format(channelList))
        device_channels = np.array(device_channels)
        # Configure scan
        device_channels_str = ','.join([f'{elem:d}' for elem in device_channels])
        # print(device_channels_str)
        # self.visa_write(f'ROUT:SCAN (@{device_channels_str})')
        # logger.info(self.visa_query('ROUTE:SCAN:SIZE?'))
        # logger.info(self.visa_query('ROUTE:SCAN?'))
        if len(chans) > 0:
            # Switch on channel number in returned data
            self.visa_write('FORM:READ:CHAN ON')
        # if self.device['DeviceSpecificParams'].get('NPLC'):
            # print(self.device.get('NPLC'))
            # self.set_num_line_cycles(self.device['DeviceSpecificParams']['NPLC'], device_channels)
        if self.device.get('AveragingTime'):
            # print(self.device.get('AveragingTime'))
            self.set_averaging_time(self.device['AveragingTime'], device_channels)

        return device_channels, device_channels_str

    def get_values(self):
        """Read channels."""
        self.visa_write(f'ROUT:SCAN (@{self.device_channels_str})')
        data = self.visa_query('READ?', return_ascii=True)
        # print(self.visa_query(f'VOLT:DC:NPLC? (@{self.device_channels_str})'))
        # print(self.visa_query(f'VOLT:DC:APER:ENAB? (@{self.device_channels_str})'))
        # print(self.visa_query(f'VOLT:DC:APER? (@{self.device_channels_str})'))
        values = data[::2]
        chans_read = data[1::2].astype(int)
        # print(values)
        # print(chans_read)
        if not np.all(np.isin(chans_read, self.device_channels)):
            msg = (
                'Returned measurements (channel(s) {}) do not match requested channel(s) ({})'
                .format(','.join([f'{elem:d}' for elem in chans_read]), self.device_channels_str))
            raise DeviceError(msg)
        readings = {
            channel_id: value for channel_id, value in zip(self.device['Channels'].keys(), values)}
        return readings
