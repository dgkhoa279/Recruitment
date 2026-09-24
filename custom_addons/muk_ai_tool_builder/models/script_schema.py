"""Small, explicit JSON Schema subset; unsupported constraints fail closed."""
import datetime
import math

from odoo import _
from odoo.exceptions import ValidationError


TYPES = {'object': dict, 'array': list, 'string': str, 'integer': int,
         'number': (int, float), 'boolean': bool, 'null': type(None)}
KEYS = {'type', 'title', 'description', 'default', 'enum', 'properties', 'required',
        'additionalProperties', 'items', 'minimum', 'maximum', 'minLength',
        'maxLength', 'minItems', 'maxItems', 'format'}


def validate_schema(schema, depth=0):
    if depth > 6 or not isinstance(schema, dict) or schema.get('type') not in TYPES:
        raise ValidationError(_('Every input schema needs a supported type (maximum depth 6).'))
    if set(schema) - KEYS:
        raise ValidationError(_('Unsupported input-schema constraints: %s') % ', '.join(sorted(set(schema) - KEYS)))
    applicable = {'type', 'title', 'description', 'default', 'enum'} | {
        'object': {'properties', 'required', 'additionalProperties'},
        'array': {'items', 'minItems', 'maxItems'},
        'string': {'minLength', 'maxLength', 'format'},
        'number': {'minimum', 'maximum'}, 'integer': {'minimum', 'maximum'},
        'boolean': set(), 'null': set(),
    }[schema['type']]
    if set(schema) - applicable:
        raise ValidationError(_('Schema constraints do not apply to this input type.'))
    for key in ('minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems'):
        if key in schema and (
            isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))
            or (isinstance(schema[key], float) and not math.isfinite(schema[key]))
        ):
            raise ValidationError(_('Invalid schema bound: %s') % key)
    for key in ('minLength', 'maxLength', 'minItems', 'maxItems'):
        if key in schema and (not isinstance(schema[key], int) or schema[key] < 0):
            raise ValidationError(_('Length bounds must be non-negative integers.'))
    for lower, upper in (('minimum', 'maximum'), ('minLength', 'maxLength'), ('minItems', 'maxItems')):
        if lower in schema and upper in schema and schema[lower] > schema[upper]:
            raise ValidationError(_('Schema minimum cannot exceed maximum.'))
    if schema.get('format') not in (None, 'date', 'date-time'):
        raise ValidationError(_('Only date and date-time formats are supported.'))
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ValidationError(_('enum must be a non-empty list.'))
    if schema['type'] == 'object':
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        if not isinstance(properties, dict) or len(properties) > 50 or not isinstance(required, list):
            raise ValidationError(_('Invalid object properties/required inputs.'))
        if any(not isinstance(key, str) for key in required) or set(required) - set(properties):
            raise ValidationError(_('Required inputs must exist in properties.'))
        if not isinstance(schema.get('additionalProperties', False), bool):
            raise ValidationError(_('additionalProperties must be a boolean.'))
        for item in properties.values():
            validate_schema(item, depth + 1)
    if schema['type'] == 'array':
        validate_schema(schema.get('items'), depth + 1)
    if 'default' in schema:
        validate_value(schema['default'], schema)
    return schema


def validate_value(value, schema, path='params'):
    kind = schema['type']
    if not isinstance(value, TYPES[kind]) or (
        kind in ('number', 'integer') and (isinstance(value, bool) or (isinstance(value, float) and not math.isfinite(value)))
    ):
        raise ValidationError(_('%s has an invalid type.') % path)
    if 'enum' in schema and not any(type(value) is type(item) and value == item for item in schema['enum']):
        raise ValidationError(_('%s is not an allowed choice.') % path)
    if kind == 'object':
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ValidationError(_('%s is missing required inputs.') % path)
        if not schema.get('additionalProperties', False) and set(value) - set(properties):
            raise ValidationError(_('%s contains unknown inputs.') % path)
        for key, item in value.items():
            if key in properties:
                validate_value(item, properties[key], '%s.%s' % (path, key))
    if kind == 'array':
        for index, item in enumerate(value):
            validate_value(item, schema['items'], '%s[%s]' % (path, index))
    if kind in ('string', 'array'):
        lower, upper = ('minLength', 'maxLength') if kind == 'string' else ('minItems', 'maxItems')
        if not schema.get(lower, 0) <= len(value) <= schema.get(upper, 20000 if kind == 'string' else 1000):
            raise ValidationError(_('%s has an invalid length.') % path)
    if kind in ('number', 'integer'):
        if value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            raise ValidationError(_('%s is outside the allowed range.') % path)
    if kind == 'string' and schema.get('format'):
        try:
            if schema['format'] == 'date':
                datetime.date.fromisoformat(value)
            else:
                datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValidationError(_('%s has an invalid date format.') % path) from error
    return value
