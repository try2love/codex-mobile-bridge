#!/bin/sh
# Run the locally built app with its own persistent test data.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -m)" in
  x86_64) output=linux-unpacked ;;
  aarch64|arm64) output=linux-arm64-unpacked ;;
  *) printf '%s\n' 'This build supports Linux x64 and ARM64.' >&2; exit 1 ;;
esac
app="$root/dist/desktop/$output/codex-mobile-bridge"
if [ ! -x "$app" ]; then
  printf '%s\n' 'Build the gateway and desktop first; see docs/linux.md.' >&2
  exit 1
fi
umask 077
export CMB_DATA_DIR="${CMB_DATA_DIR:-$root/.local/linux-preview}"
mkdir -p "$CMB_DATA_DIR"
exec "$app" "$@" >>"$CMB_DATA_DIR/desktop.log" 2>&1
