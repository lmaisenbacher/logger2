# -*- coding: utf-8 -*-

"""This module contains drivers for the Keithley 2001 multimeter connected via VISA/GPIB.
"""

import time
import logging
import numpy as np

import dev_generic

from defs import LoggerError

logger = logging.getLogger()

SWITCHTIME = 10E-3 # Time (in s) to sleep between switching channel and taking measurement

class Device(dev_generic.Device):

    def get_value(self, channel):
        """Set the switcher to the specified channel and take a measurement

        :param channel: The channel (1-10) to use
        :return: The measured voltage in V
        """
        self.setChannel(channel)
        time.sleep(SWITCHTIME)
        return self.measureVoltage()[0]
    
    def setChannel(self, channel):
        """Sets the measurement channel using the built-in switcher.
        :param channel: The channel (1-10) to use.
        """
        if not self.devicePresent:
            return
        self.device.write(":ROUTE:CLOSE (@ {})".format(channel))
        
    def measureVoltage(self):
        """Take a voltage measurement.
        :returns: voltage, measurementTime, measurementNr, channel
        """
        if not self.devicePresent:
            return
        retstring = self.device.query("READ?")
        voltagestring, timestring, measurementNrstring, channelstring = retstring.split(",")
        voltage = float(voltagestring.rstrip("NVDC"))
        measurementTime = float(timestring.rstrip("SECS"))
        measurementNr = int(measurementNrstring.rstrip("RDNG#"))
        channel = int(channelstring.rstrip("INTCHAN\n"))
        return voltage, measurementTime, measurementNr, channel
        
    def setNrMeasurements(self, nrMeasurements):
        """Sets the number of measurements that the digital filter takes
        :param nrAverages: The number of measurements (1-100)
        """
        if not self.devicePresent:
            return
        self.device.write(":SENSE:VOLTAGE:DC:AVERAGE:COUNT {}".format(nrMeasurements))

    def setAveragingState(self, state):
        """Enables or disables the digital averaging filter
        :param state: True for averaging enabled and false for averaging disabled
        """
        if not self.devicePresent:
            return
        self.device.write(":SENSE:VOLTAGE:DC:AVERAGE {}".format(int(state)))

    def setNrPLCycles(self, nrPLCycles):
        """Sets the number of power line cycles to integrate over
        :param nrPLCycles: The number of power line cycles to integrate (0.01-10)
        """
        if not self.devicePresent:
            return
        self.device.write(":SENSE:VOLTAGE:DC:NPLC {}".format(nrPLCycles))

    def __init__(self, device):
        """
        :param resourceName: The multimeter's VISA resource name.
        """
        super(Device, self).__init__(device)
        
        self.init_visa()
        self.device_channels, self.device_channels_str = self.configure_channels()

        # self.device.write("SYSTEM:PRESET") # Set all settings to default
        # self.device.write(":SENSE:VOLTAGE:DC:AVERAGE:TCON REP") # Set the averaging mode to repeat
        # self.device.write(":INITIATE:CONTINUOUS OFF") # Disable continuous acquisition
        # self.resource.timeout = 60000 # Set timeout to 60 s for long acquisitions

    def configure_channels(self):
        """Configure channels."""
        chans = self.device['Channels']
        device_channels = []
        for channel_id, chan in chans.items():
            device_channels.append(chan['DeviceChannel'])            
            if chan['Type'] == 'DCV':
                # DC voltage                
                	# 10 V with 6 1/2 digits resolution
                chRange = 5
                chRes = chRange*1e-6
                self.visa_write(
                    'CONF:VOLT:DC {:.12f},{:.18f},(@{})'.format(
                        chRange, chRes, chan['DeviceChannel']))
#         # Temperature (J type thermocouple)
#         if np.any(self.chans['Type']=='TEMPJ'):
#             channelList = ','.join(['{:d}'.format(self.to_int(elem)) for elem in self.chans[self.chans['Type']=='TEMPJ']['DeviceChannel'].values])
#             self.DAQclass.VISAwrite(self.connID, 'CONF:TEMP TC,J,(@{})'.format(channelList))
#         # Resistance
#         if np.any(self.chans['Type']=='RES'):
#             channelList = ','.join(['{:d}'.format(self.to_int(elem)) for elem in self.chans[self.chans['Type']=='RES']['DeviceChannel'].values])
#             self.DAQclass.VISAwrite(self.connID, 'CONF:RES (@{})'.format(channelList))
        device_channels = np.array(device_channels)
        # Configure scan
        device_channels_str = ','.join([f'{elem:d}' for elem in device_channels])
        print(device_channels_str)
        self.visa_write(f'ROUT:SCAN (@{device_channels_str})')
        logger.info(self.visa_query('ROUTE:SCAN:SIZE?'))
        logger.info(self.visa_query('ROUTE:SCAN?'))
        nSamplesInScan = 1
        if len(chans) > 0:
            self.visa_write(f'TRIG:COUNT {nSamplesInScan:d}')
            # Switch on channel number in returned data
            self.visa_write('FORM:READ:CHAN ON')

        return device_channels, device_channels_str

    def get_values(self):
        """Read configured channels."""
        self.visa_write(f'ROUT:SCAN (@{self.device_channels_str})')
        data = self.visa_query('READ?', return_ascii=True)
        values = data[::2]
        chans_read = data[1::2].astype(int)
        print(values)
        print(chans_read)
        if not np.all(np.isin(chans_read, self.device_channels)):
            msg = (
                'Returned measurements (channel(s) {}) do not match requested channel(s) ({})'
                .format(','.join([f'{elem:d}' for elem in chans_read]), self.device_channels_str))
            raise LoggerError(msg)
        readings = {
            channel_id: value for channel_id, value in zip(self.device['Channels'].keys(), values)}
        return readings
