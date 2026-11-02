# Adapted from 2389859005/coding-mobile (MIT), commit 7a8f003. See LICENSE.coding-mobile.
"""Signed, single-slot local IPC using Desktop's workspace file APIs.

The directory is private local runtime data, not a network share. Each server
start has a fresh generation; requests expire and are never retransmitted.
"""
import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from bridge.clients.claude.staged_reply import StagedReply
from bridge.clients.errors import BridgeUnavailable

MAX_PACKET = 32 * 1024 * 1024
# Claude Desktop's readFileAtCwd caps UTF-8 files at 10 MiB. Include the
# signed JSON envelope and base64 images so an oversized request never stalls
# the connector on an unreadable file.
MAX_REQUEST = 10 * 1024 * 1024
# Bump when the injected renderer must reload a changed API contract. A new
# owner generation alone only reconnects the existing JavaScript closure.
CONNECTOR_REVISION = 15
MUTATIONS = {'sendMessage', 'interrupt', 'stop', 'respondToToolPermission', 'start', 'archive', 'unarchive', 'mobileArchive',
             'delete', 'updateSession', 'setPermissionMode', 'setModel', 'setEffort', 'setThinkingSummariesWanted', 'mobileFork', 'mobileRename'}


def workspace_trust_rejection(message):
    """Recognize only the verified native pre-start refusal, including IPC wrappers."""
    return isinstance(message, str) and re.fullmatch(
        r'(?:(?:Error invoking [^\n]+|Error|WorkspaceTrustError): )*'
        r'Workspace requires trust approval before starting a session\.'
        r'(?: \[ccdErrorCategory:trust_required\])?', message) is not None


