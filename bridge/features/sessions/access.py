"""Explicit recovery of interrupted thread migrations from older preview builds."""
from bridge.features.accounts.account import AccountRPC


class ThreadAccessRPC(AccountRPC):
    METHODS = AccountRPC.METHODS | {'thread/resume', 'thread/unsubscribe'}


class ThreadAccess:
    def __init__(self, bridge):
        self.bridge = bridge

    def restore(self, records):
        if not records:
            return
        with ThreadAccessRPC(self.bridge.codex_home, self.bridge.catalog_reader.executable) as rpc:
            for record in records:
                selected, model, effort = record['provider'], record.get('model'), record.get('effort')
                params = {'threadId':record['id'], 'modelProvider':selected, 'excludeTurns':True}
                if model:
                    params['model'] = model
                if effort:
                    params['config'] = {'model_reasoning_effort':effort}
                result = rpc.request('thread/resume', params)
                if result.get('modelProvider', result.get('thread', {}).get('modelProvider')) != params['modelProvider']:
                    raise ValueError('旧聊天接入未能更新，请在电脑端检查')
                if model and result.get('model') != model:
                    raise ValueError('旧聊天模型未能更新，请在电脑端检查')
                rpc.request('thread/unsubscribe', {'threadId':record['id']})
