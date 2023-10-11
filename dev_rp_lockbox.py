# Copyright (c) 2018, Fabian Schmid, Edward Wang
# Copyright (c) 2023, Lothar Maisenbacher
#
# All rights reserved.
#
# Copyright (c) 2015, Red Pitaya
"""Module for controlling the Red Pitaya lockbox via SCPI commands."""

import socket
import logging

from amodevices import dev_generic
from amodevices.dev_exceptions import DeviceError

logger = logging.getLogger()

class Device(dev_generic.Device):
    """Class that represents the Red Pitaya lockbox.

    Many functions take one or both of the following parameters:
    :num_in: the input channel to use (1 or 2)
    :num_out: the output channel to use (1 or 2)
    """
    delimiter = '\r\n'

    def connect(self):
        """Open a new TCP/IP socket and connect to the configured hostname and port."""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if (timeout := self.device.get('Timeout')) is not None:
            self._socket.settimeout(timeout)

        try:
            self._socket.connect((
                self.device['Address'], self.device['SCPIConnectionParams']['Port']))
        except socket.timeout as err:
            raise DeviceError(f'Failed to connect to socket. Error: {err}')

    def __del__(self):
        if self._socket is not None:
            self._socket.close()
        self._socket = None

    def close(self):
        """Close the TCP/IP connection."""
        self.__del__()

    def rx_txt(self, chunksize=4096):
        """Receive text string and return it after removing the delimiter.

        :chunksize: number of bytes to receive at once (default: 4096)
        """
        msg = ''
        while 1:
            chunk = self._socket.recv(chunksize + len(self.delimiter)).decode('utf-8')
            # Receive chunk size of 2^n preferably
            msg += chunk
            if chunk and chunk[-2:] == self.delimiter:
                break
        logger.debug("RX: %s", msg[:-2])
        return msg[:-2]

    def tx_txt(self, msg):
        """Send text string and append delimiter.

        :msg: text string to send
        """
        logger.debug("TX: %s", msg)
        try:
            self._socket.send((msg + self.delimiter).encode('utf-8'))
        except (OSError, socket.timeout) as err:
            logger.error("Failed to send message to socket. Error: %s", err)

    def txrx_txt(self, msg):
        """Send text string and return the response after removing the delimiter.

        :msg: text string to send
        """
        self.tx_txt(msg)
        return self.rx_txt()

    def set_output_state(self, num_out, state):
        """Disable or enable the signal generator output.

        :state: True to enable the signal generator output, False to disable it
        """
        self.tx_txt('OUTPUT{}:STATE {}'.format(num_out, int(state)))

    def get_output_state(self, num_out):
        """Return whether the signal generator output is enabled.

        :returns: True if the signal generator output is enabled, False otherwise
        """
        response = self.txrx_txt('OUTPUT{}:STATE?'.format(num_out))
        return bool(int(response))

    def set_generator_frequency(self, num_out, frequency):
        """Set the frequency of the signal generator.

        :frequency: the frequency to set in Hz
        """
        self.tx_txt('SOUR{}:FREQ:FIX {}'.format(num_out, frequency))

    def get_generator_frequency(self, num_out):
        """Return the frequency of the signal generator.

        :returns: the frequency in Hz
        """
        return float(self.txrx_txt('SOUR{}:FREQ:FIX?'.format(num_out)))

    def set_generator_waveform(self, num_out, waveform):
        """Set the waveform of the signal generator.

        :waveform: waveform to set (SINE, SQUARE, TRIANGLE, SAWU, SAWD, PWM, ARBITRARY)
        """
        self.tx_txt('SOUR{}:FUNC {}'.format(num_out, waveform))

    def get_generator_waveform(self, num_out):
        """Return the waveform of the signal generator.

        :returns: the waveform of the signal generator
        """
        return self.txrx_txt('SOUR{}:FUNC?'.format(num_out))

    def set_generator_amplitude(self, num_out, amplitude):
        """Set the amplitude of the signal generator.
        Amplitude + offset value must be less than the maximum output range of ± 1V.

        :amplitude: the amplitude to set in V
        """
        self.tx_txt('SOUR{}:VOLT {}'.format(num_out, amplitude))

    def get_generator_amplitude(self, num_out):
        """Return the amplitude of the signal generator.

        :returns: the amplitude in V
        """
        return float(self.txrx_txt('SOUR{}:VOLT?'.format(num_out)))

    def set_generator_offset(self, num_out, offset):
        """Set the offset voltage of the signal generator.
        Amplitude + offset value must be less than the maximum output range of ± 1V.

        :offset: the offset voltage to set in V
        """
        self.tx_txt('SOUR{}:VOLT:OFFS {}'.format(num_out, offset))

    def get_generator_offset(self, num_out):
        """Return the offset voltage of the signal generator.

        :returns: the offset voltage in V
        """
        return float(self.txrx_txt('SOUR{}:VOLT:OFFS?'.format(num_out)))

    def set_setpoint(self, num_in, num_out, value):
        """Set the PID setpoint.

        :value: the value to set in V
        """
        self.tx_txt('PID:IN{}:OUT{}:SETPoint {}'.format(num_in, num_out, value))

    def get_setpoint(self, num_in, num_out):
        """Return the PID setpoint.

        :returns: the setpoint in V
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:SETPoint?'.format(num_in, num_out)))

    def set_kg(self, num_in, num_out, gain):
        """Set the global gain.

        :gain: the gain to set (0 to 4096)
        """
        self.tx_txt('PID:IN{}:OUT{}:KG {}'.format(num_in, num_out, gain))

    def get_kg(self, num_in, num_out):
        """Return the global gain.

        :returns: the global gain
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:KG?'.format(num_in, num_out)))

    def set_kp(self, num_in, num_out, gain):
        """Set the P gain.

        :gain: the gain to set (0 to 4096)
        """
        self.tx_txt('PID:IN{}:OUT{}:KP {}'.format(num_in, num_out, gain))

    def get_kp(self, num_in, num_out):
        """Return the P gain.

        :returns: the P gain
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:KP?'.format(num_in, num_out)))

    def set_ki(self, num_in, num_out, gain):
        """Set the I gain.

        :gain: the gain to set in 1/s. The unity gain frequency is ki/(2 pi)."""
        self.tx_txt('PID:IN{}:OUT{}:KI {}'.format(num_in, num_out, gain))

    def get_ki(self, num_in, num_out):
        """Return the I gain.

        :returns: the I gain in 1/s. The unity gain frequency is ki/(2 pi)."""
        return float(self.txrx_txt('PID:IN{}:OUT{}:KI?'.format(num_in, num_out)))

    def set_kii(self, num_in, num_out, gain):
        """Set the II (second integrator) gain.

        :gain: the gain to set in 1/s. The corner frequency is kii/(2 pi)."""
        self.tx_txt('PID:IN{}:OUT{}:KII {}'.format(num_in, num_out, gain))

    def get_kii(self, num_in, num_out):
        """Return the II (second integrator) gain.

        :returns: the II gain in 1/s. The corner frequency is kii/(2 pi)."""
        return float(self.txrx_txt('PID:IN{}:OUT{}:KII?'.format(num_in, num_out)))

    def set_kd(self, num_in, num_out, gain):
        """Set the D gain.

        :gain: the gain to set in s. The unity gain frequency is 1/(2 pi kd).
        """
        self.tx_txt('PID:IN{}:OUT{}:KD {}'.format(num_in, num_out, gain))

    def get_kd(self, num_in, num_out):
        """Return the D gain

        :returns: the D gain in s. The unity gain frequency is 1/(2 pi kd).
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:KD?'.format(num_in, num_out)))

    def set_int_reset_state(self, num_in, num_out, state):
        """Reset the integrator register.

        :state: True to enable the integrator reset, False to disable the integrator reset
        """
        self.tx_txt('PID:IN{}:OUT{}:INT:RES {}'.format(num_in, num_out, int(state)))

    def get_int_reset_state(self, num_in, num_out):
        """Return whether the integrator reset is enabled or disabled

        :returns: True if the integrator reset is enabled, False if the integrator reset is disabled
        """
        response = self.txrx_txt('PID:IN{}:OUT{}:INT:RES?'.format(num_in, num_out))
        return response == "ON"

    def set_hold_state(self, num_in, num_out, state):
        """Hold the internal state of the PID.

        :state: True to enable the PID hold, False to disable the PID hold
        """
        self.tx_txt('PID:IN{}:OUT{}:HOLD {}'.format(num_in, num_out, int(state)))

    def get_hold_state(self, num_in, num_out):
        """Return whether the PID internal state hold is enabled or disabled

        :returns: True if the PID hold is enabled, False if the PID hold is disabled
        """
        response = self.txrx_txt('PID:IN{}:OUT{}:HOLD?'.format(num_in, num_out))
        return response == "ON"

    def set_int_auto_state(self, num_in, num_out, state):
        """If enabled, the integrator register is reset when the PID output hits the configured
        limit

        :state: True to enable the automatic integrator reset, False to disable the automatic
                integrator reset
        """
        self.tx_txt('PID:IN{}:OUT{}:INT:AUTO {}'.format(num_in, num_out, int(state)))

    def get_int_auto_state(self, num_in, num_out):
        """Return whether the automatic integrator reset is enabled or disabled

        :returns: True if the automatic integrator reset is enabled, False if the automatic
                  integrator reset is disabled
        """
        response = self.txrx_txt('PID:IN{}:OUT{}:INT:AUTO?'.format(num_in, num_out))
        return response == "ON"

    def set_inv_state(self, num_in, num_out, state):
        """Invert the sign of the PID output

        :state: True to enable the inversion, False to disable the inversion
        """
        self.tx_txt('PID:IN{}:OUT{}:INV {}'.format(num_in, num_out, int(state)))

    def get_inv_state(self, num_in, num_out):
        """Return whether the sign of the PID output is inverted or not

        :returns: True if the inversion is enabled, False if the inversion is disabled
        """
        response = self.txrx_txt('PID:IN{}:OUT{}:INV?'.format(num_in, num_out))
        return response == "ON"

    def set_relock_state(self, num_in, num_out, state):
        """Enable or disable the PID relock feature. If enabled, the input not used by the PID is
        monitored. If the value falls outside the configured minimum and maximum values, the
        integrator is frozen and the output is ramped with the specified slew rate in order to
        re-acquire the lock. Once the value is inside the bounds, the integrator is turned on
        again.

        :state: True to enable the relock feature, False to disable the relock feature
        """
        self.tx_txt('PID:IN{}:OUT{}:REL {}'.format(num_in, num_out, int(state)))

    def get_relock_state(self, num_in, num_out):
        """Return whether the PID relock feature is enabled or disabled

        :returns: True if the relock feature is enabled, False if the relock feature is disabled
        """
        response = self.txrx_txt('PID:IN{}:OUT{}:REL?'.format(num_in, num_out))
        return response == "ON"

    def set_relock_stepsize(self, num_in, num_out, stepsize):
        """Set the step size (slew rate) of the relock

        :stepsize: the stepsize to set in V/s
        """
        self.tx_txt('PID:IN{}:OUT{}:REL:STEP {}'.format(num_in, num_out, stepsize))

    def get_relock_stepsize(self, num_in, num_out):
        """Return the step size (slew rate) of the relock

        :returns: the stepsize in V/s
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:REL:STEP?'.format(num_in, num_out)))

    def set_relock_minimum(self, num_in, num_out, minimum):
        """Set the minimum input voltage for which the PID is considered locked

        :minimum: the minimum input voltage to set
        """
        self.tx_txt('PID:IN{}:OUT{}:REL:MIN {}'.format(num_in, num_out, minimum))

    def get_relock_minimum(self, num_in, num_out):
        """Return the minimum input voltage for which the PID is considered locked

        :returns: the minimum input voltage
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:REL:MIN?'.format(num_in, num_out)))

    def set_relock_maximum(self, num_in, num_out, maximum):
        """Set the maximum input voltage for which the PID is considered locked

        :maximum: the maximum input voltage to set
        """
        self.tx_txt('PID:IN{}:OUT{}:REL:MAX {}'.format(num_in, num_out, maximum))

    def get_relock_maximum(self, num_in, num_out):
        """Return the maximum input voltage for which the PID is considered locked

        :returns: the maximum input voltage
        """
        return float(self.txrx_txt('PID:IN{}:OUT{}:REL:MAX?'.format(num_in, num_out)))

    def set_relock_input(self, num_in, num_out, relock_input):
        """Set the XADC input to be used for relocking the specified PID

        :relock_input: the XADC index (0-3)
        """
        self.tx_txt("PID:IN{}:OUT{}:REL:INP AIN{}".format(num_in, num_out, relock_input))

    def get_relock_input(self, num_in, num_out):
        """Return which XADC input is used for relocking the specified PID

        :returns: the XADC index (0-3)
        """
        response = self.txrx_txt('PID:IN{}:OUT{}:REL:INP?'.format(num_in, num_out))
        return int(response[-1]) # response format: AIN[0-3]

    def set_output_minimum(self, num_out, minimum):
        """Set the minimum output voltage for the specified channel.

        :num_out: the output channel (1 or 2)
        :minimum: the minimum voltage in V
        """
        self.tx_txt("OUT{}:LIM:MIN {}".format(num_out, minimum))

    def get_output_minimum(self, num_out):
        """Get the minimum output voltage for the specified channel.

        :num_out: the output channel (1 or 2)
        :returns: the minimum output voltage
        """
        return float(self.txrx_txt("OUT{}:LIM:MIN?".format(num_out)))

    def set_output_maximum(self, num_out, maximum):
        """Set the maximum output voltage for the specified channel.

        :num_out: the output channel (1 or 2)
        :maximum: the maximum voltage in V
        """
        self.tx_txt("OUT{}:LIM:MAX {}".format(num_out, maximum))

    def get_output_maximum(self, num_out):
        """Get the maximum output voltage for the specified channel.

        :num_out: the output channel (1 or 2)
        :returns: the maximum output voltage
        """
        return float(self.txrx_txt("OUT{}:LIM:MAX?".format(num_out)))

    def save_lockbox_config(self):
        """Save the lockbox configuration to the SD-card."""
        self.tx_txt("LOCK:CONF:SAVE")

    def load_lockbox_config(self):
        """Load the lockbox configuration from the SD-card."""
        self.tx_txt("LOCK:CONF:LOAD")

    def get_fast_analog_input(self, num_out):
        """Return the fast analog input voltage (in V)."""
        return float(self.txrx_txt(f'ANALOG:IN{num_out:d}:VOLT?'))

    def get_fast_analog_output(self, num_out):
        """Return the fast analog output voltage (in V)."""
        return float(self.txrx_txt(f'ANALOG:OUT{num_out:d}:VOLT?'))

    def get_device_channel(self, channel_id, chan):
        """Get device channel from channel definition `chan` for channel with ID `channel_id`"""
        device_channel = chan.get('DeviceChannel')
        if device_channel is None:
            raise DeviceError(
                'Could not get required propertry \'DeviceChannel\' for channel \'%s\'', channel_id)
        return device_channel

    def get_pid_channels(self, channel_id, chan):
        """
        Get PID channels (input 1 or 2, output 1 or 2) from string `pid` (e.g., '12' for input 1
        and output 2), which is stored in channel definition `chan['PID']` for channel with ID
        `channel_id`.
        """
        pid = chan.get('PID')
        if pid is None:
            raise DeviceError(
                f'Could not get required propertry \'PID\' for channel \'{channel_id}\'')
        try:
            pid_channels = [int(pid[0]), int(pid[1])]
        except ValueError:
            raise DeviceError(
                f'Invalid PID controller \'{pid}\' defined for channel \'{channel_id}\''
                +' (in field \'PID\'; valid values: \'11\', \'12\', \'21\', \'22\')')
        return pid_channels

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] == 'FastAnalogIn':
                device_channel = self.get_device_channel(channel_id, chan)
                value = self.get_fast_analog_input(device_channel)
                readings[channel_id] = value
            elif chan['Type'] == 'FastAnalogOut':
                device_channel = self.get_device_channel(channel_id, chan)
                value = self.get_fast_analog_output(device_channel)
                readings[channel_id] = value
            elif chan['Type'] == 'GlobalGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kg(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'PGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kp(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'IGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_ki(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'IIGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kii(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            elif chan['Type'] == 'DGain':
                pid_channels = self.get_pid_channels(channel_id, chan)
                value = self.get_kd(pid_channels[0], pid_channels[1])
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
