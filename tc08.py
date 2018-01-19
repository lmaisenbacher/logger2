
"""This module contains drivers for the pico TC-08 thermocouple data logger connected via USB.
"""

import math
import ctypes

DLLPATH = r"C:\Program Files\Pico Technology\SDK\lib\usbtc08.dll"
ERROR_CODES = {
    0: "USBTC08_ERROR_OK",
    1: "USBTC08_ERROR_OS_NOT_SUPPORTED",
    2: "USBTC08_ERROR_NO_CHANNELS_SET",
    3: "USBTC08_ERROR_INVALID_PARAMETER",
    4: "USBTC08_ERROR_VARIANT_NOT_SUPPORTED",
    5: "USBTC08_ERROR_INCORRECT_MODE",
    6: "USBTC08_ERROR_ENUMERATION_INCOMPLETE"
}

class TC08(object):
    """Class that represents one or more TC-08 thermocouple data loggers."""

    def __init__(self):
        """Load the pico DLL and initialize all available data loggers."""
        self.device_present = False
        try:
            self.lib = ctypes.WinDLL(DLLPATH)
        except OSError as err:
            print("Could not load pico TC-08 library: {}".format(err))
            return

        self.handles = []

        # Open all available devices
        handle = self.lib.usb_tc08_open_unit()
        while handle > 0:
            self.handles.append(handle)
            handle = self.lib.usb_tc08_open_unit()
        if not self.handles:
            print("Could not find any pico TC-08 thermocouple data loggers.")
            return
        else:
            self.device_present = True

    def shut_down(self):
        """Close all opened TC-08 units."""
        if not self.device_present:
            return
        self.device_present = False
        for handle in self.handles:
            retval = self.lib.usb_tc08_close_unit(handle)
            if retval is 0:
                self.print_last_error(handle)

    def set_mains(self, sixty_hertz):
        """Set up all connected devices to reject either 50 or 60 Hz mains frequency.

        :param sixty_hertz: 0 to reject 50 Hz, 1 to reject 60 Hz.
        """
        if not self.device_present:
            print("set_mains({}) called for non-present TC-08 thermocouple data logger.".format(
                sixty_hertz))
        for handle in self.handles:
            retval = self.lib.usb_tc08_set_mains(handle, sixty_hertz)
            if retval is 0:
                self.print_last_error(handle)

    def print_last_error(self, handle):
        """Print the last error returned by the specified device.

        :param handle: Which device to select.
        """
        if not self.device_present:
            return
        code = self.lib.usb_tc08_get_last_error(handle)
        print("pico TC-08 error: {}".format(ERROR_CODES[code]))

    def set_channel(self, handle, channel, tc_type):
        """Set up the specified channel for the specified device.

        :param handle: Specifies the TC-08 unit.
        :param channel: Specifies the channel to set up (0 to 8, 0 is the cold junction sensor)
        :param tc_type: Specifies which thermocouple is used (single char). Possible are
        'B', 'E', 'J', 'K', 'N', 'R', 'S', 'T'. Space disables the channel and 'X' reads voltages.
        """
        tc_type = ctypes.c_char(tc_type.encode())
        if not self.device_present:
            print("set_channel({}, {}, {}) called for non-present"
                  " TC-08 thermocouple data logger.".format(handle, channel, tc_type))
            return
        retval = self.lib.usb_tc08_set_channel(handle, channel, tc_type)
        if retval is 0:
            self.print_last_error(handle)

    def get_single(self, handle, unit):
        """Return a single reading of all channels. Channels have to be set up with set_channel().

        :param handle: Specifies the TC-08 unit.
        :param unit: Specifies the returned temperature unit. 0: degC, 1: degF, 2: K, 3: degRankine
        :returns: A list of tuples (channel, reading).
        """
        if not self.device_present:
            print("get_single({}, {}) called for non-present"
                  " TC-08 thermocouple data logger.".format(handle, unit))
            return
        temperatures = (ctypes.c_float*9)()
        overflow_flags = ctypes.c_int16()
        retval = self.lib.usb_tc08_get_single(
            handle, ctypes.byref(temperatures), ctypes.byref(overflow_flags), unit)
        if retval is 0:
            self.print_last_error(handle)
            return
        readings = []
        for channel, reading in enumerate(temperatures):
            if not math.isnan(reading):
                readings.append((channel, reading))
        return readings
