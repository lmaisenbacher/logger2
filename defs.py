# -*- coding: utf-8 -*-
"""
@author: Lothar Maisenbacher/MPQ
"""

class LoggerError(Exception):
    """Logger error."""
    def __init__(self, value, **kwds):
        super().__init__(**kwds)
        self.value = value
