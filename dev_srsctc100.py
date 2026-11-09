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

The device may set 'ADRate_ms' in its own 'DeviceSpecificParams': the
A/D rate (System.Other.A/D rate), the time between two conversions of
every input, one of the values the CTC100 offers for its line frequency
(16.7, 33.3, 50, ... 1000 ms at 60 Hz). Read at every connection and set
when it differs; a failure is logged and the rate read back used.

A 'Temperature' or 'HeaterPower' channel may also set, in its
'DeviceSpecificParams':

- 'Figures' (0 to 6): the digits after the decimal point of its value in
  the CTC100's replies (Channel.Figures; 3 unless set, mK steps for a
  temperature in K). It also sets the front-panel display, but not the
  CTC100's own log, its PID feedback or its plots. Read at every
  connection and set when it differs; a failure is logged and the channel
  read as it is.
- 'MaxUnchanged_s': the channel is returned only when its value differs
  from the one it returned before, or when that one was returned this many
  seconds ago or more. With 'Figures' 6 two conversions practically never
  agree, so a logger polling faster than the CTC100 converts writes each
  conversion once, and a value that stays the same is still written every
  'MaxUnchanged_s'. Two NaNs (a sensor open or out of range) count as the
  same value.

The values from `getOutput?` are stamped with the middle of the
conversion they most likely are (`reading_times_ns`, which the logger
uses instead of the poll's time). The CTC100 replies with its latest
conversion, which ended at most one A/D period (System.Other.A/D rate,
read at every connection) before the query was sent and, for a value
that changed since the previous `getOutput?`, after that one was sent
(the query's transfer to the CTC100 neglected). The stamp is the middle
of that span less half an A/D period. A logger polling back to back
dates a new conversion to within half the time between two queries.
"""

import logging
import math
import numbers
import time

from amodevices import SRSCTC100
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger(__name__)


class Device(SRSCTC100):

    OUTPUT_TYPES = ('Temperature', 'HeaterPower')
    QUERY_TYPES = ('PIDSetpoint', 'Custom')
    DEFAULT_MIN_INTERVAL_S = 0.0
    # 'DeviceSpecificParams' keys of the output channels only
    OUTPUT_PARAMS = ('Figures', 'MaxUnchanged_s')
    # Range of an A/D rate the CTC100 can take (ms; manual, A/D rate)
    AD_RATE_RANGE_MS = (10.0, 1000.0)
    # Wait between setting the A/D rate and reading it back (s; assumed
    # enough for the CTC100 to apply it)
    AD_RATE_SETTLE_S = 0.5

    def __init__(self, device, update_callback_func=None):
        """Initialize device with configuration dict `device`; refuses an
        unknown channel type, a bad 'ADRate_ms', 'MinInterval_s',
        'Figures' or 'MaxUnchanged_s', or two 'Figures' for one CTC100
        channel at startup."""
        rate = device.get('DeviceSpecificParams', {}).get('ADRate_ms')
        low, high = self.AD_RATE_RANGE_MS
        if rate is not None and not (
                isinstance(rate, numbers.Real) and not isinstance(rate, bool)
                and low <= rate <= high):
            raise DeviceError(
                f'\'ADRate_ms\' of device \'{device["Device"]}\' must be a'
                f' number of ms from {low:g} to {high:g}, not {rate!r}')
        figures = {}
        for channel_id, chan in device['Channels'].items():
            where = f'channel \'{channel_id}\' of device \'{device["Device"]}\''
            if chan['Type'] not in self.OUTPUT_TYPES + self.QUERY_TYPES:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for {where}')
            if not self._is_seconds(self._min_interval(chan)):
                raise DeviceError(
                    f'\'MinInterval_s\' of {where} must be a non-negative'
                    f' number of seconds, not {self._min_interval(chan)!r}')
            params = chan.get('DeviceSpecificParams', {})
            for key in self.OUTPUT_PARAMS:
                if key in params and chan['Type'] not in self.OUTPUT_TYPES:
                    raise DeviceError(
                        f'\'{key}\' of {where}: only for a Temperature or'
                        ' HeaterPower channel')
            if 'MaxUnchanged_s' in params and not self._is_seconds(
                    params['MaxUnchanged_s']):
                raise DeviceError(
                    f'\'MaxUnchanged_s\' of {where} must be a non-negative'
                    f' number of seconds, not {params["MaxUnchanged_s"]!r}')
            if 'Figures' in params:
                value = params['Figures']
                if (not isinstance(value, int) or isinstance(value, bool)
                        or not 0 <= value <= 6):
                    raise DeviceError(
                        f'\'Figures\' of {where} must be an integer from 0'
                        f' to 6, not {value!r}')
                key = self._key(chan['tags']['CTC100ChannelName'])
                if figures.setdefault(key, value) != value:
                    raise DeviceError(
                        f'\'Figures\' of {where}: another channel sets'
                        f' {figures[key]} for the same CTC100 channel')
        super().__init__(device)
        # Channel ID -> monotonic time of its last read
        self._last_read = {}
        self._reset_history()
        # A/D period (ns), read at every connection; None when unknown
        self._ad_period_ns = None
        # Channel ID -> time (ns since the epoch) of the values the latest
        # `get_values` returned from `getOutput?`
        self.reading_times_ns = {}

    def _reset_history(self):
        """Forget the previous `getOutput?` and the values returned (a new
        connection)."""
        # The previous `getOutput?` reply (channel key -> value) and when
        # it was sent (ns since the epoch)
        self._previous_outputs = None
        self._previous_sent_ns = None
        # Channel ID -> (value, monotonic time) last returned, for
        # 'MaxUnchanged_s'
        self._last_returned = {}

    @staticmethod
    def _is_seconds(value):
        """True for a non-negative number of seconds (never a bool)."""
        return (isinstance(value, numbers.Real) and not isinstance(value, bool)
                and value >= 0)

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

    @staticmethod
    def _same(a, b):
        """True when `a` and `b` are the same value, two NaNs included."""
        if a is None or b is None:
            return False
        return a == b or (isinstance(a, float) and isinstance(b, float)
                          and math.isnan(a) and math.isnan(b))

    def connect(self):
        """Open the serial connection, set the A/D rate ('ADRate_ms') and
        read it, and set the channels' 'Figures'."""
        super().connect()
        self._reset_history()
        try:
            self._ad_period_ns = round(self._apply_ad_rate() * 1e9)
        except DeviceError as err:
            self._ad_period_ns = None
            logger.warning(
                'Device \'%s\': could not read the A/D rate (%s) — values'
                ' stamped when their query was sent', self.device['Device'],
                err)
        done = set()
        for channel_id, chan in self.device['Channels'].items():
            figures = chan.get('DeviceSpecificParams', {}).get('Figures')
            name = chan.get('tags', {}).get('CTC100ChannelName')
            if figures is None or self._key(name) in done:
                continue
            done.add(self._key(name))
            try:
                before = self.read_figures(name)
                if before == str(figures):
                    continue
                self.set_figures(name, figures)
                after = self.read_figures(name)
            except DeviceError as err:
                logger.warning(
                    'Device \'%s\': could not set the Figures of CTC100'
                    ' channel \'%s\' to %d (%s) — read as it is',
                    self.device['Device'], name, figures, err)
                continue
            if after == str(figures):
                logger.info(
                    'Device \'%s\': Figures of CTC100 channel \'%s\' set'
                    ' from %s to %s', self.device['Device'], name, before,
                    after)
            else:
                logger.warning(
                    'Device \'%s\': Figures of CTC100 channel \'%s\' reads'
                    ' %s after setting it to %d — read as it is',
                    self.device['Device'], name, after, figures)

    def _apply_ad_rate(self):
        """Set the A/D rate to 'ADRate_ms' where it differs; return the
        A/D period the CTC100 reads back (s). A rate it does not take is
        logged, not raised."""
        rate = self.device['DeviceSpecificParams'].get('ADRate_ms')
        before = self.read_ad_period()
        if rate is None or abs(before * 1e3 - rate) < 1e-3:
            return before
        try:
            self.set_ad_rate(rate)
            time.sleep(self.AD_RATE_SETTLE_S)
            after = self.read_ad_period()
        except DeviceError as err:
            logger.warning(
                'Device \'%s\': could not set the A/D rate to %g ms (%s)',
                self.device['Device'], rate, err)
            return self.read_ad_period()
        if abs(after * 1e3 - rate) < 1e-3:
            logger.info('Device \'%s\': A/D rate set from %g to %g ms',
                        self.device['Device'], before * 1e3, after * 1e3)
        else:
            logger.warning(
                'Device \'%s\': A/D rate reads %g ms after setting it to'
                ' %g ms', self.device['Device'], after * 1e3, rate)
        return after

    def _read_outputs(self):
        """`getOutput?` (channel key -> value) and, per channel key, the
        middle of the conversion its value most likely is (ns since the
        epoch)."""
        outputs = {self._key(name): value
                   for name, value in self.read_outputs().items()}
        sent = self.query_sent_ns
        previous, previous_sent = self._previous_outputs, self._previous_sent_ns
        self._previous_outputs, self._previous_sent_ns = outputs, sent
        period = self._ad_period_ns
        if period is None:
            return outputs, dict.fromkeys(outputs, sent)
        times = {}
        for key, value in outputs.items():
            # The conversion ended within one period before the query was
            # sent, and after the previous query when it is new since
            start = sent - period
            if (previous is not None and previous_sent > start
                    and not self._same(value, previous.get(key))):
                start = previous_sent
            times[key] = (start + sent) // 2 - period // 2
        return outputs, times

    def _unchanged(self, channel_id, chan, value, now):
        """True when 'MaxUnchanged_s' leaves `value` out: the channel
        returned the same value less than that long ago. Notes `value` as
        returned otherwise."""
        max_unchanged = chan.get('DeviceSpecificParams', {}).get(
            'MaxUnchanged_s')
        if max_unchanged is None:
            return False
        last = self._last_returned.get(channel_id)
        if (last is not None and self._same(value, last[0])
                and now - last[1] < max_unchanged):
            return True
        self._last_returned[channel_id] = (value, now)
        return False

    def get_values(self):
        """Read the channels that are due."""
        chans = self.device['Channels']
        readings = {}
        self.reading_times_ns = {}
        outputs = None
        now = time.monotonic()
        for channel_id, chan in chans.items():
            last = self._last_read.get(channel_id)
            if last is not None and now - last < self._min_interval(chan):
                continue
            if chan['Type'] in self.OUTPUT_TYPES:
                if outputs is None:
                    outputs, times = self._read_outputs()
                name = chan['tags']['CTC100ChannelName']
                if self._key(name) not in outputs:
                    raise DeviceError(
                        f'No CTC100 channel \'{name}\' (channel'
                        f' \'{channel_id}\' of device \'{self.device["Device"]}\')')
                value = outputs[self._key(name)]
                self._last_read[channel_id] = now
                if self._unchanged(channel_id, chan, value, now):
                    continue
                self.reading_times_ns[channel_id] = times[self._key(name)]
            elif chan['Type'] == 'PIDSetpoint':
                value = self.read_pid_setpoint(chan["tags"]["CTC100ChannelName"])
            else:
                value = self.query_custom_command(chan["tags"]["CTC100CustomCommand"])
            readings[channel_id] = value
            self._last_read[channel_id] = now
        return readings
