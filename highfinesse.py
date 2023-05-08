""" This module contains drivers for the HighFinesse WS Ultimate 2 MC wavemeter."""
import logging
import ctypes

LOG = logging.getLogger(__name__)

# Constants for the wavelength meter library
# pylint: disable=invalid-name
cInstResetCalc = 0
cCtrlWLMShow = 1
cMax1 = 2
cMax2 = 3
cmiDeviationUnit = 1041
cReturnWavelengthVac = 0
cReturnFrequency = 2
cReturnWavenumber = 3
# pylint: enable=invalid-name

SET_ERRORS = {
    0: "ResERR_NoErr",
    -1: "ResERR_WlmMissing",
    -2: "ResERR_CouldNotSet",
    -3: "ResERR_ParmOutOfRange",
    -4: "ResERR_WlmOutOfResources",
    -5: "ResERR_WlmInternalError",
    -6: "ResERR_NotAvailable",
    -7: "ResERR_WlmBusy",
    -8: "ResERR_NotInMeasurementMode",
    -9: "ResERR_OnlyInMeasurementMode",
    -10: "ResERR_ChannelNotAvailable",
    -11: "ResERR_ChannelTemporarilyNotAvailable",
    -12: "ResERR_CalOptionNotAvailable",
    -13: "ResERR_CalWavelengthOutOfRange",
    -14: "ResERR_BadCalibrationSignal",
    -15: "ResERR_UnitNotAvailable",
    -16: "ResERR_FileNotFound",
    -17: "ResERR_FileCreation",
    -18: "ResERR_TriggerPending",
    -19: "ResERR_TriggerWaiting",
    -20: "ResERR_NoLegitimation"
}

