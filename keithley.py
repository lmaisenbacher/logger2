# -*- coding: utf-8 -*-

"""This module contains drivers for the Keithley 2001 multimeter connected via VISA/GPIB.
"""

import time
import pyvisa as visa

SWITCHTIME = 10E-3 # Time (in s) to sleep between switching channel and taking measurement

class Multimeter(object):

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

    def __init__(self, resourceName):
        """
        :param resourceName: The multimeter's VISA resource name.
        """
        self.devicePresent = False
        try:
            rm = visa.ResourceManager('@py')
            self.device = rm.open_resource(resourceName)
        except:
            print("Could not open Keithley 2001 Multimeter (VISA resource {}).".format(resourceName))
            return
        else:
            self.devicePresent = True
        self.device.write("SYSTEM:PRESET") # Set all settings to default
        self.device.write(":SENSE:VOLTAGE:DC:AVERAGE:TCON REP") # Set the averaging mode to repeat
        self.device.write(":INITIATE:CONTINUOUS OFF") # Disable continuous acquisition
        self.device.timeout = 60000 # Set timeout to 60 s for long acquisitions
