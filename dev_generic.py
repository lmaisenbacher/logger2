# -*- coding: utf-8 -*-
"""
Created on Wed Mar  7 16:55:25 2018

@author: Lothar Maisenbacher/MPQ
"""

import pyvisa
import logging
import numpy as np

from defs import LoggerError

logger = logging.getLogger()

class Device:
    
    def __init__(self, device):
        """
        """
        self.device_present = False        
        self.device = device
        self.visa_warning = False        
        self.visa_resource = None

    def to_float(self, value):
        """Convert `value` to float."""
        try:
            value_ = float(value)
        except ValueError:
            raise LoggerError('Value \'{}\' is not of expected type \'float\'.'.format(value))
        return value_

    def to_int(self, value):
        """Convert `value` to int."""
        e ='Value \'{}\' is not of expected type \'int\'.'.format(value)
        try:
            value_float = float(value)
        except ValueError:
            raise LoggerError(e)
        if not value_float.is_integer():
            raise LoggerError(e)
        else:
            return int(value_float)

    def closeConn(self):

        None
        
    def init_visa(self):

        # Initialize PyVISA to talk to VISA devices
        visa_rm = pyvisa.ResourceManager()
        visa_rsrc_list = visa_rm.list_resources()

    #     # Check if devices defined in settings can be found, if yes open device connection
        logger.info(
            'Connecting to device \'%s\' with VISA resource name \'%s\'',
            self.device['Device'], self.device['Address'])
        if self.device['Address'] in visa_rsrc_list:
            logger.info(
                'A device with VISA resource name \'%s\' was found.'
                +' Trying to open connection and read instrument IDN...',
                self.device['Address'])
            try:
                self.visa_resource = visa_rm.open_resource(self.device['Address'])
                visa_rcvd_idn = self.visa_resource.query('*IDN?').rstrip()
            except:
                msg = 'VISA error: Could not connect to device!'
                logger.error(msg)
                raise LoggerError(msg)                
                
            else:
                if 'Timeout' in self.device:
                    self.visa_resource.timeout = self.device['Timeout']
                self.visa_dev_status = True
                if visa_rcvd_idn == self.device['VISAIDN']:
                    logger.info(
                        'Received instrument IDN (\'%s\') matches saved IDN!', visa_rcvd_idn
                        )
                else:
                    logger.warning(
                        'VISA warning: Received instrument IDN (\'%s\')'
                        +' DOES NOT matche saved IDN!',
                        visa_rcvd_idn)
                    self.visa_warning = True
                if self.device.get('CmdOnInit', None) is not None:
                    logger.info(
                        'Sending initialization command \'%s\' to VISA device \'%s\'',
                        self.device['CmdOnInit'], self.device['Device'])
                    self.visa_write(self.device['CmdOnInit'])
        else:
            msg = (
                f'VISA error: No device with VISA resource name \'{self.device["Address"]}\''
                +' found!')
            logger.error(msg)
            raise LoggerError(msg)

    def visa_write(self, cmd):
        """Write VISA command `cmd` (str)."""
        try:
            self.visa_resource.write(cmd)
            logger.debug('VISA write to device \'%s\': \'%s\'', self.device['Device'], cmd)
        except pyvisa.VisaIOError as e:
            msg = (
                'Error in VISA communication with device \'{}\' (VISA resource name {}): {}'
                .format(
                    self.device['Device'], self.device['Address'], e.description))
            logger.error(msg)
            raise LoggerError(msg)

    def visa_query(self, query, return_ascii=False):
        """
        Send VISA query `query` (str) and return response.
        """
        try:
            if return_ascii:
                response = self.visa_resource.query_ascii_values(query, container=np.array)
            else:
                response = self.visa_resource.query(query).rstrip()
            logger.debug('VISA query to device \'%s\': \'%s\'', self.device['Device'], query)
            logger.debug('VISA device \'%s\' response: \'%s\'', self.device['Device'], response)
            return response
        except pyvisa.VisaIOError as e:
            msg = (
                'Error in VISA communication with device \'{}\' (VISA resource name \'{}\'): {}'
                .format(
                    self.device['Device'], self.device['Address'], e.description))
            logger.error(msg)
            raise LoggerError(msg)
