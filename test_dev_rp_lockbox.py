# Copyright (c) 2018, Fabian Schmid, Edward Wang
# Copyright (c) 2023, Lothar Maisenbacher
#
# All rights reserved.
#
# Copyright (c) 2015, Red Pitaya
"""Module for controlling the Red Pitaya lockbox via SCPI commands."""

import logging

import dev_rp_lockbox

logger = logging.getLogger()

device = {
    'Address': '128.32.239.27',
    'Timeout': 1.,
    'SCPIConnectionParams': {
        'Port': 5000,
        },
    }
device_instance = dev_rp_lockbox.Device(device)
device_instance.connect()
