# -*- coding: utf-8 -*-
"""
This module contains drivers for the Kurt J. Lesker KJLC Carbon XCG Series pressure gauge.
A custom controller has been built to read out analog voltages from the pressure gauges with 
some integrated circuits that communicate using the I2C protocol. A single instance of this device
can measure up to four different XCG pressure gauges, each being on a different 'channel' on 
the controller. In addition to the pressure gauge readout this driver also updates a small 
OLED display on the front of the pressure gauge controller. 

The multiplier for each channel is used to scale the analog voltage to corresponding pressure.
The conversion is 100 Torr/V, however since the maximum ADC voltage rating is ~ 4V, we have to
scale down the output as well. The multiplier for each channel accounts for both of these scaling factors.

A USB type C cable can be used to connect to the front of the controller. 
"""

import busio
import board
from defs import LoggerError
import dev_generic
import adafruit_ssd1306
from PIL import Image, ImageDraw, ImageFont
from adafruit_ads1x15.analog_in import AnalogIn
import adafruit_ads1x15.ads1115 as ADS
import time
import os

os.environ["BLINKA_MCP2221"] = "1"


class Device(dev_generic.Device):

    ADS_CHANNELS = {
        0:
        {
            "Address": 0x48,
            "Pins": (ADS.P0, ADS.P1),
            "Multiplier": 1
        },
        1:
        {
            "Address": 0x48,
            "Pins": (ADS.P2, ADS.P3),
            "Multiplier": 1
        }  # ,
        # 2:
        # {
        #    "Address": 0x49,
        #    "Pins": (ADS.P0, ADS.P1),
        #    "Multiplier": 1
        # },
        # 3:
        # {
        #    "Address": 0x49,
        #    "Pins": (ADS.P2, ADS.P3),
        #    "Multiplier": 1
        # }
    }

    DISPLAY_WIDTH = 128
    DISPLAY_HEIGHT = 64

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)
        # initialize I2C bus, used for display and ADS1115
        i2c = busio.I2C(board.SCL, board.SDA)
        # Initialize ADS1115
        self.channels = self.make_channels(i2c)
        # Initialize Display
        self.display = adafruit_ssd1306.SSD1306_I2C(
            self.DISPLAY_WIDTH, self.DISPLAY_HEIGHT, i2c, addr=0x3d)

    def make_channels(self, i2c):
        """ 
        Create instances of channel objects that the device instance will use to record voltages.

        i2c : busio.I2C
            Instance of an I2C bus. ADS channels communicate over this instance of an I2C bus.
        """
        channels = []
        for chan in self.ADS_CHANNELS:
            adc = ADS.ADS1115(i2c, address=self.ADS_CHANNELS[chan]["Address"])
            channels.append(AnalogIn(adc, *self.ADS_CHANNELS[chan]["Pins"]))
        return channels

    def read_pressure(self, channel):
        """
        Read the last recorded pressure from a specified channel. Pressure is measured in Torr.

        channel : int
            Channel number ranging from zero to three. 
        """
        return self.voltage_to_pressure(channel, self.channels[channel].voltage)

    def voltage_to_pressure(self, channel, voltage):
        """
        Convert the voltage for measured for a gauge to the corresponding pressure in Torr.

        channel : int
            Channel number ranging from zero to three.
        voltage : float
            Voltage measured from pressure gauge. 
        """
        return self.ADS_CHANNELS[channel]["Multiplier"] * voltage

    def update_display(self, readings, font=None, padding_fraction=1.5):
        """
        Update the display with the most recent pressure readings from the pressure gauges that are connected. 

        readings : dict
            A dictionary with the keys as channel names specified in self.device['channels'] and the values as the 
            pressure readings.
        font : ImageFont
            An instance of PIL.ImageFont. If not set then the default PIL image font is used.
        padding_fraction : float
            A factor used to determine how much spacing should be between text. Default is 1.5 times the bounding box of the text.
        """
        if font == None:
            font = ImageFont.load_default()
        image = Image.new("1", (self.display.width, self.display.height))
        draw = ImageDraw.Draw(image)
        text = "Units = Torr"
        (font_width, font_height) = font.getbbox(text)[2:]
        draw.text((0, self.display.height - font_height),
                  "Units = Torr",
                  font=font,
                  fill=255,
                  )
        for i, chan in enumerate(readings):
            # In the future save on calculations needed for positioning message
            text = f"{chan}: {readings[chan]:.2e}"
            (font_width, font_height) = font.getbbox(text)[2:]
            draw.text((0, padding_fraction * i * font_height // len(readings)),
                      text,
                      font=font,
                      fill=255,
                      )
        self.display.image(image)
        self.display.show()
        return

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['Pressure']:
                value = self.read_pressure(chan['DeviceChannel'])
                readings[channel_id] = value
            else:
                raise LoggerError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    + f' of device \'{self.device["Device"]}\'')
        self.update_display(readings)
        return readings
