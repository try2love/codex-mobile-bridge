"""Private image attachments shared by the desktop conversation adapters."""
import base64
import uuid
from pathlib import Path

from bridge.features.workspace.uploads import Uploads


class DesktopUploads:
    def __init__(self, directory):
        self.store = Uploads(Path(directory)/'desktop-sessions')

    @staticmethod
    def scope(provider, sid):
        if provider not in ('claude', 'deepseek', 'zcode', 'antigravity') or not isinstance(sid, str) or not sid or len(sid) > 512:
            raise ValueError('会话标识无效')
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'desktop-attachment:'+provider+':'+sid))

    def resolve(self, provider, sid, identifiers, *, local_claude_cli=False, native_files=False):
        rows = self.store.resolve(self.scope(provider, sid), identifiers)
        if any(not row.get('image') for row in rows) and not (provider == 'claude' and local_claude_cli or provider in ('zcode', 'antigravity') and native_files):
            raise ValueError('此应用的消息附件仅支持图片；其他文件请使用文件传输。')
        images = [{'url': 'data:'+row['image']+';base64,'+base64.b64encode(Path(row['localPath']).read_bytes()).decode('ascii'),
                   'name': row['name'], **({'path': row['localPath']} if provider == 'antigravity' else {})}
                  for row in rows if row.get('image')]
        files = [{'id': row['id'], 'path': row['localPath'], 'name': row['name']} for row in rows if not row.get('image')]
        return images, files
