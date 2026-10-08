# Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
"""Signed, single-slot local IPC using Desktop's workspace file APIs.

The directory is private local runtime data, not a network share. Each server
start has a fresh generation; requests expire and are never retransmitted.
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from .errors import BridgeUnavailable

MAX_PACKET = 32 * 1024 * 1024
# Claude Desktop's readFileAtCwd caps UTF-8 files at 10 MiB. Include the
# signed JSON envelope and base64 images so an oversized request never stalls
# the connector on an unreadable file.
MAX_REQUEST = 10 * 1024 * 1024
# Bump when the injected renderer must reload a changed API contract. A new
# owner generation alone only reconnects the existing JavaScript closure.
CONNECTOR_REVISION = 4


class FileDesktop:
    def __init__(self, directory, token):
        self.directory = Path(directory).resolve()
        self.token = token
        self.generation = secrets.token_hex(16)
        self.seq = 0
        self.lock = asyncio.Lock()
        self.cached = {}
        self.stamp = None
        self.message = ''
        self.write({'generation': self.generation, 'seq': 0, 'type': 'idle'})
        (self.directory / 'response.json').write_text('{}', encoding='utf-8')

    def pack(self, value):
        payload = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        signature = hmac.new(self.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return json.dumps({'payload': payload, 'signature': signature}, ensure_ascii=False)

    def write(self, value):
        packet = self.pack(value)
        if len(packet.encode('utf-8')) > MAX_REQUEST:
            raise ValueError('Claude 单次消息与图片编码后不能超过 10 MiB，请减少附件')
        target = self.directory / 'request.json'
        temp = self.directory / 'request.tmp'
        temp.write_text(packet, encoding='utf-8')
        temp.chmod(0o600)
        for attempt in range(40):
            try:
                temp.replace(target)
                return
            except PermissionError:
                # Windows can briefly deny rename while Electron reads the file.
                if attempt == 39:
                    raise
                time.sleep(.025)

    def read(self):
        path = self.directory / 'response.json'
        try:
            stat = path.stat()
            stamp = (stat.st_mtime_ns, stat.st_size)
            if stamp == self.stamp:
                return self.cached
            if stat.st_size > MAX_PACKET:
                return {}
            envelope = json.loads(path.read_text(encoding='utf-8'))
            payload, signature = envelope['payload'], envelope['signature']
            expected = hmac.new(self.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
                return {}
            value = json.loads(payload)
            if not isinstance(value, dict) or value.get('generation') != self.generation:
                return {}
            self.cached, self.stamp = value, stamp
            return value
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Desktop writes are not atomic; partial packets must never dispatch.
            return self.cached

    @property
    def connected(self):
        value = self.read()
        timestamp = value.get('timestamp', 0)
        return (isinstance(timestamp, (int, float)) and 0 <= time.time() - timestamp < 20
                and value.get('connected') is True and value.get('connectorRevision') == CONNECTOR_REVISION)

    @property
    def capabilities(self):
        return self.read().get('surfaces', {}) if self.connected else {}

    def disconnected(self, message):
        self.message = message

    async def call(self, surface, method, *args, timeout=30):
        deadline = time.monotonic() + timeout
        try:await asyncio.wait_for(self.lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError as exc:raise BridgeUnavailable("Claude 正忙，请稍后刷新选项") from exc
        try:
            if not self.connected or method not in self.capabilities.get(surface, []):
                raise BridgeUnavailable('Claude Desktop 文件桥接未连接或不支持此操作')
            self.seq += 1
            seq = self.seq
            try:
                await asyncio.to_thread(self.write,
                    {'type': 'request', 'generation': self.generation, 'seq': seq,
                     'expires': time.time() + timeout, 'surface': surface,
                     'method': method, 'args': list(args)})
            except OSError as exc:
                raise BridgeUnavailable('无法写入本地桥接文件，请检查文件占用和目录权限') from exc
            while time.monotonic() < deadline:
                reply = self.read()
                if reply.get('seq') == seq and reply.get('done') is True:
                    if 'error' in reply:
                        raise BridgeUnavailable(str(reply['error']))
                    return reply.get('result')
                if not self.connected:
                    raise BridgeUnavailable('Claude Desktop 文件桥接已断开；操作结果可能不确定')
                await asyncio.sleep(.2)
            raise BridgeUnavailable('Claude Desktop 响应超时；请检查原会话，不要重复提交')

        finally:self.lock.release()

    def close(self):
        self.write({'generation': self.generation, 'seq': self.seq + 1, 'type': 'stop'})
