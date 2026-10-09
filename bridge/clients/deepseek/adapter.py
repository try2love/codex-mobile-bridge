"""Attach to the existing Harness Desktop host; never start another runtime."""
import json
import os
import re
import secrets
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from bridge.features.accounts.models import NoRedirect
from bridge.app.lifecycle import private_json
from bridge.clients.errors import BridgeUnavailable
from bridge.clients.deepseek.setup import MARKER, insertion, verified_directory, legacy_account_configured

PLUGIN_ID = re.compile(r'''["']?id["']?\s*:\s*["']?codex-mobile-desktop(?:["'\s,}]|$)''')
BRIDGE_REVISION = 4
UPDATE_REASON = 'Harness 接入需要更新，请结束任务后点击“重启并接入”'


def _write_patch(patch, text):
    patch = patch.resolve()
    temp = patch.with_suffix('.mobile.tmp')
    temp.write_text(text, encoding='utf-8')
    temp.chmod(patch.stat().st_mode & 0o777)
    temp.replace(patch)


class DeepSeek:
    def __init__(self, directory, home=None):
        self.directory = Path(directory).expanduser().resolve()
        self.home = Path(home or os.environ.get('DSH_HOME', '').strip() or Path.home()/'.dsh').expanduser().resolve()
        self.reused = False
        self.legacy_protocol = False
        self.receipts_path = None

    def install(self):
        return self.ensure_installed()

    def _line(self):
        entry = {'insert': [{'id': 'codex-mobile-desktop', 'name': (self.directory/'mobile-host.mjs').as_uri(),
                             'config': {'configPath': str(self.directory/'connection.json')}}]}
        return '- '+json.dumps(entry, ensure_ascii=False)

    def installation_status(self):
        patch = self.home/'profiles/desktop/cordis.patch.yml'
        text = patch.read_text('utf-8') if patch.is_file() else ''
        try:
            block = insertion(text)
        except ValueError:
            block = None
        owned = bool(block and block[0] == json.loads(self._line()[2:]) and len(PLUGIN_ID.findall(text)) == 1)
        return {'installed': owned, 'desktopProfileReady': patch.is_file(),
                'conflict': bool(MARKER in text or PLUGIN_ID.search(text)) and not owned}

    def discover_existing(self):
        """Inspect a desktop profile without installing, launching or rewriting it."""
        patch = self.home/'profiles/desktop/cordis.patch.yml'
        if not patch.is_file():
            return {'installed': False, 'desktopProfileReady': False, 'updateRequired': False}
        text = patch.read_text('utf-8')
        if MARKER not in text and not PLUGIN_ID.search(text):
            return {'installed': False, 'desktopProfileReady': True, 'updateRequired': False}
        return {**self.use_existing(), 'desktopProfileReady': True}

    def use_existing(self, directory=None):
        """Restore a verified previous Bridge connector without taking it over."""
        text = (self.home/'profiles/desktop/cordis.patch.yml').read_text('utf-8')
        block = insertion(text)
        verified = verified_directory(block[0], self.home) if block and len(PLUGIN_ID.findall(text)) == 1 else None
        if not verified or (directory is not None and Path(directory).resolve() != verified[0]):
            raise ValueError('已有 Harness 接入无法验证，尚未修改配置')
        self.directory, self.legacy_protocol = verified
        self.reused = True
        self.receipts_path = self.directory.parent/'requests.sqlite'
        return {'installed': True, 'changed': False, 'restartRequired': self.legacy_protocol,
                'updateRequired': self.legacy_protocol, 'reused': True,
                'sourceDirectory': str(self.directory), 'protocolReady': True, 'legacyProtocol': self.legacy_protocol}

    def update_existing(self):
        """Update a verified reused connector only for a confirmed restart.

        The owning gateway's source location, token and durable receipts stay in
        place. Discovery may update a stopped app; a running app must pass the
        manager's idle check and exit first. Unknown sources are never replaced.
        """
        self.use_existing(self.directory)
        target = self.directory/'mobile-host.mjs'
        content = Path(__file__).with_name('host.mjs').read_bytes()
        changed = target.read_bytes() != content
        if changed:
            temp = target.with_suffix('.tmp')
            temp.write_bytes(content)
            temp.chmod(0o600)
            temp.replace(target)
        self.legacy_protocol = False
        return {'installed': True, 'changed': changed, 'restartRequired': True,
                'reused': True, 'sourceDirectory': str(self.directory)}

    def ensure_installed(self):
        """Attach to the user's existing desktop profile, without starting a Host.

        Repeated scans do not rewrite files or rotate an active connector token.
        A source update requires a normal desktop restart; it never restarts an
        active desktop from this configuration operation.
        """
        patch = self.home/'profiles/desktop/cordis.patch.yml'
        if not patch.is_file():
            raise ValueError('未找到 Harness 桌面配置，请先启动一次 DeepSeek Harness，或选择正确的数据目录')
        text = patch.read_text('utf-8')
        line = self._line()
        block = insertion(text)
        owned = bool(block and block[0] == json.loads(line[2:]) and len(PLUGIN_ID.findall(text)) == 1)
        verified = verified_directory(block[0], self.home) if block and len(PLUGIN_ID.findall(text)) == 1 else None
        source = Path(__file__).with_name('host.mjs')
        # A previous preview may own an active Host. Reuse its verified transport
        # and receipt ledger without replacing its loaded source or credentials.
        if verified and (not owned or self.reused or verified[1] or
                         (verified[0]/'mobile-host.mjs').read_bytes() != source.read_bytes()):
            return self.use_existing()
        if (MARKER in text or PLUGIN_ID.search(text)) and not verified:
            raise ValueError('已有 Harness 接入无法验证，尚未修改配置')
        source = Path(__file__).with_name('host.mjs')
        target = self.directory/'mobile-host.mjs'
        content = source.read_bytes()
        source_changed = not target.is_file() or target.read_bytes() != content
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = self.directory/'connection.json'
        config_changed = not config.exists()
        if not config.exists():
            private_json(config, {'token': secrets.token_urlsafe(32), 'endpoint': str(self.directory/'endpoint.json')})
        else:
            saved = json.loads(config.read_text('utf-8'))
            if not isinstance(saved.get('token'), str) or not saved['token'] or saved.get('endpoint') != str(self.directory/'endpoint.json'):
                raise ValueError('Harness 接入配置无效，请先移除接入后重新扫描')
        if source_changed:
            temp = target.with_suffix('.tmp')
            temp.write_bytes(content)
            temp.chmod(0o600)
            temp.replace(target)
        if not owned:
            backup = self.directory/'cordis.patch.before-mobile.yml'
            if not backup.exists():
                shutil.copyfile(patch, backup)
                backup.chmod(0o600)
            text += '\n'+MARKER+'\n'+line+'\n'
            _write_patch(patch, text)
        record = self.directory/'installation.json'
        if not record.exists() or not owned:
            private_json(record, {'home': str(self.home), 'line': line})
        changed = not owned or source_changed or config_changed
        return {'installed': True, 'changed': changed, 'restartRequired': changed}

    def uninstall(self):
        if self.reused:
            raise ValueError('当前使用已有 Harness 接入，未移除原接入配置')
        record = self.directory/'installation.json'
        if not record.exists():
            return {'installed': False}
        saved = json.loads(record.read_text())
        patch = Path(saved['home'])/'profiles/desktop/cordis.patch.yml'
        text = patch.read_text('utf-8')
        block = insertion(text)
        if not block or block[0] != json.loads(saved['line'][2:]) or len(PLUGIN_ID.findall(text)) != 1:
            raise ValueError('插件配置已被修改，请在 Harness 中检查后移除')
        _write_patch(patch, text[:block[1]]+text[block[2]:])
        # Invalidate the current generation immediately; reload removes the plugin.
        config = self.directory/'connection.json'
        private_json(config, {'token': secrets.token_urlsafe(32), 'endpoint': str(self.directory/'endpoint.json')})
        record.unlink()
        return {'installed': False, 'restartRequired': True}

    def call(self, action, sid=None, body=None):
        try:
            config = json.loads((self.directory/'connection.json').read_text())
            endpoint = json.loads((self.directory/'endpoint.json').read_text())
            port = endpoint['port']
            if not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError('Harness 端口无效')
            request = urllib.request.Request(f'http://127.0.0.1:{port}/mobile', data=json.dumps({'action': action, 'sid': sid, 'body': body or {}}).encode(), headers={'Authorization': 'Bearer '+config['token'], 'Content-Type': 'application/json'})
            with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(32*1024*1024+1)
            if len(raw) > 32*1024*1024:
                raise BridgeUnavailable('会话内容过大，请在桌面查看')
            value = json.loads(raw)
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read(4096)).get('error', 'Harness 拒绝了操作')
            except ValueError:
                message = 'Harness 拒绝了操作'
            raise BridgeUnavailable(message) from exc
        except (OSError, KeyError) as exc:
            raise BridgeUnavailable('DeepSeek 桌面未连接，请安装接入插件并重新打开 Harness') from exc
        if action == 'detail':
            records = value.pop('records', [])
            value['messages'] = messages(records)
            value['turns'] = turns(records)
            if not isinstance(value.get('capabilities'), dict):
                # Older connectors still provide readable history. Do not imply
                # their old message protocol supports the new composer controls.
                value['capabilities'] = {name: False for name in (
                    'models', 'effort', 'permissions', 'attachments', 'skills', 'queue', 'steer', 'send')}
                value['notice'] = UPDATE_REASON
        if action == 'status' and self.legacy_protocol and value.get('connected') and 'configured' not in value:
            configured = False
            if legacy_account_configured(self.home):
                rows = self.call('list').get('sessions', [])
                sid = next((row.get('id') for row in rows if row.get('id')), None)
                if sid:
                    catalog = self.call('catalog', sid, {'section': 'models'})
                    configured = any(str(model.get('id', '')).startswith('deepseek-account/') for model in catalog.get('models', []))
            value.update(configured=configured, configurationEvidence='stored-account-and-catalog' if configured else 'unknown',
                         protocolReady=True, legacyProtocol=True, reused=self.reused)
        if action == 'status':
            if self.legacy_protocol and value.get('bridgeRevision') == BRIDGE_REVISION:
                # Another gateway may have safely updated the shared source and
                # restarted this host since this adapter was constructed. Recheck
                # ownership and the on-disk source before clearing stale state.
                self.use_existing(self.directory)
            value['updateRequired'] = self.legacy_protocol or value.get('bridgeRevision') != BRIDGE_REVISION
        return value