class FileDesktop:
    def _known_start_rejection(self, reply):
        request = self._request_packet()
        return (request.get('method') == 'start' and request.get('surface') == 'code'
                and request.get('seq') == reply.get('seq') and reply.get('done') is True
                and workspace_trust_rejection(reply.get('error')))

    def __init__(self, directory, token, profile=None):
        self.directory = Path(directory).resolve()
        self.staged_reply = None
        self.token = token
        self.set_profile(profile)
        self.generation = secrets.token_hex(16)
        self.seq = 0
        self.lock = asyncio.Lock()
        self.cached = {}
        self.stamp = None
        self.message = ''
        self.unconfirmed_mutations = set()
        self.stopped = False
        self.resume_after = 0
        self.write({'generation': self.generation, 'seq': 0, 'type': 'idle'})
        (self.directory / 'response.json').write_text('{}', encoding='utf-8')
        try:
            self.monitor = asyncio.get_running_loop().create_task(self._watch_handoff())
        except RuntimeError:
            self.monitor = None

    def set_profile(self, profile):
        if not profile:
            self.staged_reply = None
        elif not self.staged_reply or self.staged_reply.root != Path(profile) / 'pending-uploads':
            self.staged_reply = StagedReply(profile, self.token)

    async def _watch_handoff(self):
        """Fence this writer before acknowledging an idle renderer handoff."""
        try:
            while not self.stopped:
                await asyncio.sleep(.2)
                value = self.read()
                offer = value.get('handoffId')
                if not offer and value.get('done') is not True:
                    continue
                async with self.lock:
                    # A call may have completed while we waited for the lock.
                    value = self.read()
                    if self.stopped:
                        continue
                    request = self._request_packet()
                    seq = value.get('seq')
                    if (value.get('done') is True and not self.unconfirmed_mutations and
                            request.get('type') == 'request' and request.get('seq') == seq == self.seq and
                            ('error' not in value or request.get('method') not in MUTATIONS
                             or self._known_start_rejection(value))):
                        # A timed-out call can receive a late signed result.
                        # Clear only that exact request, once, after its result
                        # has become known; do not overwrite a newer request.
                        try:
                            await self._write_locked({'generation': self.generation,
                                'seq': seq, 'type': 'idle'})
                        except OSError:
                            continue
                    request = self._request_packet()
                    if (not isinstance(offer, str) or not offer or
                            value.get('handoffId') != offer or self.unconfirmed_mutations or
                            request.get('type') != 'idle' or request.get('seq') != self.seq or
                            value.get('seq') != self.seq):
                        continue
                    self.stopped = True
                    try:
                        await self._write_locked({'generation': self.generation,
                            'seq': self.seq, 'type': 'handoff-ack', 'handoffId': offer})
                    except OSError:
                        # The offer may already be visible to the renderer. Do not
                        # reopen this writer after an uncertain acknowledgement.
                        self.message = 'Claude 接替确认写入失败，请手动重新连接'
                    return
        except asyncio.CancelledError:
            pass

    async def _write_locked(self, value):
        """Keep the mailbox lock until a submitted filesystem write settles."""
        task = asyncio.create_task(asyncio.to_thread(self.write, value))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            try:
                task.result()
            except Exception:
                pass
            raise

    def _request_packet(self):
        try:
            envelope = json.loads((self.directory/'request.json').read_text(encoding='utf-8'))
            payload, signature = envelope['payload'], envelope['signature']
            expected = hmac.new(self.token.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
                return {}
            value = json.loads(payload)
            return value if value.get('generation') == self.generation else {}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}

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
        if self.staged_reply and (self.cached.get('receipt') or time.time() - self.cached.get('timestamp', 0) >= 20):
            self.staged_reply.collect(path, self.generation, MAX_PACKET, self.pack)
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
            if value.get('done') is True and ('error' not in value or self._known_start_rejection(value)):
                self.unconfirmed_mutations.discard(value.get('seq'))
            self.cached, self.stamp = value, stamp
            return value
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Desktop writes are not atomic; partial packets must never dispatch.
            return self.cached

    @property
    def connected(self):
        value = self.read()
        timestamp = value.get('timestamp', 0)
        return (not self.stopped and isinstance(timestamp, (int, float)) and timestamp >= self.resume_after
                and 0 <= time.time() - timestamp < 20
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
            acknowledged = False
            if not self.connected or method not in self.capabilities.get(surface, []):
                raise BridgeUnavailable('Claude Desktop 文件桥接未连接或不支持此操作')
            self.seq += 1
            seq = self.seq
            if method in MUTATIONS:
                self.unconfirmed_mutations.add(seq)
            try:
                await self._write_locked(
                    {'type': 'request', 'generation': self.generation, 'seq': seq,
                     'expires': time.time() + timeout, 'surface': surface,
                     'method': method, 'args': list(args)})
            except OSError as exc:
                self.unconfirmed_mutations.discard(seq)
                raise BridgeUnavailable('无法写入本地桥接文件，请检查文件占用和目录权限') from exc
            except ValueError:
                self.unconfirmed_mutations.discard(seq)
                raise
            while time.monotonic() < deadline:
                reply = self.read()
                if reply.get('seq') == seq and reply.get('done') is True:
                    if 'error' in reply:
                        acknowledged = method not in MUTATIONS or self._known_start_rejection(reply)
                        raise BridgeUnavailable(str(reply['error']))
                    acknowledged = True
                    return reply.get('result')
                if not self.connected:
                    raise BridgeUnavailable('Claude Desktop 文件桥接已断开；操作结果可能不确定')
                await asyncio.sleep(.2)
            raise BridgeUnavailable('Claude Desktop 响应超时；请检查原会话，不要重复提交')

        finally:
            # Only this writer may clear its last request. An uncertain mutation
            # remains unacknowledged, so a new gateway cannot retire this owner.
            try:
                if acknowledged and not self.unconfirmed_mutations:
                    try:
                        await self._write_locked({'generation': self.generation,
                            'seq': seq, 'type': 'idle'})
                    except OSError:
                        pass
            finally:
                self.lock.release()

    def close(self):
        if not self.stopped:
            self.write({'generation': self.generation, 'seq': self.seq + 1, 'type': 'stop'})
        self.stopped = True
        if self.monitor and not self.monitor.done():
            loop = self.monitor.get_loop()
            try:
                current = asyncio.get_running_loop()
            except RuntimeError:
                current = None
            if current is loop:
                self.monitor.cancel()
            elif loop.is_running():
                async def finish():
                    self.monitor.cancel()
                    try:
                        await self.monitor
                    except asyncio.CancelledError:
                        pass
                asyncio.run_coroutine_threadsafe(finish(), loop).result(timeout=2)
