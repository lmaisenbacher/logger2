# -*- coding: utf-8 -*-
"""
This module contains a driver for PurpleAir air quality sensor/particle counters,
with measurements read from the web API at `https://api.purpleair.com/`.

Currently, each sensor - here defined as a channel - is read out with a separate API request.
In the future, the group feature of the API should be used to read out multiple sensors.

The access the API, an API key is required, which at the time of writing needed to be requested 
from `contact@purpleair.com`.
"""

import logging
import numpy as np
import requests

import dev_generic

from defs import LoggerError

logger = logging.getLogger()

class Device(dev_generic.Device):
       
    def __init__(self, device):
        """
        Initialize device.
        
        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)

    def get_values(self):
        """Read channels."""
        readings = {}
        for channel_id, channel in self.device['Channels'].items():
            
            sensor_index = channel['PurpleAirSensorIndex']
            url = f'https://api.purpleair.com/v1/sensors/{sensor_index}'
            headers = {'X-API-Key': self.device['PurpleAirAPIKey']}
            params = {}
            # Some sensors are not public and a sensor-specific read key is required
            read_key = channel.get('PurpleAirReadKey')
            if read_key is not None:
                params['read_key'] = read_key
            r = requests.get(url, headers=headers, params=params)
            if not r.ok:
                msg = (
                    'Could not complete HTTP Get request for PurpleAir API'
                    +f' ({r.status_code}: {r.reason})')
                raise LoggerError(msg)
            r.request.headers
            data = r.json()

            # Return concentration of particles (particles/dl) greater than 0.3 μm in size
            # (this is the smallest particle size recorded; there are also entries for particles
            # greater than 0.5 μm, ..., but these counts are included here, since this is for
            # 0.3 μm *and greater*; see also PurpleAir API documentation)
            readings[channel_id] = float(np.sum([
                data['sensor'][f'{size:.1f}_um_count'] for size in [0.3]]))
        
        return readings
