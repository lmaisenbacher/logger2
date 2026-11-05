# -*- coding: utf-8 -*-
"""
This module contains drivers for the Stanford Research Instruments CTC100
cryogenic temperature controller using its USB interface,
which implements a virtual serial port.
The hardware driver is `amodevices.SRSCTC100`; this module adds the
logger-facing channel handling.

Every channel is read at most every 'MinInterval_s' seconds (in the
channel's 'DeviceSpecificParams'; default 0, every poll), so a fast logger
can log quantities that change slowly at a slower rate; a poll returns
only the channels that were due. The 'Temperature' and 'HeaterPower'
channels due in a poll come from one `getOutput?` query, the latest
conversion of every channel of the CTC100, so a poll costs one round trip
however many of them are logged. 'PIDSetpoint' and 'Custom' channels are
a query each. 'CTC100ChannelName' (in the channel's tags) names the CTC100
channel as the CTC100 does, spaces optional and case ignored, as in its
own commands.
"""

import numbers
import time

from amodevices import SRSCTC100
from amodevices.dev_exceptions import DeviceError

class Device(SRSCTC100):

    OUTPUT_TYPES = ('Temperature', 'HeaterPower')
    QUERY_TYPES = ('PIDSetpoint', 'Custom')
    DEFAULT_MIN_INTERVAL_S = 0.0

    def __init__(self, device, update_callback_func=None):
        """Initialize device with configuration dict `device`; refuses an
        unknown channel type or a bad 'MinInterval_s' at startup."""
        for channel_id, chan in device['Channels'].items():
            where = f'channel \'{channel_id}\' of device \'{device["Device"]}\''
            if chan['Type'] not in self.OUTPUT_TYPES + self.QUERY_TYPES:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for {where}')
            interval = self._min_interval(chan)
            if (not isinstance(interval, numbers.Real)
                    or isinstance(interval, bool) or interval < 0):
                raise DeviceError(
                    f'\'MinInterval_s\' of {where} must be a non-negative'
                    f' number of seconds, not {interval!r}')
        super().__init__(device)
        # Channel ID -> monotonic time of its last read
        self._last_read = {}

    @classmethod
    def _min_interval(cls, chan):
        """The channel's 'MinInterval_s' (its 'DeviceSpecificParams'), or
        the default."""
        return chan.get('DeviceSpecificParams', {}).get(
            'MinInterval_s', cls.DEFAULT_MIN_INTERVAL_S)

    @staticmethod
    def _key(name):
        """A CTC100 channel name as the CTC100 matches it: without spaces,
        case ignored."""
        return name.replace(' ', '').lower()

    def get_values(self):
        """Read the channels that are due."""
        chans = self.device['Channels']
        readings = {}
        outputs = None
        now = time.monotonic()
        for channel_id, chan in chans.items():
            last = self._last_read.get(channel_id)
            if last is not None and now - last < self._min_interval(chan):
                continue
            if chan['Type'] in self.OUTPUT_TYPES:
                if outputs is None:
                    outputs = {self._key(name): value
                               for name, value in self.read_outputs().items()}
                name = chan['tags']['CTC100ChannelName']
                if self._key(name) not in outputs:
                    raise DeviceError(
                        f'No CTC100 channel \'{name}\' (channel'
                        f' \'{channel_id}\' of device \'{self.device["Device"]}\')')
                value = outputs[self._key(name)]
            elif chan['Type'] == 'PIDSetpoint':
                value = self.read_pid_setpoint(chan["tags"]["CTC100ChannelName"])
            else:
                value = self.query_custom_command(chan["tags"]["CTC100CustomCommand"])
            readings[channel_id] = value
            self._last_read[channel_id] = now
        return readings
