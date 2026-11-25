"""Verify the bundled plugin against an explicitly selected local source checkout."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def verify(source, root=ROOT):
    lock = json.loads((root/'bridge/clients/deepseek/plugin-source.json').read_text())
    commit = lock['commit']
    def original(name):
        return subprocess.check_output(['git', '-C', str(source), 'show', commit+':'+name])
    for name, target in lock['files'].items():
        if (root/target).read_bytes() != original(name):
            raise ValueError('DSH plugin source drift: '+target)
    upstream = json.loads(original('package.json'))
    bundled = json.loads((root/'bridge/clients/deepseek/plugin/package.json').read_text())
    for field in lock['manifestFields']:
        if upstream.get(field) != bundled.get(field):
            raise ValueError('DSH plugin manifest drift: '+field)
    for field, change in lock.get('manifestOverrides', {}).items():
        if (field in lock['manifestFields'] or upstream.get(field) != change['upstream'] or
                bundled.get(field) != change['bundled']):
            raise ValueError('DSH plugin reviewed manifest override drift: '+field)
    return commit


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-repo', type=Path, required=True)
    args = parser.parse_args()
    print('DSH bundled source matches independent repository commit '+verify(args.plugin_repo))
