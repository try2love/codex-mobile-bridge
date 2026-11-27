#!/usr/bin/env python3
"""Stage the shared, repository-owned phone scripts for DevEco; no SDK install."""
import argparse
from pathlib import Path
import shutil
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('channel-link.js', 'mobile-ui.js', 'mobile-clipboard.js', 'mobile-system-bars.js')


def prepare(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in SCRIPTS:
        shutil.copyfile(ROOT / 'mobile/shared/web' / name, destination / name)
    shutil.copyfile(ROOT / 'mobile/shared/web/mobile-offline.js', destination / 'mobile-offline.js')
    spec = importlib.util.spec_from_file_location('harmony_offline_shell', ROOT / 'scripts/build-offline-shell.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build(destination / 'offline-shell')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', type=Path,
                        default=ROOT / 'mobile/harmony/entry/src/main/resources/rawfile/generated')
    prepare(parser.parse_args().destination)
