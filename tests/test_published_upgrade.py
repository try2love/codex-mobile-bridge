"""Provenance failures must stop before downloading or installing packages."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('upgrade_prepare', ROOT/'scripts/prepare-upgrade-acceptance.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PublishedUpgradeProvenanceTests(unittest.TestCase):
    def rejected(self, message, *, source='a'*40, head=None, version='2.2.2-preview.1', changed='', conclusion='success'):
        run = {'head_sha': head or source, 'path': '.github/workflows/desktop.yml',
               'status': 'completed', 'conclusion': conclusion}
        with patch.dict(os.environ, {'SOURCE_REF': source, 'CANDIDATE_RUN_ID': '123'}), \
                patch.object(prepare.subprocess, 'run') as ancestry, \
                patch.object(prepare.subprocess, 'check_output', side_effect=[json.dumps({'version': version}).encode(), changed]), \
                patch.object(prepare, 'api', return_value=run), \
                patch.object(prepare.urllib.request, 'urlretrieve') as download:
            with self.assertRaisesRegex(SystemExit, message):
                prepare.main()
            download.assert_not_called()
            if len(source) == 40:
                ancestry.assert_called_once_with(['git', 'merge-base', '--is-ancestor', prepare.BASE, source], check=True)

    def test_branch_name_is_not_an_immutable_source(self):
        self.rejected('exact 40-character', source='release/v2.2.2-preview')

    def test_unrelated_run_head_is_rejected(self):
        self.rejected('successful Desktop builds', head='b'*40)

    def test_failed_build_cannot_supply_acceptance_artifacts(self):
        self.rejected('successful Desktop builds', conclusion='failure')

    def test_wrong_product_version_is_rejected(self):
        self.rejected('Unexpected frozen product version', version='2.2.1')

    def test_unbuilt_product_changes_are_rejected(self):
        self.rejected('differs from product', changed='bridge/app/service.py\n')
