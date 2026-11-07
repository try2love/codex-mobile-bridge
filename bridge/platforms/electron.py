"""Read metadata from the installed Electron application without executing its code."""
import json
import struct
from pathlib import Path


def package_manifest(archive):
    # Read one public package file, without unpacking application code or credentials.
    with Path(archive).open('rb') as stream:
        header = stream.read(16)
        if len(header) != 16:
            raise ValueError('桌面程序元数据不完整')
        _, header_size, _, json_size = struct.unpack('<4I', header)
        if not 0 < json_size <= 16*1024*1024 or header_size < json_size:
            raise ValueError('桌面程序元数据不完整')
        index = json.loads(stream.read(json_size))['files']['package.json']
        if index.get('unpacked') or not 0 < index['size'] < 1024*1024:
            raise ValueError('桌面程序元数据不完整')
        stream.seek(8+header_size+int(index['offset']))
        return json.loads(stream.read(index['size']))
