"""Runtime adaptation for LAN listeners, published entries, and Quick Tunnel."""
import json
import logging
import ipaddress
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from bridge.features.network.interfaces import (
    eligible_lan_addresses,
    enumerate_interfaces,
    network_signature,
    watch_changes,
)


LOGGER = logging.getLogger(__name__)


def notify_route_rechecks(callbacks):
    """Wake every enabled route even when another route's callback fails."""
    for callback in callbacks:
        try:
            callback()
        except Exception:
            LOGGER.exception('Network route check failed')


def _write_status(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n', encoding='utf-8')
    temp.chmod(0o600)
    temp.replace(path)


class DynamicListenerManager:
    """Own dynamic LAN listeners and keep their HTTP origins in sync."""

    def __init__(self, root, port, bridge, config, web_dir, server_factory):
        self.root = root
        self.port = port
        self.bridge = bridge
        self.config = config
        self.web_dir = web_dir
        self.server_factory = server_factory
        self.configure = lambda listener: None
        self.lock = threading.RLock()
        self.listeners = {}
        self.active = []
        self.error = ''
        self._owned_origins = set()
        self._clean_legacy_http_origins()

    def _clean_legacy_http_origins(self):
        # Manual additional origins are HTTPS. Numeric HTTP sources belong to
        # this dynamic owner, so previous-run LAN entries never stay trusted.
        for origin in list(self.root.origins):
            parsed = urlsplit(origin)
            if parsed.scheme != 'http':
                continue
            try:
                host = ipaddress.IPv4Address(parsed.hostname)
            except (ValueError, TypeError):
                continue
            if host.is_loopback or parsed.port != self.port:
                continue
            self.root.origins.discard(origin)
            self.root.hosts.discard(parsed.netloc)

    def _set_origins(self, addresses):
        new_origins = {f'http://{address}:{self.port}' for address in addresses}
        if new_origins == self._owned_origins:
            return
        # Change only this manager's LAN entries, preserving concurrent fixed,
        # Quick Tunnel and loopback entries. Never temporarily clear shared sets.
        removed = self._owned_origins - new_origins
        self.root.origins.difference_update(removed)
        self.root.origins.update(new_origins)
        self.root.hosts.difference_update(urlsplit(origin).netloc for origin in removed)
        self.root.hosts.update(urlsplit(origin).netloc for origin in new_origins)
        self._owned_origins = new_origins

    def _start(self, address):
        listener = self.server_factory((address, self.port), self.bridge, self.config,
                                       self.web_dir, shared=self.root)
        thread = threading.Thread(target=listener.serve_forever,
                                  kwargs={'poll_interval': 0.5},
                                  name='gateway-lan-' + address, daemon=True)
        thread.start()
        return listener, thread

    def _close(self, entry):
        listener, thread = entry
        listener.shutdown()
        thread.join(timeout=3)
        listener.server_close()

    def apply(self, addresses):
        desired = list(dict.fromkeys(addresses))
        with self.lock:
            previous = list(self.active)
            if set(desired) == set(self.active) and not self.error:
                return list(self.active), ''
            errors = []
            started = []
            for address in desired:
                if address in self.listeners:
                    continue
                try:
                    entry = self._start(address)
                    self.listeners[address] = entry
                    started.append(address)
                    self.configure(entry[0])
                except OSError as exc:
                    errors.append(f'{address}: {exc}')
            if errors:
                # Never publish a partial replacement. Roll back listeners that
                # were newly opened, and keep the previous working listeners.
                for address in list(started):
                    if address in previous:
                        continue
                    entry = self.listeners[address]
                    try:
                        self._close(entry)
                        del self.listeners[address]
                    except OSError:
                        pass
                self.active = sorted(self.listeners)
                self.error = '; '.join(errors)
                self._set_origins(self.active)
                return list(self.active), self.error
            for address in list(self.listeners):
                if address in desired:
                    continue
                entry = self.listeners[address]
                try:
                    self._close(entry)
                    del self.listeners[address]
                except OSError as exc:
                    errors.append(f'{address}: {exc}')
            self.active = sorted(self.listeners)
            self.error = '; '.join(errors)
            self._set_origins(self.active)
            return list(self.active), self.error

    def addresses(self):
        with self.lock:
            return list(self.active)

    def error_message(self):
        with self.lock:
            return self.error

    def close(self):
        failures = []
        with self.lock:
            for address in list(self.listeners):
                entry = self.listeners[address]
                try:
                    self._close(entry)
                    del self.listeners[address]
                except OSError as exc:
                    failures.append(f'{address}: {exc}')
            self.active = sorted(self.listeners)
            self.error = '; '.join(failures)
            self._set_origins(self.active)


class RuntimeNetworkMonitor:
    """Opt-in adaptation: native change events, with a slow polling fallback."""

    RETRY_MIN = 2.0
    RETRY_MAX = 60.0

    def __init__(self, data_dir, preferences, port, manager, instance_id,
                 enumerator=None, interval=30.0, stability=3.0,
                 route_enumerator=None, clock=time.monotonic, pid=None,
                 enabled=False, watcher_factory=None):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir/'network-status.json'
        self.preferences = preferences
        self.port = port
        self.manager = manager
        self.instance_id = str(instance_id or '')
        self.enumerator = enumerator or enumerate_interfaces
        self.route_enumerator = route_enumerator or network_signature
        self.watcher_factory = watcher_factory or watch_changes
        self.interval = interval
        self.stability = stability
        self.clock = clock
        self.pid = pid
        self.enabled = bool(enabled)
        self.closed = threading.Event()
        self.worker = None
        self.watcher = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.lifecycle_lock = threading.Lock()
        self.lock = threading.RLock()
        self.pending = None
        self.pending_since = None
        self.pending_route = None
        self.pending_route_since = None
        self.route_signature = None
        self.interfaces = []
        self.active = []
        self.network_changed = None
        self.network_cancelled = None
        self.last_status_key = None
        self._initialized = False
        self._generation = 0
        self._retry_at = 0.0
        self._retry_delay = self.RETRY_MIN
        self._scan_error = ''
        self._fast_until = None

    def _desired(self):
        selected = self.preferences.get('lanInterfaces')
        if not self.preferences.get('lan'):
            return []
        if selected is None:
            return sorted(eligible_lan_addresses(self.interfaces))
        wanted = set(selected)
        return sorted({address for row in self.interfaces if row['id'] in wanted
                       for address in row.get('addresses', [])})

    def _route_signature(self):
        value = self.route_enumerator(self.interfaces)
        return json.loads(json.dumps(value, sort_keys=True)) if value is not None else {}

    def _status(self):
        selected = self.preferences.get('lanInterfaces')
        error = str(self.manager.error_message() or self._scan_error)
        pending = self.enabled and self.pending is not None and self.pending != self.active
        route_pending = self.enabled and self.pending_route != self.route_signature
        return {
            'pid': self.pid,
            'instanceId': self.instance_id,
            'enabled': self.enabled,
            'mode': 'off' if not self.preferences.get('lan') else
                    'all' if selected is None else 'selected',
            'selectedInterfaces': [] if selected is None else [str(item) for item in selected],
            'interfaces': self.interfaces,
            'addresses': list(self.active),
            'pendingAddresses': list(self.pending) if pending else [],
            'stable': self._initialized and not pending and not route_pending and not error,
            'error': error,
        }

    def status(self):
        with self.lock:
            return self._status()

    def _publish(self):
        status = self._status()
        key = json.dumps(status, separators=(',', ':'), sort_keys=True)
        if key != self.last_status_key:
            status['updatedAt'] = int(time.time())
            try:
                _write_status(self.path, status)
            except OSError:
                LOGGER.warning('Could not write network adaptation status', exc_info=True)
            else:
                self.last_status_key = key

    def scan(self, initial=False):
        # The disabled mode initializes LAN once; subsequent calls do no work.
        with self.lock:
            if self.closed.is_set() or (not self.enabled and
                    (not initial or self._initialized)):
                return list(self.active)
            generation = self._generation
        rows = self.enumerator()
        with self.lock:
            if generation != self._generation or self.closed.is_set():
                return list(self.active)
            self.interfaces = rows
            self._scan_error = ''
            desired = self._desired()
            now = self.clock()
            if desired != self.active and self._fast_until is None:
                self._fast_until = now + 12.0
            if self.pending != desired:
                self.pending = desired
                self.pending_since = now
                self._retry_at = 0.0
                self._retry_delay = self.RETRY_MIN
            lan_ready = initial or now - self.pending_since >= self.stability
            if lan_ready and (not self._initialized or desired != self.active or
                              self.manager.error_message()) and now >= self._retry_at:
                self.active, error = self.manager.apply(desired)
                if error:
                    self._retry_at = now + self._retry_delay
                    self._retry_delay = min(self.RETRY_MAX, self._retry_delay * 2)
                else:
                    self._retry_at = 0.0
                    self._retry_delay = self.RETRY_MIN
            self.active = self.manager.addresses()
            route_changed = False
            if self.enabled:
                next_route = self._route_signature()
                if self.pending_route != next_route:
                    self.pending_route = next_route
                    self.pending_route_since = now
                    if self._fast_until is None:
                        self._fast_until = now + 12.0
                if self.route_signature is None or initial:
                    self.route_signature = next_route
                elif (next_route != self.route_signature and
                      now - self.pending_route_since >= self.stability):
                    self.route_signature = next_route
                    route_changed = True
            self._initialized = True
            if self.pending == self.active and self.pending_route == self.route_signature:
                self._fast_until = None
            self._publish()
            if route_changed and self.network_changed:
                try:
                    self.network_changed()
                except Exception:
                    LOGGER.exception('Network route check failed')
            return list(self.active)

    def _wait_interval(self):
        with self.lock:
            now = self.clock()
            if self._retry_at > now:
                return min(self.interval, self._retry_at - now)
            if (self.pending != self.active or self.pending_route != self.route_signature):
                if self._fast_until is not None and now < self._fast_until:
                    return min(2.0, self.interval)
                return self.interval
            return self.interval

    def _run(self, stop):
        last_scan = 0.0
        while not stop.is_set() and not self.closed.is_set():
            # Coalesce bursty OS events; callbacks never enumerate or bind.
            with self.lock:
                spacing = self.interval if (self._fast_until is not None and
                           self.clock() >= self._fast_until) else 2.0
            if stop.wait(max(0.0, spacing - (time.monotonic() - last_scan))):
                break
            self._wake.clear()
            try:
                self.scan()
            except Exception as exc:
                with self.lock:
                    self._scan_error = str(exc)
                    self._retry_at = self.clock() + self._retry_delay
                    self._retry_delay = min(self.RETRY_MAX, self._retry_delay * 2)
                    try:
                        self._publish()
                    except OSError:
                        pass
                LOGGER.exception('Network adaptation scan failed')
            last_scan = time.monotonic()
            self._wake.wait(self._wait_interval())

    def start(self):
        self.set_enabled(self.enabled)

    def set_enabled(self, enabled):
        with self.lifecycle_lock:
            if enabled and self.worker and self.worker.is_alive() and self._stop.is_set():
                raise ValueError('Previous network monitor is still stopping')
            with self.lock:
                if self.closed.is_set():
                    return self._status()
                changed = self.enabled != bool(enabled)
                self.enabled = bool(enabled)
                if changed:
                    self._generation += 1
                    self.pending = None
                    self.pending_route = self.route_signature
                    self._retry_at = 0.0
                    self._retry_delay = self.RETRY_MIN
                    self._fast_until = None
                if self._initialized:
                    self._publish()
            if self.enabled:
                if not self.worker or not self.worker.is_alive():
                    self._stop = threading.Event()
                    self._wake.clear()
                    try:
                        self.watcher = self.watcher_factory(self._wake.set)
                    except Exception:
                        LOGGER.exception('Network event watcher unavailable; using fallback checks')
                    self.worker = threading.Thread(target=self._run, args=(self._stop,),
                                                   name='network-adaptation', daemon=True)
                    self.worker.start()
            else:
                self._stop.set()
                self._wake.set()
                if self.network_cancelled:
                    self.network_cancelled()
                if self.watcher:
                    self.watcher.close()
                    self.watcher = None
                if self.worker and self.worker is not threading.current_thread():
                    self.worker.join(timeout=5)
                    if not self.worker.is_alive():
                        self.worker = None
            return self.status()

    def addresses(self):
        with self.lock:
            return list(self.active)

    def close(self):
        self.set_enabled(False)
        self.closed.set()
        self.manager.close()
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
