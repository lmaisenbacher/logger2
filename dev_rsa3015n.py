# -*- coding: utf-8 -*-
"""
This module contains drivers for the Rigol RSA3015N via VISA.
"""
import logging
import numpy as np

from amodevices import dev_generic
from amodevices.dev_exceptions import DeviceError

import pyvisa

rm = pyvisa.ResourceManager()
rm.list_resources()
# ('ASRL1::INSTR', 'ASRL2::INSTR', 'GPIB0::12::INSTR')
inst = rm.open_resource('GPIB0::12::INSTR')
print(inst.query("*IDN?"))