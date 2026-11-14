#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
APP_PATH="${1:-build/Build/Products/Release-iphoneos/CodexMobile.app}"
test -x "$APP_PATH/Helpers/TaskMonitor"

# TrollStore consumes ad-hoc signatures and their entitlements; no developer certificate is used.
/usr/bin/codesign --force --sign - --generate-entitlement-der \
  --entitlements TaskMonitor/TaskMonitor.entitlements "$APP_PATH/Helpers/TaskMonitor"
/usr/bin/codesign --force --sign - --generate-entitlement-der \
  --entitlements CodexMobile/TrollStore.entitlements "$APP_PATH"
/usr/bin/codesign --verify --deep --strict "$APP_PATH"
python3 - "$APP_PATH" <<'PY'
import pathlib
import plistlib
import subprocess
import sys

app = pathlib.Path(sys.argv[1])
for target, entitlements in (
    (app, pathlib.Path("CodexMobile/TrollStore.entitlements")),
    (app / "Helpers/TaskMonitor", pathlib.Path("TaskMonitor/TaskMonitor.entitlements")),
):
    signed = subprocess.check_output(["/usr/bin/codesign", "-d", "--entitlements", ":-", str(target)])
    actual = plistlib.loads(signed)
    expected = plistlib.loads(entitlements.read_bytes())
    # Verify the UI's graphics allowlists and the helper's permissions survive signing.
    if any(actual.get(key) != value for key, value in expected.items()):
        raise SystemExit(f"Missing TrollStore entitlements: {target}")
PY

mkdir -p package/Payload
/usr/bin/ditto "$APP_PATH" package/Payload/CodexMobile.app
cd package
# Preserve the download name used by existing workflows.
/usr/bin/ditto -c -k --keepParent Payload CodexMobile-unsigned.ipa
shasum -a 256 CodexMobile-unsigned.ipa > SHA256SUMS.txt
