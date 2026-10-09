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
        if provider not in ('claude', 'deepseek') or not isinstance(sid, str) or not sid or len(sid) > 512:
            raise ValueError('会话标识无效')
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'desktop-attachment:'+provider+':'+sid))

    def resolve(self, provider, sid, identifiers):
        rows = self.store.resolve(self.scope(provider, sid), identifiers)
        if any(not row.get('image') for row in rows):
            raise ValueError('此应用的消息附件仅支持图片；其他文件请使用文件传输。')
        return [{'url': 'data:'+row['image']+';base64,'+base64.b64encode(Path(row['localPath']).read_bytes()).decode('ascii'),
                 'name': row['name']} for row in rows]
