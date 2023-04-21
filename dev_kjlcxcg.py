
# -*- coding: utf-8 -*-
"""
Write a docstring here
"""

import logging
import board
import busio
import adafruit_ads1x15.ads1115 as ADS
from adafruit_ads1x15.analog_in import AnalogIn


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
            self.channels = self.device["Channels"]
            for channel_id, channel in self.channels.items():
                analog_channel = ADS.ADS1115(busio.I2C(board.SCL, board.SDA), address=int(channel["I2CAddress"], 16))
                channel["ChanObj"] = AnalogIn(analog_channel, channel["Pins"]["Signal"], channel["Pins"]["Reference"])
                self.init_display()
        except OSError:
            raise LoggerError(
                f"I2C port for {device['Address']} couldn't be opened")


    def read_pressure(self, chan_obj):
        """Read pressure."""
        return chan_obj.voltage

    def get_values(self):
        """Read channels."""
        readings = {}
        for chan_num, (channel_id, channel) in enumerate(self.channels.items()):
            if channel['Type'] in ['Pressure']:
                try:
                    value = self.read_pressure(channel['ChanObj'])
                    readings[channel_id] = value
                    self.update_display(value, chan_num)
                except OSError:
                    raise LoggerError(
                        f"I2C port ({channel['I2CAddress']}) for {channel_id} couldn't be opened")
            else:
                raise LoggerError(
                    f'Unknown channel type \'{channel["Type"]}\' for channel \'{name}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings

    def clear_display(self):
        """Change all pixels to black on the display."""
        self.display.fill(0)
        self.display.show()
        return

    def init_display(self):
        """Establish a connection to the display for later use and add labels for measurements."""
        self.display = adafruit_ssd1306.SSD1306_I2C(128, 64, board.I2C(), addr=0x3D, reset=digitalio.DigitalInOut(board.D4))
        self.clear_display()
        self.image = Image.new("1", (self.dispaly.width, self.display.height))
        self.draw = ImageDraw.Draw(self.image)
        self.font = ImageFont.load_default()
        for i, channel_id in enumerate(self.channels.keys()):
            text = f"{channel_id}:"
            self.draw.text(
            (0, i * self.display.width // 4),
            text,
            font=self.font,
            fill=255,
            )
        return
    
    def update_display(self, value, channel_num):
        """
            Update the display with current pressure gauge readings in Torr for the channel with channel_num.
            Readings appear in the middle of the screen
            
            channel_num : int
                Channel number based on order in channel.keys().
        """
        text = f"{value} Torr"
        self.draw.text(
            (self.display // 2, channel_num * self.height // 4),
            text,
            font=self.font,
            fill=255,
        )
        return