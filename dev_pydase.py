# -*- coding: utf-8 -*-
"""
pydase apps/plug-ins.
"""

import logging
import pint
from amodevices.dev_generic import Device
from amodevices.dev_exceptions import DeviceError
from pydase import Client

from aiohttp.client_exceptions import InvalidUrlClientError
import socketio.exceptions

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
            client = Client(
                url=f'ws://{self.device["Address"]}:{self.device["Port"]}',
                block_until_connected=True,
                sio_client_kwargs={
                    'reconnection_attempts': 1,
                    })
        except socketio.exceptions.ConnectionError as e:
            msg = (
                'Encountered `socketio.exceptions.ConnectionError` error '
                +f'while connecting to server: {e}')
            raise DeviceError(
                f'Could not connect to pydase server of device \'{self.device["Device"]}\': {msg}')
        except InvalidUrlClientError as e:
            msg = (
                'Encountered `aiohttp.client_exceptions.InvalidUrlClientError` error '
                +f'while connecting to server: {e}')
            raise DeviceError(
                f'Could not connect to pydase server of device \'{self.device["Device"]}\': {msg}')
        self._client = client.proxy

    def get_values(self):
        """Read channels."""
        chans = self.device['Channels']
        readings = {}
        for channel_id, chan in chans.items():
            if chan['Type'] in ['pydaseAttribute']:
                server_structure = chan['pydaseServerStructure']
                if (subclass := server_structure.get('Subclass')) is not None:
                    object_ = getattr(self._client, subclass)
                    if (subclass_index := server_structure.get('SubclassIndex')) is not None:
                        object_ = object_[subclass_index]
                else:
                    object_ = self._client
                value_raw = getattr(object_, server_structure['Attribute'])
                numerical_value = value_raw.m if isinstance(value_raw, pint.Quantity) else value_raw
                readings[channel_id] = float(numerical_value)
            else:
                raise DeviceError(
                    f'Unknown channel type \'{chan["Type"]}\' for channel \'{channel_id}\''
                    +f' of device \'{self.device["Device"]}\'')
        return readings
