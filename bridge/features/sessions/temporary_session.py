"""Expose an existing ephemeral child through the shared session HTTP contract.

No fallback to the parent executor: unsupported persistent operations are rejected.
The parent contributes only workspace access and catalog settings.
"""
from types import SimpleNamespace

from bridge.features.workspace.files import artifact_paths, host_file_links
from bridge.features.sessions import message_management


class TemporarySession:
    capabilities = {'notifications': False}
    temporary = True
    actions = {None, 'timeline', 'detail', 'changes', 'poll', 'events', 'catalog',
               'send', 'settings', 'permissions', 'stop', 'respond', 'queue', 'uploads', 'notifications', 'message-management'}

    def __init__(self, bridge, chat):
        self.bridge, self.chat = bridge, chat
        self.host, self.host_label, self.closed = bridge.host, bridge.host_label, bridge.closed
        self.store = SimpleNamespace(get=self.view)

    def session(self, identifier, **options):
        if self.chat.id != identifier:
            raise ValueError('临时侧边聊天已结束，请返回主聊天')
        return self.chat

    def view(self, identifier, **options):
        chat = self.session(identifier)
        value = chat.view()
        value['submissions'] = [{**row, 'attachments': [item['name'] for item in row['attachments']]}
                                for row in value['submissions'] if row['status'] in ('queued', 'unknown')]
        writable = value['connected']
        return {**value, 'title': '临时侧边聊天', 'temporary': True, 'host': self.host,
                'draftAccountIdentity': 'temporary:' + identifier,
                'hostLabel': self.host_label, 'connectionError': value.get('error'),
                'attachedParent': {'id': chat.parent, 'host': self.host, 'title': '主聊天', 'available': True},
                'access': {'source': 'temporary', 'runtime': 'temporary', 'readOnly': not writable,
                    'canSend': writable, 'canInterrupt': writable, 'canApprove': writable,
                    'canReconnect': False, 'canConfigure': False, 'canChangePermissions': writable,
                    'canChangeModel': writable, 'canSelectSkills': writable, 'canUseModes': writable,
                    'canUseGoal': False, 'canUseSideChat': False, 'canEditMessages': False}}

    def timeline_read(self, identifier, mode='page', **options):
        chat = self.session(identifier)
        with chat.condition:
            view = self.view(identifier)
            # The standard timeline expects preview descriptors, not raw uploads.
            for turn in view['turns']:
                turn['actionable'] = False
                for row in turn['messages']:
                    row['attachments'] = [dict(a, type='image' if a.get('image') else 'file')
                                          for a in row.get('attachments', [])]
            chat.timeline.update(view)
            result = (chat.timeline.detail(**options) if mode == 'detail' else
                      chat.timeline.changes(**options) if mode == 'changes' else chat.timeline.page(**options))
            if mode != 'detail':
                # Reuse the same upload preview endpoint as the ordinary composer.
                uploads = {t['id']: next((m.get('attachments', []) for m in t['messages'] if m['role'] == 'user'), [])
                           for t in view['turns']}
                for row in result['rows']:
                    if row['role'] == 'user':
                        for item, original in zip(row.get('attachments', []), uploads.get(row['turnId'], [])):
                            if original.get('image') and original.get('id'):
                                item.update(uploadId=original['id'], mime=original['image'], thumb=original.get('thumb'))
            artifacts = artifact_paths(chat.state, self.bridge.store.home)
            result['files'] = host_file_links(chat.state, [dict(id=key, **{k: value[k] for k in
                ('name', 'reference', 'references', 'image')}) for key, value in artifacts.items()])
            return result

    def _operate(self, identifier, action, **body):
        chat = self.session(identifier)
        try:
            return self.bridge.side_chats.operate(chat.parent, action, {'id': identifier, **body},
                                                 catalog_reader=self.bridge.catalog_reader)
        finally:
            if action not in ('catalog', 'skills', 'linux-permission-options'):
                chat.changed()

    def catalog(self, identifier, refresh=False, kind=None, query='', offset=0, limit=200, ids=None):
        if kind == 'skills':
            return self._operate(identifier, 'skills', query=query, offset=offset, refresh=refresh, selected=ids or [])
        if kind not in (None, 'models'):
            raise ValueError('无效目录类型')
        return self._operate(identifier, 'catalog', refresh=refresh)

    def send(self, identifier, text, submission_id, mode='send', skills=None, *, work_mode=None,
             attachments=None, references=None, ui_locale=None):
        return self._operate(identifier, 'send', text=text, submissionId=submission_id, mode=mode,
            skills=skills or [], workMode=work_mode if mode == 'steer' else work_mode or 'default',
            attachments=attachments or [], references=references)

    def settings(self, identifier, model, effort, **options):
        if options:
            raise ValueError('临时侧边聊天暂不支持此设置')
        return {**self._operate(identifier, 'settings', model=model, effort=effort), 'confirmed': True}

    def permissions(self, identifier, preset, confirmed=False, **options):
        return {**self._operate(identifier, 'permissions', preset=preset, confirmed=confirmed), 'confirmed': True}

    def linux_permission_options(self, identifier):
        return self._operate(identifier, 'linux-permission-options')

    def interrupt(self, identifier):
        return self._operate(identifier, 'stop')

    def respond(self, identifier, request_id, response):
        return self._operate(identifier, 'respond', requestId=request_id, response=response)

    def cancel_queued(self, identifier, submission_id):
        return self._operate(identifier, 'cancel-queued', submissionId=submission_id)

    def _receipts(self):
        return [(key, {**{k: row[k] for k in ('status', 'text', 'mode', 'attachments', '_editId', '_managementRevision') if k in row},
                       'attachmentNames': [item['name'] for item in row.get('attachments', [])]})
                for key, row in self.chat.submissions.items()]

    def message_management(self, identifier, receipt_ids=()):
        chat = self.session(identifier)
        with chat.actions, chat.lock:
            return {**message_management.snapshot(self._receipts(), receipt_ids, scope=identifier),
                    'draftAccountIdentity': 'temporary:' + identifier}

    def manage_messages(self, identifier, body):
        chat = self.session(identifier)
        with chat.actions, chat.lock:
            body = dict(body)
            if body.pop('expectedAccount', None) != 'temporary:' + identifier:
                raise ValueError('临时侧聊已变化，请重新打开消息管理')
            rows, result = message_management.mutate(self._receipts(), body, scope=identifier)
            chat.submissions = {key: {**chat.submissions[key], **entry} for key, entry in rows}
            chat.changed()
            return {**message_management.snapshot(self._receipts(), scope=identifier), **result,
                    'draftAccountIdentity': 'temporary:' + identifier}

    def upload(self, identifier, upload_id, name, data):
        chat = self.session(identifier)
        return self.bridge.side_chats.attachment(chat.parent, identifier, 'put', upload_id, name, data)

    def upload_thumb(self, identifier, upload_id, data, width, height):
        chat = self.session(identifier)
        return self.bridge.side_chats.attachment(chat.parent, identifier, 'set_thumb', upload_id, data, width, height)

    def upload_preview(self, identifier, upload_id, variant='thumb'):
        chat = self.session(identifier)
        return self.bridge.side_chats.attachment(chat.parent, identifier, 'preview', upload_id, variant)

    def artifact(self, identifier, artifact_id):
        chat = self.session(identifier)
        with chat.lock:
            files = artifact_paths(chat.state, self.bridge.store.home)
            if artifact_id not in files:
                raise KeyError('文件不属于此聊天的工作目录')
            return files[artifact_id]
