# -*- coding: utf-8 -*-
"""Tests for `readings` — the reading → fields contract every device
module's `get_values()` feeds into. Runs under pytest or as a script.
"""

import math

import numpy as np
import pytest

from defs import LoggerError
from readings import (
    channels_missing_status,
    check_status_config,
    reading_fields,
    status_field_key,
    with_status,
)

CHANNEL = {'Type': 'Frequency', 'field-key': 'frequency'}


class Capable:
    STATUS_CAPABLE = True


class Plain:
    pass


def _device(**channels):
    return {'Device': 'D', 'Model': 'M', 'Channels': channels}


def _raises(device, cls, text):
    # LoggerError carries its message in `.value`
    with pytest.raises(LoggerError) as info:
        check_status_config(device, cls)
    assert text in info.value.value


def test_status_field_key_default_rename_opt_out():
    assert status_field_key(CHANNEL) == 'status'
    assert status_field_key({**CHANNEL, 'status-field-key': 'wm_status'}) == 'wm_status'
    assert status_field_key({**CHANNEL, 'status-field-key': None}) is None


def test_with_status():
    assert with_status(CHANNEL, 1.0, 'ok') == {'frequency': 1.0, 'status': 'ok'}
    assert with_status({**CHANNEL, 'status-field-key': None}, 1.0, 'ok') == 1.0


def test_scalar_reading():
    assert reading_fields(CHANNEL, 387.0) == {'frequency': 387.0}
    assert reading_fields(CHANNEL, np.float32(2.0)) == {'frequency': np.float32(2.0)}
    assert reading_fields(CHANNEL, math.nan) == {}
    assert reading_fields(CHANNEL, np.float64('inf')) == {}
    assert reading_fields(CHANNEL, None) == {}


def test_dict_reading_drops_missing_fields_one_by_one():
    assert reading_fields(CHANNEL, {'frequency': 387.0, 'status': 'ok'}) == {
        'frequency': 387.0, 'status': 'ok'}
    assert reading_fields(CHANNEL, {'frequency': math.nan,
                                    'status': 'overexposed'}) == {
        'status': 'overexposed'}
    assert reading_fields(CHANNEL, {'frequency': math.nan,
                                    'status': None}) == {}
    # Companions never stand alone: the own field must be present
    with pytest.raises(LoggerError):
        reading_fields(CHANNEL, {'status': 'ok'})


def test_transforms_apply_to_a_numeric_own_field_only():
    channel = {**CHANNEL, 'Multiplier': 1e3}
    assert reading_fields(channel, {'frequency': 387.0, 'status': 'ok'}) == {
        'frequency': 387000.0, 'status': 'ok'}
    assert reading_fields(channel, np.int64(2)) == {'frequency': 2000.0}
    channel = {**CHANNEL, 'Converter': {
        'Type': 'polynomial', 'Coefficients': {'0': 1.0, '1': 2.0}}}
    assert reading_fields(channel, 3.0) == {'frequency': 7.0}
    # A missing, string, or bool own value is never transformed
    channel = {**CHANNEL, 'Multiplier': 2.0}
    assert reading_fields(channel, {'frequency': None, 'status': 'no_signal'}) == {
        'status': 'no_signal'}
    assert reading_fields(channel, 'locked') == {'frequency': 'locked'}
    assert reading_fields(channel, True) == {'frequency': True}


def test_status_config_gate():
    # Capable model: default key fine, rename fine, null fine
    check_status_config(_device(F=dict(CHANNEL)), Capable)
    check_status_config(_device(F={**CHANNEL, 'status-field-key': 'wm_status'}), Capable)
    check_status_config(_device(F={**CHANNEL, 'status-field-key': None}), Capable)
    # Plain model: no key or null is fine, an explicit key is refused
    check_status_config(_device(F=dict(CHANNEL)), Plain)
    check_status_config(_device(F={**CHANNEL, 'status-field-key': None}), Plain)
    _raises(_device(F={**CHANNEL, 'status-field-key': 'status'}), Plain,
            'reports no status')
    # Malformed keys (blank is not an opt-out) and the value collision
    for bad in ('', ' ', 'st atus', 'a,b', 'a=b', 3):
        _raises(_device(F={**CHANNEL, 'status-field-key': bad}), Capable,
                'invalid')
    _raises(_device(F={**CHANNEL, 'status-field-key': 'frequency'}), Capable,
            'overwrite')
    _raises(_device(F={**CHANNEL, 'field-key': 'status'}), Capable,
            'overwrite')                       # the DEFAULT collides too
    check_status_config(_device(F={**CHANNEL, 'field-key': 'status',
                                   'status-field-key': None}), Capable)


def test_channels_missing_status():
    device = _device(F=dict(CHANNEL), G={**CHANNEL, 'status-field-key': None},
                     H=dict(CHANNEL))
    readings = {'F': {'frequency': 1.0, 'status': 'ok'}, 'G': 2.0, 'H': 3.0}
    assert channels_missing_status(device, readings, Capable()) == ['H']
    assert channels_missing_status(device, readings, Plain()) == []


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q']))
