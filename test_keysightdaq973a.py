# -*- coding: utf-8 -*-
"""
Created on Wed May 18 16:00:49 2022

@author: Unitrap
"""

import logging
import numpy as np

import keysightdaq973a

logging.basicConfig(level=logging.INFO)

device = {
    'Device': 'Keysight DAQ973A Multimeter 1',
    'Model': 'Keysight DAQ973A Multimeter',
    'Address': 'TCPIP0::K-DAQ973A-0296.local::inst0::INSTR',
    'Timeout': 60000,
    'VISAIDN': 'Keysight Technologies,DAQ973A,MY59000296,A.02.02-01.00-02.01-00.02-02.00-03-03',
    'Channels':
        {
            "V1":
            {   
                "DeviceChannel":102,
                "Type":"DCV",
                "Multiplier":100,
                "field-key":"value",
                "measurement":"voltage",
                "tags":
                {
                    "sensor":"V1",
                    "unit":"V"
                }
            },
            "V2":
            {   
                "DeviceChannel":101,
                "Type":"DCV",
                "Multiplier":100,
                "field-key":"value",
                "measurement":"voltage",
                "tags":
                {
                    "sensor":"V2",
                    "unit":"V"
                }
            }                
        }
    }

Device = keysightdaq973a.Device(device)

# print(Device.visa_query('READ?', return_ascii=True))
# 

chans_data = Device.read_channels()
print(chans_data)
