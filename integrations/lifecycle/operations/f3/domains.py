"""Finite integer domains for untrusted nanosecond/epoch inputs; no coercion."""


class DomainError(ValueError): pass


def integer(value, label):
    if type(value) is not int or value < 0:
        raise DomainError(label + '_must_be_nonnegative_integer')
    return value


def clock_receipt(value):
    if not isinstance(value, dict) or not {'rt_ns', 'epoch'} <= value.keys():
        raise DomainError('clock_receipt_shape')
    result = {key: integer(value[key], key) for key in ('rt_ns', 'epoch')}
    if 'mono_ns' in value: result['mono_ns'] = integer(value['mono_ns'], 'mono_ns')
    return result
