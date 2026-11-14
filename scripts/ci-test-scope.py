#!/usr/bin/env python3
"""Select PR checks from paths; unknown paths and non-PR runs keep full coverage."""
import json
import os
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
changed_paths = runpy.run_path(str(ROOT / 'scripts/ci-mobile-scope.py'))['changed_paths']
CHECKS = ('backend', 'ui', 'frontend', 'browser', 'android', 'ios', 'mobile_update')


def select_checks(paths, full=False):
    selected = set(CHECKS) if full else set()
    for path in filter(None, paths):
        if path.endswith('.md') and (path.startswith(('docs/', '.agents/notes/')) or '/' not in path):
            continue
        if path.startswith('web/'):
            selected.update(('ui', 'browser'))
            if not path.endswith('.css'):
                selected.add('frontend')
            if path.startswith('web/client-icons/') or path.startswith('web/hosts/') and path.endswith('.js'):
                selected.update(('android', 'ios'))
            elif not path.endswith(('.css', '.js', '.html')):
                selected.update(CHECKS)  # Resource manifests and new asset types.
        elif path.startswith('desktop/'):
            selected.add('frontend')
            if not path.endswith(('.css', '.html')):
                selected.add('backend')  # IPC, packaging and runtime contracts.
        elif path.startswith('mobile/android/'):
            selected.update(('frontend', 'android', 'mobile_update'))
        elif path.startswith('mobile/ios/'):
            selected.update(('frontend', 'ios', 'mobile_update'))
        elif path.startswith('mobile/'):
            selected.update(('frontend', 'browser', 'android', 'ios', 'mobile_update'))
        elif path.startswith('bridge/'):
            selected.add('backend')
            if not path.startswith('bridge/platforms/'):
                selected.update(('frontend', 'browser'))
        elif path.startswith('tests/browser/'):
            selected.add('browser')
        elif path.startswith('tests/') and path.endswith(('.test.cjs', '.test.js', '.test.mjs')):
            selected.add('frontend')
        elif path.startswith(('tests/test_mobile', 'tests/test_ios', 'tests/test_android')):
            selected.update(CHECKS)
        elif path.startswith('tests/') and path.endswith('.py'):
            selected.add('backend')
        else:
            selected.update(CHECKS)  # CI, dependencies, scripts, contracts, unknowns.
    return {key: key in selected for key in CHECKS}


def verify_results(scope, results):
    """Selected jobs must succeed; backend keeps required matrix sentinels."""
    for key in CHECKS:
        expected = 'success' if scope[key] or key == 'backend' else 'skipped'
        if results.get(key) != expected:
            raise ValueError(f'{key}: expected {expected}, got {results.get(key)}')


def main():
    paths = changed_paths(ROOT, os.environ.get('BASE_SHA'), os.environ['HEAD_SHA'])
    scope = select_checks(paths, full=os.environ.get('EVENT_NAME') != 'pull_request')
    print(json.dumps(scope, indent=2))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, enabled in scope.items():
                output.write(f'{key}={str(enabled).lower()}\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write('## Selected checks\n\n' + '\n'.join(
                f'- {key}: {"run" if enabled else "skip (unaffected)"}' for key, enabled in scope.items()) + '\n')


if __name__ == '__main__':
    main()
