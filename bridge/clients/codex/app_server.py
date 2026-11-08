"""WebSocket client for an already running Codex app-server control socket.

The proxy command only connects to the given socket. This module never starts a
daemon, resumes a thread, or changes its model, provider, cwd, or approvals.
"""

import json
import os
import subprocess
import threading
from concurrent.futures import Future, TimeoutError
from bridge.clients.websocket import WebSocket
from pathlib import Path


class AppServerError(RuntimeError):
    pass


class AppServerUnavailable(AppServerError):
    pass


class AppServerUnknownResult(AppServerError):
    """The request may have reached the runtime; the caller must not replay it."""


class AppServerUnsupported(AppServerError):
    pass


class _WebSocket(WebSocket):
    def __init__(self, reader, writer):
        super().__init__(reader, writer, error=AppServerError,
                         unavailable=AppServerUnavailable, unsupported=AppServerUnsupported)


class CodexAppServer:
    MAX_LINE = 64 * 1024 * 1024

    def __init__(self, socket_path, cli='codex', on_event=None,
                 on_server_request=None, on_disconnect=None, process_factory=None,
                 command=None, check_socket=True, client_name='codex-mobile-bridge'):
        if not socket_path or not Path(socket_path).is_absolute():
            raise ValueError('An absolute existing control socket path is required')
        self.socket_path = str(socket_path)
        self.cli = str(cli)
        self.command = list(command) if command is not None else [
            self.cli, 'app-server', 'proxy', '--sock', self.socket_path]
        self.check_socket = check_socket
        self.client_name = client_name
        self.on_event = on_event or (lambda event: None)
        self.on_server_request = on_server_request or (lambda request: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.process_factory = process_factory or subprocess.Popen
        self.process = None
        self.transport = None
        self.server_info = None
        self.pending = {}
        self.next_id = 1
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.connect_lock = threading.Lock()

    def connect(self, timeout=5):
        with self.connect_lock:
            if self.process is not None and self.server_info is not None:
                return self.server_info
            if self.check_socket and not Path(self.socket_path).exists():
                raise AppServerUnavailable('Codex app-server control socket is unavailable')
            try:
                options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
                process = self.process_factory(
                    self.command,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, **options)
            except OSError as exc:
                raise AppServerUnavailable('Could not connect to the existing Codex runtime') from exc
            self.process = process
            handshake = Future()
            def open_websocket():
                try:
                    handshake.set_result(_WebSocket(process.stdout, process.stdin))
                except Exception as exc:
                    handshake.set_exception(exc)
            threading.Thread(target=open_websocket, daemon=True).start()
            try:
                transport = handshake.result(timeout=timeout)
                self.transport = transport
            except TimeoutError as exc:
                self.close()
                raise AppServerUnavailable('Codex control socket handshake timed out') from exc
            except Exception:
                self.close()
                raise
            threading.Thread(target=self._read_loop, args=(process, transport), daemon=True).start()
            try:
                info = self.request('initialize', {
                    'clientInfo': {'name': self.client_name, 'version': '1'},
                    'capabilities': {'experimentalApi': True}}, timeout=timeout,
                    replay_safe=True)
                if not isinstance(info, dict) or not info.get('codexHome'):
                    raise AppServerUnsupported('Unexpected app-server initialize response')
                self.server_info = info
                self._send({'method': 'initialized', 'params': {}})
                return info
            except Exception:
                self.close()
                raise

    def _send(self, message):
        with self.write_lock:
            transport = self.transport
            if transport is None:
                raise AppServerUnavailable('Codex app-server connection is closed')
            try:
                transport.send(message)
            except (OSError, ValueError) as exc:
                raise AppServerUnknownResult('Codex runtime connection failed; request result is unknown') from exc

    def request(self, method, params=None, timeout=15, replay_safe=False):
        """Send one RPC. A failed write or lost response is never retried here."""
        with self.lock:
            request_id = self.next_id
            self.next_id += 1
            future = Future()
            self.pending[request_id] = future
        try:
            self._send({'id': request_id, 'method': method, 'params': params or {}})
            try:
                response = future.result(timeout=timeout)
            except TimeoutError as exc:
                if replay_safe:
                    raise AppServerUnavailable('Codex runtime read timed out') from exc
                raise AppServerUnknownResult('Codex runtime response timed out; request may have completed') from exc
            if 'error' in response:
                error = response['error']
                if method == 'thread/delete' and isinstance(error, dict):
                    from bridge.features.sessions.create import native_delete_error
                    raise native_delete_error(error, (params or {}).get('threadId'))
                code = error.get('code') if isinstance(error, dict) else None
                detail = error.get('message', 'App-server request failed') if isinstance(error, dict) else str(error)
                if method in ('thread/archive', 'thread/unarchive'):
                    from bridge.features.sessions.create import ArchiveRejected
                    raise ArchiveRejected(error)
                if code == -32601:
                    raise AppServerUnsupported(detail)
                raise AppServerError(detail)
            return response['result']
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def loaded_threads(self, limit=200):
        """List IDs loaded in this runtime; loading does not establish ownership."""
        self.connect()
        ids = []
        cursor = None
        seen = set()
        while True:
            result = self.request('thread/loaded/list', {'limit': limit, 'cursor': cursor},
                                  replay_safe=True)
            if not isinstance(result, dict) or not isinstance(result.get('data'), list):
                raise AppServerUnsupported('Unexpected loaded thread list response')
            ids.extend(result['data'])
            cursor = result.get('nextCursor')
            if not cursor:
                return ids
            if cursor in seen:
                raise AppServerUnsupported('Repeated loaded thread cursor')
            seen.add(cursor)

    def is_loaded(self, thread_id):
        return thread_id in self.loaded_threads()

    def respond(self, request_id, result=None, error=None):
        """Return a decision to a server request only after the caller authorizes it."""
        if error is not None:
            self._send({'id': request_id, 'error': error})
        else:
            self._send({'id': request_id, 'result': result})

    def _read_loop(self, process, transport):
        try:
            while True:
                message = transport.receive()
                with self.lock:
                    if self.process is not process:
                        return
                if not isinstance(message, dict):
                    continue
                if 'id' in message and ('result' in message or 'error' in message):
                    with self.lock:
                        if self.process is not process:
                            return
                        future = self.pending.get(message['id'])
                        if future is not None and not future.done():
                            future.set_result(message)
                elif 'id' in message and 'method' in message:
                    self.on_server_request(message)
                elif 'method' in message:
                    self.on_event(message)
        except (OSError, EOFError, ValueError, TypeError, AppServerError):
            pass
        finally:
            with self.lock:
                current = self.process is process
                if current:
                    self.process = None
                    self.transport = None
                    self.server_info = None
                    for future in self.pending.values():
                        if not future.done():
                            future.set_exception(AppServerUnknownResult(
                                'Codex runtime connection closed; request result is unknown'))
            if current:
                self._stop_process(process)
                self.on_disconnect()

    def close(self):
        """Disconnect this proxy client; never stop or restart the daemon."""
        with self.lock:
            process = self.process
            self.process = None
            self.transport = None
            self.server_info = None
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(AppServerUnknownResult(
                        'Codex runtime connection closed; request result is unknown'))
        if process is not None:
            self._stop_process(process)

    @staticmethod
    def _stop_process(process):
        # The child is a byte proxy only. Stopping it never stops app-server.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
