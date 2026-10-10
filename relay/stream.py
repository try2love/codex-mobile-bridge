"""Bounded, credited download frames shared by relay and connector."""
import re

CHUNK = 64 * 1024
WINDOW = 8
ID_LENGTH = 43


def frame(identifier, data):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', identifier) or not 0 < len(data) <= CHUNK:
        raise ValueError('Invalid download frame')
    return identifier.encode('ascii') + data


def unpack(data):
    if not ID_LENGTH < len(data) <= ID_LENGTH + CHUNK:
        raise ValueError('Invalid download frame size')
    identifier = data[:ID_LENGTH].decode('ascii')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', identifier):
        raise ValueError('Invalid download identity')
    return identifier, data[ID_LENGTH:]