class Wavemeter():
    """Class for the wavemeter. Emits newExposures, newLevels, newWavenumber and
    newDeviationSignal Qt signals."""

    def __init__(self, library_path=r"C:\Windows\System32\wlmData.dll"):
        """Load the wavelength meter library and connect to the device software.

        :library_path: path to the wavelength meter library
        """
        super().__init__()
        self.device_present = False

        try:
            self.wlm_lib = ctypes.WinDLL(library_path)
        except OSError as err:
            LOG.error("Could not open HighFinesse Wavelength Meter library. Error: %s", str(err))
        else:
            self.wlm_lib.GetFrequencyNum.restype = ctypes.c_double
            self.wlm_lib.GetWavelengthNum.restype = ctypes.c_double
            self.wlm_lib.GetDeviationSignal.restype = ctypes.c_double
            self.wlm_lib.GetDeviationReference.restype = ctypes.c_double

            retval = self.wlm_lib.Instantiate(cInstResetCalc, 0, 0, 0)
            if retval == 0:
                LOG.error("Could not instantiate wavelength meter.")
                return

            self.device_present = True

            # Set the wavelength meter to report frequencies
            retval = self.wlm_lib.SetPIDSetting(
                cmiDeviationUnit, 1, cReturnFrequency, ctypes.c_double(0))

    def set_pid_setpoint(self, setpoint):
        """Set the PID setpoint.

        :setpoint: the setpoint in THz
        """
        if not self.device_present:
            LOG.warning("set_pid_setpoint(%f) called for non-present HighFinesse wavemeter.",
                        setpoint)
            return
        retval = self.wlm_lib.SetPIDCourseNum(1, str(setpoint).encode())
        if retval != 0:
            LOG.error("Coult not set PID setpoint. Error: %s", SET_ERRORS[retval])

    def get_pid_setpoint(self):
        """Return the current PID setpoint.

        :returns: the current setpoint in THz or -1 of the device is not present
        """
        if not self.device_present:
            LOG.warning("get_pid_setpoint() called for non-present HighFinesse wavemeter.")
            return -1
        strbuf = ctypes.create_string_buffer(1024)
        self.wlm_lib.GetPIDCourseNum(1, ctypes.byref(strbuf))
        return float(strbuf.value.lstrip(b"= ").replace(b",", b"."))

    def set_pid_status(self, status):
        """Enable or disable the PID.

        :status: True to enable the PID, False to disable it
        """
        if not self.device_present:
            LOG.warning("set_pid_status(%s) called for non-present HighFinesse wavemeter.", status)
            return

        retval = self.wlm_lib.SetDeviationMode(ctypes.c_bool(status))
        if retval != 0:
            LOG.error("Coult not set PID status. Error: %s", SET_ERRORS[retval])

    def set_automatic_exposure(self, status):
        """Enable or disable automatic exposure.

        :status: True to enable automatic exposure, False to disable it
        """
        if not self.device_present:
            LOG.warning("set_automatic_exposure(%s) called for non-present HighFinesse wavemeter.",
                        status)
            return
        retval = self.wlm_lib.SetExposureMode(status)
        if retval != 0:
            LOG.error("Coult not set automatic exposure. Error: %s", SET_ERRORS[retval])

    def set_exposure_1(self, exposure):
        """Set the exposure of the first sensor.

        :exposure: the exposure to set in ms
        """
        if not self.device_present:
            LOG.warning("set_exposure_1(%d) called for non-present HighFinesse wavemeter.",
                        exposure)
            return
        retval = self.wlm_lib.SetExposureNum(1, 1, exposure)
        if retval != 0:
            LOG.error("Coult not set first sensor exposure. Error: %s", SET_ERRORS[retval])

    def set_exposure_2(self, exposure):
        """Set the exposure of the second sensor.

        :exposure: the exposure to set in ms
        """
        if not self.device_present:
            LOG.warning("set_exposure_2(%d) called for non-present HighFinesse wavemeter.",
                        exposure)
            return
        retval = self.wlm_lib.SetExposureNum(1, 2, exposure)
        if retval != 0:
            LOG.error("Coult not set second sensor exposure. Error: %s", SET_ERRORS[retval])

    def get_pid_output_voltage(self):
        """Return the current PID output voltage.

        :returns: the PID output voltage in mV or -1.0 if the device is not present.
        """
        if not self.device_present:
            LOG.warning("update_pid_output_voltage() called for non-present HighFinesse wavemeter.")
            return -1.0
        return self.wlm_lib.GetDeviationSignal(ctypes.c_double(0))

    def get_exposures(self):
        """Return the current exposure times.

        :returns: a tuple containing the exposure times of the two sensors in ms or (-1, -1) if the
                  device is not present.
        """
        if not self.device_present:
            LOG.warning("update_exposures() called for non-present HighFinesse wavemeter.")
            return (-1, -1)
        return self.wlm_lib.GetExposureNum(1, 1, 0), self.wlm_lib.GetExposureNum(1, 2, 0)

    def get_levels(self):
        """Return the current sensor levels.

        :returns: a tuple containing the levels of the two sensors or (-1.0, -1.0) if the device is
                  not present"""
        if not self.device_present:
            LOG.warning("update_levels() called for non-present HighFinesse wavemeter.")
            return (-1.0, -1.0)
        return self.wlm_lib.GetAmplitudeNum(1, cMax1, 0), self.wlm_lib.GetAmplitudeNum(1, cMax2, 0)

    def get_frequency(self):
        """Return the current laser frequency.

        :returns: the current frequency in THz or -1 if the device is not present
        """
        if not self.device_present:
            LOG.warning("get_frequency() called for non-present HighFinesse wavemeter.")
            return -1
        return self.wlm_lib.GetFrequencyNum(1, ctypes.c_double(0))

    def get_automatic_exposure(self):
        """Return if automatic exposure is enabled.

        :returns: True if automatic exposure is enabled, False if it is disabled or the device is
                  not present
        """
        if not self.device_present:
            LOG.warning("get_automatic_exposure() called for non-present HighFinesse wavemeter.")
            return False
        return self.wlm_lib.GetExposureMode(ctypes.c_bool(False))

    def get_pid_enabled(self):
        """"Return if the PID controller is enabled.

        :returns: True if the PID is enabled, False if it is disabled or the device is not present
        """
        if not self.device_present:
            LOG.warning("get_pid_enabled() called for non-present HighFinesse wavemeter.")
            return False
        return self.wlm_lib.GetDeviationMode(ctypes.c_bool(False))
