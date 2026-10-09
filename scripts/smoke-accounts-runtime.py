#!/usr/bin/env python3
"""Native config integration, isolated home, synthetic API key, no model turn."""
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from bridge.features.accounts.accounts import Accounts, ManagedRPC
from bridge.clients.codex.catalog import Catalog

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(dir=root/'.tmp') as directory:
    home = Path(directory)/'home'
    home.mkdir()
    original = b'# preserve this comment\nmodel = "old-model"\nforced_login_method = "chatgpt"\n[notice]\nhide_rate_limit_model_nudge = true\n'
    (home/'config.toml').write_bytes(original)
    runtime = Catalog.find_runtime()
    bridge = SimpleNamespace(codex_home=home, data_dir=Path(directory), catalog_reader=SimpleNamespace(executable=runtime), account=SimpleNamespace(lock=threading.Lock()), lock=threading.RLock(), live={}, remote_bridges={}, submissions={})
    accounts = Accounts(bridge)
    row = accounts.add_api({'name':'Synthetic API','baseUrl':'http://127.0.0.1:9/v1','apiKey':'synthetic-not-a-secret','model':'fixture-model'})['accounts'][0]
    prepared = accounts.prepare(row,accounts.snapshot_files())
    scanned = accounts.scan({'source': str(prepared)})['discovery']['candidates']
    candidate = next(row for row in scanned if row.get('name') == 'bridge_api')
    assert candidate['canImport']
    assert accounts.candidates[candidate['id']]['key'] == 'synthetic-not-a-secret'
    assert 'synthetic-not-a-secret' not in json.dumps(accounts.desktop_status())
    with ManagedRPC(prepared,runtime) as rpc:
        config = rpc.request('config/read',{'includeLayers':False})['config']
        assert config['model_provider']=='bridge_api'
        assert config['model']=='fixture-model'
        assert config['forced_login_method']=='api'
        assert config['openai_base_url']=='http://127.0.0.1:9/v1'
        assert rpc.request('account/read',{'refreshToken':False})['account']['type']=='apiKey'
        assert 'synthetic-not-a-secret' not in (prepared/'config.toml').read_text()
        assert config['model_providers']['bridge_api']['base_url']=='http://127.0.0.1:9/v1'
        result = rpc.request('config/batchWrite',{'edits':[{'keyPath':key,'value':val,'mergeStrategy':'replace'} for key,val in
                            [('model_provider','openai'),('model',None),('model_providers.bridge_api',None),('openai_base_url',None)]]})
        assert result['status']=='ok'
    assert (home/'config.toml').read_bytes()==original
    text=(prepared/'config.toml').read_text()
    assert '[notice]' in text and 'hide_rate_limit_model_nudge = true' in text
    assert 'synthetic-not-a-secret' not in text and 'bridge_api' not in text
    print('PASS: native provider scanning and writes/removals, unrelated setting preservation, original home unchanged; no login or inference.')
