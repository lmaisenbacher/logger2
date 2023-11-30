# -*- coding: utf-8 -*-
"""
pydase apps/plug-ins.
"""

import logging

from amodevices.dev_generic import Device
from amodevices.dev_exceptions import DeviceError

import rpyc

logger = logging.getLogger()

class Device(Device):

    def __init__(self, device):
        """
        Initialize device.

        device : dict
            Configuration dict of the device to initialize.
        """
        super(Device, self).__init__(device)
        self._client = None

    def connect(self):
        """Open connection to server."""
        try:
            conn = rpyc.connect(
                self.device['Address'], self.device['Port'],
                timeout=self.device.get('Timeout', 10))
            self._client = conn.root
        except ConnectionError as e:
            raise DeviceError(
                f'Could not connect to Slapdash server of device \'{self.device["Device"]}\': {e}')

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['SlapdashAttribute']:
                server_structure = chan['SlapdashServerStructure']
                if (subclass := server_structure.get('Subclass')) is not None:
                    object_ = getattr(self._client, subclass)
                    if (subclass_index := server_structure.get('SubclassIndex')) is not None:
                        object_ = object_[subclass_index]
                else:
                    object_ = self._client
                value = getattr(object_, server_structure['Attribute'])
                readings[channel_id] = value
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