def turns(records):
    """Project native turn boundaries; an idle session is never completion evidence."""
    result = {}
    endings = {'completed', 'aborted', 'blocked', 'error', 'max-tokens', 'interrupted', 'forked'}
    for record in records:
        event = record.get('event', {})
        kind, data, sequence = event.get('type'), event.get('data') or {}, event.get('seq')
        if kind not in ('turn/start', 'turn/end'):
            continue
        turn = data.get('turn')
        if type(turn) is not int or turn < 0 or type(sequence) is not int or sequence < 0:
            continue
        identifier = str(turn)
        if identifier in result and result[identifier]['sequence'] >= sequence:
            continue
        value = {'turnId': identifier, 'status': 'inProgress', 'sequence': sequence}
        if kind == 'turn/end':
            reason = data.get('reason')
            ending = reason.get('kind') if isinstance(reason, dict) else None
            if ending not in endings:
                continue
            value.update(status='completed' if ending == 'completed' else 'failed', endReason=ending)
        result[identifier] = value
    return sorted(result.values(), key=lambda value: value['sequence'])


def messages(records):
    result = []
    for item in records:
        event = item.get('event', {})
        data = event.get('data') or {}
        kind = event.get('type')
        if kind in ('user/message', 'assistant/message'):
            content = data.get('message', data).get('content', [])
            if isinstance(content, str):
                content = [{'type': 'text', 'text': content}]
            for i, block in enumerate(content):
                text = block.get('text') or block.get('thinking') or ('[图片]' if block.get('type') == 'image' else '')
                if text:
                    result.append({'id': str(event.get('seq'))+':'+str(i), 'role': 'reasoning' if block.get('type') in ('thinking', 'reasoning') else kind.split('/')[0], 'text': text})
        elif kind in ('tool/call', 'tool/result', 'todo/write'):
            result.append({'id': str(event.get('seq')), 'role': 'activity', 'title': data.get('name') or data.get('toolName') or kind, 'text': json.dumps(data, ensure_ascii=False, indent=2)})
    return result
