# -*- coding: utf-8 -*-
"""Per-channel readings and the InfluxDB fields they write.

A device module's `get_values()` returns one reading per channel ID. A
reading is either a scalar — the value of the channel's 'field-key' —
or a dict ``{field key: value}``: the channel's own field (always
present; None or NaN when the device gave no value) plus COMPANION
fields written on the same database row, under field keys the module
takes from the channel's configuration (`with_status` builds one). The
one companion defined so far is the STATUS text: a plain-word string
per reading ('ok' with a valid value, else the reason, e.g.
'overexposed' — the convention lives in `amodevices.status`, each
driver's code → word table beside its codes; a STRING fleet-wide,
since InfluxDB fixes a field's type at its first write),
written whether or not the value is. A module that reports one sets
`STATUS_CAPABLE = True` on its device class and then writes it for
EVERY channel by default, under the field key 'status'; the channel key
'status-field-key' renames the field, and a `null` value switches it
off for that channel (`status_field_key`). `check_status_config`
validates these keys before any device is touched — refused on models
that report no status, must be a plain field name, must differ from
the channel's own field key — so a misplaced key can never silently
write nothing or the wrong type.

'Multiplier' and 'Converter' apply to the channel's own field only,
and only to a numeric value. Missing values — None or a non-finite
float — are dropped field by field (InfluxDB has no representation for
them; influxdb-client would silently drop them); a reading with no
field left writes nothing.
"""

import numbers

import numpy as np

from defs import LoggerError

STATUS_FIELD_KEY = 'status-field-key'
DEFAULT_STATUS_FIELD_KEY = 'status'


def status_field_key(channel):
    """The field key a status-capable module writes `channel`'s status
    under: 'status' unless 'status-field-key' renames it; None when the
    channel sets it to `null` (no status field)."""
    return channel.get(STATUS_FIELD_KEY, DEFAULT_STATUS_FIELD_KEY)


def with_status(channel, value, status):
    """A status-capable module's reading for `channel`: `value` alone
    when the channel switched the status off, else the dict carrying
    `status` beside it (None status = nothing to say)."""
    key = status_field_key(channel)
    if key is None:
        return value
    return {channel['field-key']: value, key: status}


def convert_value(channel, value):
    """Apply the channel's 'Multiplier' and polynomial 'Converter'."""
    if 'Multiplier' in channel:
        value *= channel['Multiplier']
    if 'Converter' in channel and channel['Converter'].get('Type') == 'polynomial':
        coeffs_dict = channel['Converter'].get('Coefficients', {})
        coeffs = np.array(list(coeffs_dict.values()))
        exponents = np.array(list(coeffs_dict.keys())).astype(int)
        value = np.sum(coeffs*value**exponents)
    return value


def is_missing(value):
    """True for None or a non-finite float — values the database
    cannot hold."""
    if value is None:
        return True
    return isinstance(value, (float, np.floating)) and not np.isfinite(value)


def is_numeric(value):
    """True for a real number (Python or numpy), never for a bool."""
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def reading_fields(channel, reading):
    """The fields `reading` writes for `channel`, ``{field key: value}``;
    empty when nothing is writable. Raises `LoggerError` for a dict
    reading without the channel's own field (a device-module bug —
    companions never stand alone)."""
    own_key = channel['field-key']
    if isinstance(reading, dict):
        if own_key not in reading:
            raise LoggerError(
                f'Reading for field \'{own_key}\' lacks its own value'
                f' (device-module bug): {reading!r}')
        fields = dict(reading)
    else:
        fields = {own_key: reading}
    own = fields[own_key]
    if is_numeric(own) and not is_missing(own):
        fields[own_key] = convert_value(channel, own)
    return {key: value for key, value in fields.items() if not is_missing(value)}


def check_status_config(device, device_class):
    """Validate the 'status-field-key' entries of `device`'s channels
    against its module class. Raises `LoggerError` for the key on a
    model that reports no status (`STATUS_CAPABLE` unset — it would
    silently write nothing), for a blank or malformed key, and for a
    status key — explicit or the default — equal to the channel's own
    'field-key' (the text would overwrite the value)."""
    capable = getattr(device_class, 'STATUS_CAPABLE', False)
    for channel_id, chan in device['Channels'].items():
        where = f'Channel \'{channel_id}\' of device \'{device["Device"]}\''
        key = status_field_key(chan)
        if key is None:
            continue                     # switched off: fine on any model
        if STATUS_FIELD_KEY in chan and not capable:
            raise LoggerError(
                f'{where} has \'{STATUS_FIELD_KEY}\', but model'
                f' \'{device["Model"]}\' reports no status')
        if not capable:
            continue
        if (not isinstance(key, str) or not key or key != key.strip()
                or any(c in key for c in ' ,="\\')):
            raise LoggerError(
                f'{where}: invalid \'{STATUS_FIELD_KEY}\' {key!r} (a plain'
                ' field name, or null to switch the status off)')
        if key == chan.get('field-key'):
            raise LoggerError(
                f'{where}: the status field key {key!r} equals \'field-key\''
                ' — the status text would overwrite the value')


def channels_missing_status(device, readings, module):
    """Channel IDs of a status-capable `module` whose status is switched
    on but whose reading came back as a plain value: no status will be
    written — a module gap, or a channel to set to null."""
    if not getattr(module, 'STATUS_CAPABLE', False):
        return []
    return [channel_id for channel_id, chan in device['Channels'].items()
            if status_field_key(chan) is not None
            and not isinstance(readings.get(channel_id), dict)]
