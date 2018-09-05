"""This module contains drivers for the following equipment from Pfeiffer Vacuum:

* TPG 366 MaxiGauge. 6-Channel Measurement and Control Unit for Compact Gauges
"""

import socket

PFEIFFER_PORT = 8000 # Ethernet port the device listens on

ETX = chr(3)  # \x03
CR = chr(13)
LF = chr(10)
ENQ = chr(5)  # \x05
ACK = chr(6)  # \x06
NAK = chr(21)  # \x15

# Code translations constants
MEASUREMENT_STATUS = {
    0: 'Measurement data okay',
    1: 'Underrange',
    2: 'Overrange',
    3: 'Sensor error',
    4: 'Sensor off (IKR, PKR, IMR, PBR)',
    5: 'No sensor (output: 5,2.0000E-2 [mbar])',
    6: 'Identification error'
}

PRESSURE_UNITS = {
    0: 'mbar/bar',
    1: 'Torr',
    2: 'Pascal',
    3: 'Micron',
    4: 'hPascal',
    5: 'Volt'
}

class TPG366():
    """Driver for the TPG 366 MaxiGauge 6-channel measurement and control unit."""

    delimiter = CR + LF
    chunksize = 4096

    def __init__(self, host, timeout=None):
        """Initialize the object and open a socket connection.
        
        :host: a string containing the hostname or IP address of the device.
        :timeout: the connection timeout in s or None to disable the timeout feature (default: None)
        :raises: socket.error if the connection to the device fails
        """

        self._socket = socket.socket()
        if timeout is not None:
            self._socket.settimeout(timeout)

        self._socket.connect((host, PFEIFFER_PORT))

        # By default the device floods the port with measurement data until the first command is
        # received. Reset clears the buffers.
        self._reset()

    def _cr_lf(self, string):
        """Pad carriage return and line feed to a string and encode to bytes

        :string: string to pad
        :returns: the padded bytes
        """
        return (string + CR + LF).encode()

    def _reset(self):
        """Reset the unit and empty the accumulated measurement data from the socket buffer."""
        self._socket.sendall(self._cr_lf('RES'))

        msg = ''
        while 1:
            chunk = self._socket.recv(self.chunksize).decode()
            msg += chunk
            if msg.endswith(ACK + self.delimiter):
                break

        self._socket.sendall(ENQ.encode())
        self._socket.recv(self.chunksize)

    def _send_command(self, command):
        """Send a command and check if it is positively acknowledged

        :command: The command to send
        :raises IOError: if the negative acknowledged or a unknown response is returned
        """
        self._socket.sendall(self._cr_lf(command))
        msg = ''
        while 1:
            chunk = self._socket.recv(self.chunksize).decode()
            msg += chunk
            if msg.endswith(self.delimiter):
                break

        response = msg.rstrip(self.delimiter)

        if response == NAK:
            message = 'Gauge returned negative acknowledge'
            raise IOError(message)
        elif response != ACK:
            message = 'Gauge returned unknown response:\n{}'.format(repr(response))
            raise IOError(message)

    def _get_data(self):
        """Get the data that is ready on the device.

        :returns: the raw data (without delimiter)
        """
        self._socket.sendall(ENQ.encode())

        msg = ''
        while 1:
            chunk = self._socket.recv(self.chunksize).decode()
            msg += chunk
            if msg.endswith(self.delimiter):
                break

        return msg.rstrip(self.delimiter)

    def pressure_gauge(self, gauge):
        """Return the pressure measured by gauge X.

        :gauge: The gauge number, 1 to 6
        :raises ValueError: if gauge is not 1 to 6
        :returns: (value, (status_code, status_message))
        """
        if gauge not in range(1, 7):
            message = 'The input gauge number can only be 1...6'
            raise ValueError(message)
        self._send_command('PR{}'.format(gauge))
        reply = self._get_data()
        status_code = int(reply.split(',')[0])
        value = float(reply.split(',')[1])
        return value, (status_code, MEASUREMENT_STATUS[status_code])

    def pressure_gauges(self):
        """Return the pressures measured by all gauges.

        :returns: list of 6 tuples (value, (status_code, status_message))
        """
        self._send_command('PRX')
        reply = self._get_data().split(',')
        return [(float(reply[i+1]), (int(reply[i]), MEASUREMENT_STATUS[int(reply[i])]))
                for i in range(0, 12, 2)]

    def pressure_unit(self):
        """Return the pressure unit.

        :returns: the pressure unit
        """
        self._send_command('UNI')
        unit_code = int(self._get_data())
        return PRESSURE_UNITS[unit_code]

    def get_value(self, channel):
        """Perform a pressure measurement of the specified channel and return the value.

        :param channel: the measurement channel (1 to 6)
        :return: the measured value in mBar
        """
        return self.pressure_gauge(channel)[0]

    def get_values(self):
        """Perform a pressure measurement of all 6 channels and return the measured values.

        :return: a list of the measured value in mBar
        """
        values_dict = {}
        for index, element in self.pressure_gauges():
            values_dict[index+1] = element[0]
        return values_dict
