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
            ch_range = device_specific_params.get('Range')
            ch_range_str = f'{ch_range:.12f},' if ch_range is not None else ''
            if ch_range is not None:
                # Set 6 1/2 digits resolution if not specified otherwise
                ch_res = device_specific_params.get('Resolution', ch_range*1e-6)
                ch_res_str = f'{ch_res:.18f},'
            if chan['Type'] == 'DCV':
                # DC voltage
                if (num_plc := chan["DeviceSpecificParams"].get('NPLC')) is not None:
                    self.visa_write(f'VOLT:DC:NPLC {num_plc:f},(@{chan["DeviceChannel"]})')
                self.visa_write(
                    f'CONF:VOLT:DC {ch_range_str}{ch_res_str}(@{chan["DeviceChannel"]})')
            if chan['Type'] == 'ACV':
                # AC voltage
                cmd = f'CONF:VOLT:AC {ch_range_str}{ch_res_str}(@{chan["DeviceChannel"]})'
                self.visa_write(cmd)
            if chan['Type'] == 'RES':
                # Resistance
                cmd = f'CONF:RES {ch_range_str}{ch_res_str}(@{chan["DeviceChannel"]})'
                self.visa_write(cmd)
            if chan['Type'] == 'TEMPT':
                # T-type thermocouple
                cmd = f'CONF:TEMP:TCouple T,(@{chan["DeviceChannel"]})'
                self.visa_write(cmd)
        device_channels = np.array(device_channels)
        # Configure scan
        device_channels_str = ','.join([f'{elem:d}' for elem in device_channels])
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
        dev_chans_read = data[1::2].astype(int)
        reading_by_dev_chan = {dev_chan: value for dev_chan, value in zip(dev_chans_read, values)}
        if not np.all(np.isin(dev_chans_read, self.device_channels)):
            msg = (
                'Returned measurements (channel(s) {}) do not match requested channel(s) ({})'
                .format(
                    ','.join([f'{elem:d}' for elem in dev_chans_read]), self.device_channels_str))
            raise DeviceError(msg)
        readings = {
            channel_id: reading_by_dev_chan[channel['DeviceChannel']]
            for channel_id, channel in self.device['Channels'].items()}
        return readings
