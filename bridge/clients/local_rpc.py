"""Connect a gateway adapter to its selected desktop's private local service."""
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import time

MAX_FRAME = 64 * 1024 * 1024


def packet(value):
    data = json.dumps(value, ensure_ascii=False).encode()
    if not 0 < len(data) <= MAX_FRAME:raise ValueError('本机请求过大')
    return struct.pack('!I', len(data)) + data


def unpack(data):
    if len(data) < 4:raise EOFError('本机服务连接已关闭')
    size = struct.unpack('!I', data[:4])[0]
    if not 0 < size <= MAX_FRAME or size != len(data) - 4:raise ValueError('本机回执过大或不完整')
    value = json.loads(data[4:])
    if not isinstance(value, dict):raise ValueError('本机回执格式无效')
    return value


def read_packet(read):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            part = read(min(65536, size - len(data)))
            if not part:raise EOFError('本机服务连接已关闭')
            data.extend(part)
        return data
    head = exact(4);size = struct.unpack('!I', head)[0]
    if not 0 < size <= MAX_FRAME:raise ValueError('本机回执过大')
    return unpack(head + exact(size))


def write_packet(write, value):
    data = packet(value);offset = 0
    while offset < len(data):
        count = write(data[offset:])
        if not count:raise EOFError('本机服务连接已关闭')
        offset += count


class LocalService:
    def __init__(self, profile, name):
        if not re.fullmatch('[a-z-]+', name):
            raise ValueError('本机服务名称无效')
        self.profile, self.name = Path(profile).resolve(), name
        self.record = self.profile/'.local-services'/name/'ipc.json' if os.name == 'nt' else self.profile/(name+'-ipc.json')

    def _exchange(self, request, timeout=130):
        if os.name == 'nt':
            from bridge.platforms.windows import local_service as native
            record = native.read_record(self.record)
            if (not isinstance(record, dict) or record.get('name') != self.name or os.path.normcase(record.get('profile', '')) != os.path.normcase(str(self.profile))
                    or not isinstance(record.get('token'), str) or not re.fullmatch('[0-9a-f]{64}', record['token'])
                    or not re.fullmatch(r'\\\\\.\\pipe\\cmb-rpc-[a-z-]+-[0-9a-f]{32}', record.get('endpoint', ''))):
                raise ValueError('本机服务记录与当前资料目录不匹配')
            with native.local_connect(record['endpoint'], timeout=min(timeout, 5)) as stream:
                native.require_peer(stream, server=False, expected=record['owner'])
                pipe = native.byte_stream(stream);deadline = time.monotonic() + timeout
                write_packet(lambda data: pipe.send(data, timeout=deadline-time.monotonic()), {'token': record['token'], 'request': request})
                result = read_packet(lambda size: pipe.recv(size, timeout=deadline-time.monotonic()))
            return self._result(result)
        fd = os.open(self.record, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            meta = os.fstat(source.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o600:
                raise ValueError('本机服务记录权限无效')
            raw = source.read(4097)
        if len(raw)>4096:
            raise ValueError('本机服务记录无效')
        record = json.loads(raw)
        if not isinstance(record, dict):raise ValueError('本机服务记录无效')
        endpoint = Path(record['endpoint'])
        parent = endpoint.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('本机服务目录权限无效')
        payload = json.dumps({'token':record['token'],'request':request},ensure_ascii=False).encode()
        if len(payload)>MAX_FRAME:
            raise ValueError('本机请求过大')
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
            stream.settimeout(timeout);stream.connect(str(endpoint))
            stream.sendall(struct.pack('!I',len(payload))+payload)
            def exact(count):
                data=bytearray()
                while len(data)<count:
                    block=stream.recv(min(65536,count-len(data)))
                    if not block:raise EOFError('本机服务连接已关闭')
                    data.extend(block)
                return data
            size=struct.unpack('!I',exact(4))[0]
            if not 0<size<=MAX_FRAME:raise ValueError('本机回执过大')
            result=json.loads(exact(size))
        return self._result(result)

    @staticmethod
    def _result(result):
        if not isinstance(result, dict):raise ValueError('本机回执格式无效')
        if not result.get('ok'):
            raise ValueError(result.get('error','官方远控请求失败'))
        return result['value']

    def status(self):
        try:return self._exchange({'command':'status'},timeout=3)
        except (OSError,ValueError,EOFError,KeyError,TypeError):return {'connected':False,'configured':False,'available':False,'reason':'请在网关中连接 ZCode 官方远控。'}

    def owner(self):
        return self.status().get('owner')

    def connect(self, *, wait=False):
        return self._exchange({'command':'connect', **({'waitForDevice': True} if wait else {})})

    def close(self):
        return self._exchange({'command':'disconnect'},timeout=10)

    def call(self, request):
        return self._exchange({'command':'call','request':request})
