import json
import os
import textwrap
from unittest.mock import patch
import runpy
from pathlib import Path
import unittest

scope = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/ci-test-scope.py'))


class SelectChecks(unittest.TestCase):
    def test_styles_skip_backend_and_native_builds(self):
        for path in ('web/features/chat/chat.css', 'web/hosts/mobile.css'):
            checks = scope['select_checks']([path])
            self.assertTrue(checks['browser'])
            for key in ('backend', 'frontend', 'android', 'ios', 'mobile_update'):
                self.assertFalse(checks[key], (path, key))

    def test_platform_builds_and_combined_changes(self):
        checks = scope['select_checks'](['mobile/android/src/Main.java', 'README.md'])
        self.assertTrue(checks['android'])
        self.assertFalse(checks['ios'])
        checks = scope['select_checks'](['mobile/ios/App.swift', 'bridge/api/httpd.py'])
        self.assertTrue(checks['ios'])
        self.assertTrue(checks['backend'])
        self.assertTrue(checks['browser'])

    def test_unknown_dependencies_ci_and_non_pr_keep_full_coverage(self):
        for path in ('new-source/file.xyz', 'package-lock.json', '.github/workflows/tests.yml', 'scripts/ci-test-scope.py', 'docs/architecture/dependency-exceptions.json', 'new-source/template.md'):
            self.assertTrue(all(scope['select_checks']([path]).values()))
        self.assertTrue(all(scope['select_checks'](['README.md'], full=True).values()))
        self.assertFalse(any(scope['select_checks'](['README.md']).values()))

    def test_gate_rejects_failed_cancelled_or_skipped_selected_jobs(self):
        selected = scope['select_checks'](['web/chat.css'])
        results = {key: 'success' if value or key == 'backend' else 'skipped' for key, value in selected.items()}
        scope['verify_results'](selected, results)
        for state in ('failure', 'cancelled', 'skipped', None):
            with self.assertRaises(ValueError):
                scope['verify_results'](selected, {**results, 'browser': state})
        with self.assertRaises(ValueError):
            scope['verify_results'](selected, {**results, 'ios': 'success'})

    def test_actual_workflow_gate_rejects_invalid_or_missing_scope(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/tests.yml').read_text()
        body = workflow.split("python3 - <<'PYTHON'\n", 1)[1].split('          PYTHON', 1)[0]
        code = compile(textwrap.dedent(body), 'workflow-gate', 'exec')
        jobs = {'backend': 'test', 'ui': 'ui-tests', 'frontend': 'frontend-tests',
                'browser': 'browser-tests', 'android': 'android-compile', 'ios': 'ios-compile',
                'mobile_update': 'mobile-update-tests'}
        needs = {name: {'result': 'skipped'} for name in jobs.values()}
        needs['test']['result'] = 'success'
        needs['change-scope'] = {'result': 'success', 'outputs': {key: 'false' for key in jobs}}
        def run():
            with patch.dict(os.environ, {'NEEDS_JSON': json.dumps(needs)}):
                exec(code, {})
        run()
        needs['change-scope']['outputs']['browser'] = 'true'
        with self.assertRaises(ValueError):
            run()
        needs['browser-tests']['result'] = 'success'
        run()
        needs['change-scope']['outputs']['browser'] = ''
        with self.assertRaises(SystemExit):
            run()
        needs['change-scope']['result'] = 'failure'
        with self.assertRaises(SystemExit):
            run()

    def test_required_backend_names_keep_matrix_but_skip_every_test_step(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/tests.yml').read_text()
        backend = workflow.split('  test:\n', 1)[1].split('  ui-tests:', 1)[0]
        self.assertIn('if: always()', backend)
        self.assertIn("outputs.backend == 'true' && matrix.os || 'ubuntu-latest'", backend)
        steps = backend.split('    steps:\n', 1)[1].split('      - ')[2:]  # first step only validates selection
        self.assertGreater(len(steps), 4)
        for step in steps:
            self.assertIn("if: needs.change-scope.outputs.backend == 'true'", step)
