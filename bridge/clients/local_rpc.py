"""Connect a gateway adapter to its selected desktop's private local service."""
import json
import os
from pathlib import Path
import socket
import stat
import struct


class LocalService:
    def __init__(self, profile, name):
        if not name.replace('-', '').isalpha():
            raise ValueError('本机服务名称无效')
        self.record = Path(profile)/(name+'-ipc.json')

    def _exchange(self, request, timeout=130):
        fd = os.open(self.record, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            meta = os.fstat(source.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o600:
                raise ValueError('本机服务记录权限无效')
            raw = source.read(4097)
        if len(raw)>4096:
            raise ValueError('本机服务记录无效')
        record = json.loads(raw)
        endpoint = Path(record['endpoint'])
        parent = endpoint.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('本机服务目录权限无效')
        payload = json.dumps({'token':record['token'],'request':request},ensure_ascii=False).encode()
        if len(payload)>64*1024*1024:
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
            if not 0<size<=64*1024*1024:raise ValueError('本机回执过大')
            result=json.loads(exact(size))
        if not result.get('ok'):
            raise ValueError(result.get('error','官方远控请求失败'))
        return result['value']

    def status(self):
        try:return self._exchange({'command':'status'},timeout=3)
        except (OSError,ValueError,EOFError):return {'connected':False,'configured':False,'reason':'请在网关中连接 ZCode 官方远控。'}

    def owner(self):
        return self.status().get('owner')

    def connect(self, *, wait=False):
        return self._exchange({'command':'connect', **({'waitForDevice': True} if wait else {})})

    def close(self):
        return self._exchange({'command':'disconnect'},timeout=10)

    def call(self, request):
        return self._exchange({'command':'call','request':request})
