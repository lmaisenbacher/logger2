# Copyright (c) 2018, Fabian Schmid, Edward Wang
# Copyright (c) 2023-2026, Lothar Maisenbacher
#
# All rights reserved.
#
# Copyright (c) 2015, Red Pitaya
"""Module for controlling the Red Pitaya lockbox via SCPI commands."""

import logging

import dev_rp_lockbox

logger = logging.getLogger()

device = {
    'Device': 'Red Pitaya lockbox',
    # The lockbox's address (a placeholder: set it before running)
    'Address': '192.0.2.10',
    'Timeout': 1.,
    'SCPIConnectionParams': {
        'Port': 5000,
        },
    }
device_instance = dev_rp_lockbox.Device(device)
device_instance.connect()
