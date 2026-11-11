"""Exact-origin cloud accounts and device keys, exclusively in the native OS vault."""
import base64
import hashlib
import hmac
import json
import math
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bridge.features.accounts.secrets import backend, service
from bridge.features.auth.auth import session_file_lock
from bridge.features.auth.tls import client_context
from bridge.features.network.cloud_crypto import exact_origin, new_key, public_key
from bridge.features.network.cloud_access import BindingAccess, WebAccess
from bridge.features.notifications.channels import read_json, write_json
from bridge.features.notifications.cloud import CloudNotifications


class CloudError(ValueError):
    def __init__(self, code, status=0):
        self.code, self.status = code, status
        super().__init__("Cloud service: " + code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def scope(origin, account_id):
    exact_origin(origin)
    uuid.UUID(account_id)
    return hashlib.sha256((origin + "\0" + account_id).encode("ascii")).hexdigest()


def cloud_time(value):
    if not isinstance(value, str) or len(value) > 64:
        raise CloudError('invalid_entitlement_response')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise CloudError('invalid_entitlement_response') from None


def entitlement_seconds(value):
    if value['active'] is not True:
        return 0
    if value['required'] is False:
        return None
    return max(0, (cloud_time(value['expires_at']) - cloud_time(value['server_time'])).total_seconds())


def verified_entitlement(value):
    if (not isinstance(value, dict) or type(value.get('required')) is not bool
            or type(value.get('active')) is not bool):
        raise CloudError('invalid_entitlement_response')
    server_time = cloud_time(value.get('server_time')).isoformat()
    expires_at = value.get('expires_at')
    if expires_at is not None:
        expires_at = cloud_time(expires_at).isoformat()
    if value['required'] and value['active'] and expires_at is None:
        raise CloudError('invalid_entitlement_response')
    result = {'required': value['required'], 'active': value['active'],
              'expires_at': expires_at, 'server_time': server_time}
    if result['required'] and result['active'] and not entitlement_seconds(result):
        result['active'] = False
    return result


class Controller:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'cloud-account.json'
        self.lock_path = self.directory / 'cloud-account.lock'
        self.access = BindingAccess(directory)
        self.web_access = WebAccess(directory)

    def web_access_state(self, config=None):
        config = self.config() if config is None else config
        if not config:
            return {'allowed': False, 'revision': 0, 'error': ''}
        return self.web_access.state(scope(config['origin'], config['account_id']), config['gateway_id'])

    def set_web_access(self, value, block=None):
        if (set(value) != {'action', 'origin', 'account_id', 'gateway_id', 'allowed', 'expected_revision'}
                or type(value['allowed']) is not bool or type(value['expected_revision']) is not int
                or not 0 <= value['expected_revision'] < 2**53 - 1):
            raise ValueError('invalid cloud operation')
        with session_file_lock(self.lock_path):
            config = self.config()
            if not config or any(value[key] != config[key] for key in ('origin', 'account_id', 'gateway_id')):
                raise CloudError('credential_scope_mismatch')
            current = self.web_access_state(config)
            if current['error']:
                raise CloudError(current['error'])
            if current['revision'] != value['expected_revision']:
                raise CloudError('web_access_conflict')
            if block is not None:
                block()
            try:
                self.web_access.save(scope(config['origin'], config['account_id']), config['gateway_id'],
                                     value['allowed'], current['revision'] + 1)
            except (OSError, ValueError):
                raise CloudError('web_access_storage_error') from None
            return self.status()

    def config(self):
        value = read_json(self.path, {})
        if value:
            if (not isinstance(value, dict)
                    or set(value) != {'origin', 'account_id', 'gateway_id', 'login', 'enabled'}
                    or not isinstance(value['login'], str) or len(value['login']) > 320
                    or type(value['enabled']) is not bool):
                raise CloudError('invalid_local_configuration')
            scope(value['origin'], value['account_id'])
            uuid.UUID(value['gateway_id'])
        return value

    def _vault(self):
        return backend(), service(self.directory) + '.cloud.v1'

    def secret(self, config=None):
        config = self.config() if config is None else config
        if not config:
            raise CloudError('login_required', 401)
        vault, name = self._vault()
        try:
            value = json.loads(vault.get_password(name, scope(config['origin'], config['account_id'])) or '{}')
        except Exception:
            raise CloudError('native_vault_unavailable') from None
        if value.get('origin') != config['origin'] or value.get('account_id') != config['account_id']:
            raise CloudError('login_required', 401)
        return value

    def save_secret(self, config, value):
        if value.get('origin') != config['origin'] or value.get('account_id') != config['account_id']:
            raise CloudError('credential_scope_mismatch')
        vault, name = self._vault()
        try:
            vault.set_password(name, scope(config['origin'], config['account_id']), json.dumps(value))
        except Exception:
            raise CloudError('native_vault_unavailable') from None

    def request(self, origin, path, method='GET', value=None, token=''):
        exact_origin(origin)
        if not path.startswith('/v1/') or any(c in path for c in ('?', '#', '..', '\\')):
            raise ValueError('invalid cloud API path')
        data = json.dumps(value, separators=(',', ':')).encode('utf-8') if value is not None else None
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json',
                   'User-Agent': 'BridgeDesktop/1'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=client_context()))
        req = urllib.request.Request(origin + path, data=data, headers=headers, method=method)
        try:
            with opener.open(req, timeout=20) as response:
                raw = response.read(65537)
                if len(raw) > 65536:
                    raise CloudError('response_too_large')
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                raise CloudError('redirect_forbidden', exc.code) from None
            try:
                code = json.loads(exc.read(65536)).get('code', 'unavailable')
            except (ValueError, OSError):
                code = 'unavailable'
            if not isinstance(code, str) or not code.replace('_', '').isalnum() or len(code) > 80:
                code = 'unavailable'
            raise CloudError(code, exc.code) from None
        except (urllib.error.URLError, OSError):
            raise CloudError('network_or_certificate_error') from None

    def account_request(self, path, method='GET', value=None):
        # Rotation is serialized across the management worker and gateway process.
        with session_file_lock(self.lock_path):
            return self._account_request_locked(path, method, value)

    def _account_request_locked(self, path, method='GET', value=None):
        config = self.config()
        secret = self.secret(config)
        if secret.get('access_deadline', 0) <= time.time() + 30:
            token = secret.get('refresh_token', '')
            if not token:
                raise CloudError('login_required', 401)
            # Persist the same attempt before networking, including across process restarts.
            request_id = secret.get('refresh_request_id') or str(uuid.uuid4())
            secret['refresh_request_id'] = request_id
            self.save_secret(config, secret)
            started = time.time()
            result = self.request(config['origin'], '/v1/auth/refresh', 'POST',
                                  {'refresh_token': token, 'request_id': request_id})
            if result['account']['id'] != config['account_id']:
                raise CloudError('credential_scope_mismatch')
            secret.update(access_token=result['access_token'], refresh_token=result['refresh_token'],
                          access_deadline=started + result['expires_in'])
            secret.pop('refresh_request_id', None)
            self.save_secret(config, secret)
        if not secret.get('access_token'):
            raise CloudError('login_required', 401)
        return self.request(config['origin'], path, method, value, secret['access_token'])

    def device_request(self, path, method='GET', value=None):
        config = self.config()
        return self.request(config['origin'], path, method, value, self.secret(config)['device_token'])

    def _save_entitlement_locked(self, config, value, code=''):
        # This file is display metadata, not an offline authorization or credential.
        write_json(self.directory / 'cloud-entitlement.json', {
            'scope': scope(config['origin'], config['account_id']), 'gateway_id': config['gateway_id'],
            'entitlement': value, 'code': code, 'checked_at': time.time()})

    def _refresh_entitlement_locked(self):
        config = self.config()
        if not config:
            raise CloudError('login_required', 401)
        try:
            if not self.secret(config).get('device_token'):
                raise CloudError('login_required', 401)
            value = verified_entitlement(self._account_request_locked('/v1/account/entitlement'))
        except (CloudError, KeyError, TypeError, ValueError) as exc:
            error = exc if isinstance(exc, CloudError) else CloudError('invalid_entitlement_response')
            self._save_entitlement_locked(config, None, error.code)
            raise error from None
        self._save_entitlement_locked(config, value)
        return value

    def refresh_entitlement(self):
        with session_file_lock(self.lock_path):
            return self._refresh_entitlement_locked()

    def _cached_entitlement(self, config):
        if not config:
            return None, ''
        cached = read_json(self.directory / 'cloud-entitlement.json', {})
        if (not isinstance(cached, dict) or cached.get('scope') != scope(config['origin'], config['account_id'])
                or cached.get('gateway_id') != config['gateway_id']):
            return None, ''
        code = cached.get('code', '')
        if not isinstance(code, str) or len(code) > 80 or (code and not code.replace('_', '').isalnum()):
            code = 'invalid_entitlement_response'
        try:
            value = verified_entitlement(cached.get('entitlement'))
            remaining = entitlement_seconds(value)
            checked_at = cached['checked_at']
            if type(checked_at) not in (float, int) or not math.isfinite(checked_at):
                raise ValueError('invalid cache time')
            elapsed = max(0, time.time() - checked_at)
            if remaining is not None and remaining <= elapsed:
                value['active'] = False
            return value, code
        except (CloudError, KeyError, TypeError, ValueError):
            return None, code or 'invalid_entitlement_response'

    def login(self, value):
        with session_file_lock(self.lock_path):
            return self._login_locked(value)

    def _login_locked(self, value):
        origin = exact_origin(value['origin'])
        login, password = value.get('login', ''), value.get('password', '')
        if not isinstance(login, str) or not isinstance(password, str) or len(password) > 1024:
            raise ValueError('invalid cloud login')
        self.request(origin, '/v1/capabilities')
        result = self.request(origin, '/v1/auth/login', 'POST', {'login': login, 'password': password})
        account = result['account']
        config = {'origin': origin, 'account_id': account['id'], 'login': account['login'], 'enabled': True}
        try:
            old = self.secret(config)
        except CloudError as exc:
            if exc.code != 'login_required':
                raise
            old = {}
        # Lost control keys never inherit old bindings or a cloud-record authorization.
        if not old.get('control_private') or not old.get('preview_private'):
            old = {'origin': origin, 'account_id': account['id'], 'gateway_id': str(uuid.uuid4()),
                   'control_private': base64.b64encode(new_key()).decode('ascii'),
                   'preview_private': base64.b64encode(new_key()).decode('ascii')}
        config['gateway_id'] = old['gateway_id']
        key = public_key(base64.b64decode(old['control_private'], validate=True))
        device = self.request(origin, '/v1/gateways/enroll', 'POST',
                              {'id': old['gateway_id'], 'name': value.get('name') or 'Desktop',
                               'control_public_key': base64.b64encode(key).decode('ascii')}, result['access_token'])
        if device['account_id'] != account['id'] or device['control_public_key'] != base64.b64encode(key).decode('ascii'):
            raise CloudError('device_identity_mismatch')
        old.update(access_token=result['access_token'], refresh_token=result['refresh_token'],
                   access_deadline=time.time() + result['expires_in'], device_token=device['device_token'])
        old.pop('refresh_request_id', None)
        self.save_secret(config, old)
        write_json(self.path, config)
        try:
            self._refresh_entitlement_locked()
        except (CloudError, OSError):
            pass  # Login was confirmed; a failed service read is not a failed login.
        return self.status()

    def logout(self):
        confirmed, error, clear_error = False, '', ''
        with session_file_lock(self.lock_path):
            config = self.config()
            if config:
                # Fence local relay authority before any vault or network operation.
                write_json(self.path, {})
                try:
                    secret = self.secret(config)
                except (CloudError, OSError, ValueError) as exc:
                    error = exc.code if isinstance(exc, CloudError) else 'native_vault_unavailable'
                    if error != 'login_required':
                        clear_error = error
                    secret = None
                if secret is not None:
                    token = secret.get('access_token', '')
                    for name in ('access_token', 'refresh_token', 'device_token'):
                        secret[name] = ''
                    try:
                        self.save_secret(config, secret)
                    except (CloudError, OSError, ValueError) as exc:
                        clear_error = exc.code if isinstance(exc, CloudError) else 'native_vault_unavailable'
                    if token:
                        # Logout never spends a refresh token or requires a valid login.
                        try:
                            result = self.request(config['origin'], '/v1/auth/logout', 'POST', {}, token)
                            confirmed = isinstance(result, dict) and result.get('logged_out') is True
                            if not confirmed:
                                error = 'logout_result_unconfirmed'
                        except CloudError as exc:
                            error = exc.code
                        except (OSError, TypeError, ValueError):
                            error = 'logout_result_unconfirmed'
                    else:
                        error = 'login_required'
        return {**self.status(), 'logged_out': True, 'remote_logout_confirmed': confirmed,
                'logout_error': error, 'credential_clear_error': clear_error}

    def status(self):
        config = self.config()
        pins = read_json(self.directory / 'cloud-bindings.json', {})
        bindings = pins.get(scope(config['origin'], config['account_id']), {}) if config and isinstance(pins, dict) else {}
        if not isinstance(bindings, dict):
            bindings = {}
        runtime = read_json(self.directory / 'cloud-status.json', {})
        entitlement, error = self._cached_entitlement(config)
        current_scope = scope(config['origin'], config['account_id']) if config else ''
        if not isinstance(runtime, dict) or runtime.get('scope') != current_scope or runtime.get('gateway_id') != config.get('gateway_id'):
            runtime = {'state': 'stopped'}
        return {'configured': bool(config), **config,
                'enabled': bool(config),
                'web_access': self.web_access_state(config),
                'entitlement': entitlement, 'entitlement_error': error,
                'runtime': {k: runtime[k] for k in ('state', 'code', 'pid') if k in runtime},
                'bindings': [{'binding_id': k, **{name: pin[name] for name in ('gateway_id', 'mobile_public_key', 'preview_public_key', 'device_name', 'safety_code') if name in pin},
                              **self.access.state(current_scope, config['gateway_id'], k)}
                             for k, pin in bindings.items() if isinstance(pin, dict) and pin.get('gateway_id') == config['gateway_id']]}

    def trusted_pin(self, config, binding):
        pins = read_json(self.directory / 'cloud-bindings.json', {})
        pin = pins.get(scope(config['origin'], config['account_id']), {}).get(binding)
        return pin if isinstance(pin, dict) and pin.get('gateway_id') == config['gateway_id'] else None

    def device_directory(self):
        config = self.config()
        secret = self.secret(config)
        caps = self.request(config['origin'], '/v1/capabilities')
        if caps.get('device_discovery') != 'account_directory_v1' or caps.get('binding_access') != 'desktop_switch_v1':
            raise CloudError('automatic_discovery_unsupported')
        result = self.request(config['origin'], '/v1/gateway/bindings', token=secret['device_token'])
        records = result.get('bindings') if isinstance(result, dict) else None
        if not isinstance(records, list) or len(records) > 200:
            raise CloudError('invalid_device_directory')
        desktop = base64.b64encode(public_key(base64.b64decode(secret['control_private'], validate=True))).decode('ascii')
        key = scope(config['origin'], config['account_id'])
        with session_file_lock(self.lock_path):
            if self.config() != config or self.secret(config).get('device_token') != secret['device_token']:
                raise CloudError('credential_scope_mismatch')
            proofs = read_json(self.directory / 'cloud-device-proofs.json', {}).get(key + ':' + config['gateway_id'], {})
            devices = []
            seen = set()
            for record in records:
                binding = str(uuid.UUID(record['id']))
                installation = str(uuid.UUID(record['installation_id']))
                if (binding in seen or record.get('gateway_id') != config['gateway_id']
                        or record.get('gateway_public_key') != desktop
                        or record.get('status') not in ('pending', 'active', 'disabled', 'revoked')
                        or type(record.get('access_revision')) is not int or not 0 <= record['access_revision'] < 2**63
                        or not isinstance(record.get('device_name'), str) or not 1 <= len(record['device_name'].encode('utf-8')) <= 100):
                    raise CloudError('invalid_device_directory')
                seen.add(binding)
                mobile = base64.b64decode(record['mobile_public_key'], validate=True)
                if len(mobile) != 32 or not any(mobile):
                    raise CloudError('invalid_device_directory')
                pin = self.trusted_pin(config, binding)
                proof = pin or proofs.get(binding, {})
                matches = not pin or pin.get('mobile_public_key') == record['mobile_public_key']
                verified = bool(proof.get('mobile_public_key') == record['mobile_public_key']
                                and (pin or proof.get('installation_id') == installation)
                                and proof.get('safety_code') and proof.get('preview_public_key'))
                access = self.access.observe(key, config['gateway_id'], binding, record, matches)
                devices.append({'binding_id': binding, 'installation_id': installation,
                                'device_name': record['device_name'], 'mobile_public_key': record['mobile_public_key'],
                                'preview_public_key': proof.get('preview_public_key', '') if verified else '',
                                'safety_code': proof.get('safety_code', '') if verified else '', 'proof_verified': verified,
                                'trusted': bool(pin) and matches, 'online': record.get('mobile_online') is True,
                                'device_revoked': record.get('device_revoked') is True, **access})
            write_json(self.directory / 'cloud-device-directory.json', {
                'scope': key, 'gateway_id': config['gateway_id'], 'devices': devices})
            return {'devices': devices}

    def set_binding_access(self, value, block=None):
        binding = str(uuid.UUID(value['binding_id']))
        allowed, expected = value.get('allowed'), value.get('expected_revision')
        if type(allowed) is not bool or type(expected) is not int or not 0 <= expected < 2**63:
            raise ValueError('invalid cloud access revision')
        with session_file_lock(self.lock_path):
            config = self.config()
            if not config:
                raise CloudError('login_required', 401)
            key = scope(config['origin'], config['account_id'])
            pin = self.trusted_pin(config, binding)
            generation = self.access.begin(key, config['gateway_id'], binding, allowed, expected)
        # Fence disk authority and running channels before waiting on cloud I/O.
        if block is not None:
            block(binding)
        notifications = CloudNotifications(self.directory)
        try:
            notifications.disable(config, binding)
        finally:
            notifications.close()
        error, confirmed = '', False
        try:
            secret = self.secret(config)
            record = self.request(config['origin'], '/v1/gateway/bindings/' + binding, token=secret['device_token'])
            if (record.get('gateway_id') != config['gateway_id'] or record.get('id') != binding
                    or record.get('status') == 'revoked' or record.get('device_revoked')):
                raise CloudError('device_access_unavailable')
            mobile = record['mobile_public_key']
            if allowed:
                proof = pin or read_json(self.directory / 'cloud-device-proofs.json', {}).get(key + ':' + config['gateway_id'], {}).get(binding)
                if (not proof or proof.get('mobile_public_key') != mobile
                        or not pin and (proof.get('installation_id') != record.get('installation_id')
                                       or not proof.get('safety_code') or not proof.get('preview_public_key')
                                       or not hmac.compare_digest(proof['safety_code'], str(value.get('safety_code', ''))))):
                    raise CloudError('device_proof_unavailable')
                with session_file_lock(self.lock_path):
                    if self.config() != config or self.access.state(key, config['gateway_id'], binding)['generation'] != generation:
                        raise CloudError('device_access_superseded')
                    pins_path = self.directory / 'cloud-bindings.json'
                    pins = read_json(pins_path, {})
                    trusted = {name: proof[name] for name in ('mobile_public_key', 'preview_public_key', 'device_name', 'safety_code')}
                    trusted['gateway_id'] = config['gateway_id']
                    if pin and any(pin.get(name) != trusted[name] for name in ('mobile_public_key', 'preview_public_key', 'gateway_id')):
                        raise CloudError('device_identity_mismatch')
                    pins.setdefault(key, {})[binding] = trusted
                    write_json(pins_path, pins)
            elif pin and pin.get('mobile_public_key') != mobile:
                raise CloudError('device_identity_mismatch')
            result = self.request(config['origin'], '/v1/gateway/bindings/' + binding + '/access', 'POST', {
                'allowed': allowed, 'expected_revision': expected, 'mobile_public_key': mobile}, secret['device_token'])
            if (result.get('id') != binding or result.get('gateway_id') != config['gateway_id']
                    or result.get('mobile_public_key') != mobile or result.get('status') != ('active' if allowed else 'disabled')
                    or type(result.get('access_revision')) is not int or result['access_revision'] <= expected):
                raise CloudError('device_access_unconfirmed')
            with session_file_lock(self.lock_path):
                if self.config() == config and self.secret(config).get('device_token') == secret['device_token']:
                    confirmed = self.access.finish(key, config['gateway_id'], binding, generation, result)
            if not confirmed:
                error = 'device_access_superseded'
                with session_file_lock(self.lock_path):
                    self.access.finish(key, config['gateway_id'], binding, generation)
        except (CloudError, OSError, KeyError, TypeError, ValueError) as exc:
            error = exc.code if isinstance(exc, CloudError) else 'device_access_unconfirmed'
            with session_file_lock(self.lock_path):
                self.access.finish(key, config['gateway_id'], binding, generation)
        return {'allowed': allowed and confirmed, 'remote_access_confirmed': confirmed, 'access_error': error}

    def control(self, value):
        if not isinstance(value, dict):
            raise ValueError('invalid cloud operation')
        action = value.get('action', 'status')
        if action == 'status':
            return self.status()
        if action == 'devices':
            return self.device_directory()
        if action == 'access':
            return self.set_binding_access(value)
        if action == 'web-access':
            return self.set_web_access(value)
        if action == 'login':
            return self.login(value)
        if action in ('capabilities', 'sponsorship', 'register', 'signup-code', 'signup-verify', 'forgot'):
            origin = exact_origin(value['origin'])
            if action == 'sponsorship':
                result = self.request(origin, '/v1/sponsorship')
                image = result.get('image') if isinstance(result, dict) else None
                prefix = 'data:image/png;base64,'
                if not isinstance(image, str) or not image.startswith(prefix) or len(image) > 65536:
                    raise CloudError('invalid_sponsorship_response')
                try:
                    raw = base64.b64decode(image[len(prefix):], validate=True)
                    if (len(raw) < 33 or raw[:8] != b'\x89PNG\r\n\x1a\n' or raw[12:16] != b'IHDR'
                            or not 64 <= int.from_bytes(raw[16:20], 'big') <= 1024
                            or not 64 <= int.from_bytes(raw[20:24], 'big') <= 1024):
                        raise ValueError('invalid PNG')
                except (ValueError, TypeError):
                    raise CloudError('invalid_sponsorship_response') from None
                return {'image': image}
            if action == 'capabilities':
                return self.request(origin, '/v1/capabilities')
            payload = {'login': value.get('login', '')}
            if action in ('register', 'signup-code', 'signup-verify'):
                if action == 'signup-verify':
                    code = value.get('verification_code', '')
                    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
                        raise ValueError('Enter the six-digit email verification code.')
                    payload['code'] = code
                capabilities = self.request(origin, '/v1/capabilities')
                if capabilities.get('mode') == 'selfhost':
                    raise CloudError('registration_disabled')
                methods = capabilities.get('email_verification_methods', [])
                if not isinstance(methods, list) or 'code' not in methods:
                    raise CloudError('email_code_unsupported')
                if capabilities.get('registration_flow') != 'email_code_password':
                    raise CloudError('email_first_registration_unsupported')
            if action == 'register':
                token = value.get('registration_token', '')
                if not isinstance(token, str) or len(token) != 43 or not all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in token):
                    raise CloudError('invalid_registration_token')
                payload['password'] = value.get('password', '')
                payload['registration_token'] = token
            endpoint = {'signup-code': 'registration-code', 'signup-verify': 'registration-verify'}.get(action, action)
            return self.request(origin, '/v1/auth/' + endpoint, 'POST', payload)
        if action == 'entitlement':
            return self.refresh_entitlement()
        if action == 'redeem':
            code = value.get('code', '')
            if not isinstance(code, str) or not 1 <= len(code) <= 128:
                raise ValueError('invalid redemption code')
            with session_file_lock(self.lock_path):
                config = self.config()
                self.secret(config)
                digest = hashlib.sha256(code.encode('utf-8')).hexdigest()
                path = self.directory / 'cloud-redemption.json'
                prior = read_json(path, {})
                if prior.get('scope') != scope(config['origin'], config['account_id']) or prior.get('digest') != digest:
                    prior = {'scope': scope(config['origin'], config['account_id']), 'digest': digest,
                             'request_id': str(uuid.uuid4())}
                    write_json(path, prior)
                result = self._account_request_locked('/v1/account/redeem', 'POST', {'code': code, 'request_id': prior['request_id']})
                try:
                    if not isinstance(result, dict) or not isinstance(result.get('id'), str):
                        raise ValueError('invalid redemption receipt')
                    uuid.UUID(result['id'])
                    if type(result['days']) is not int or not 1 <= result['days'] <= 3650:
                        raise ValueError('invalid service days')
                    cloud_time(result['expires_at'])
                    cloud_time(result['created_at'])
                except (CloudError, KeyError, TypeError, ValueError):
                    raise CloudError('redemption_result_unconfirmed') from None
                error = ''
                try:
                    entitlement = self._refresh_entitlement_locked()
                except (CloudError, OSError) as exc:
                    entitlement = None
                    error = exc.code if isinstance(exc, CloudError) else 'service_status_unavailable'
                try:
                    current = self.status()
                except (CloudError, OSError, ValueError):
                    current = None
                    error = error or 'service_status_unavailable'
                receipt = {key: result[key] for key in ('id', 'days', 'expires_at', 'created_at')}
                return {**receipt, 'redeemed': True, 'entitlement': entitlement,
                        'refresh_error': error, 'cloud': current}
        if action == 'logout':
            return self.logout()
        raise ValueError('unsupported cloud operation')
