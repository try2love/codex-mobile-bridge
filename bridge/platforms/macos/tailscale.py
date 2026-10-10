"""Known macOS Tailscale CLI installation locations."""
from pathlib import Path


def candidates():
    return [Path('/Applications/Tailscale.app/Contents/MacOS/Tailscale'),
            Path.home()/'Applications/Tailscale.app/Contents/MacOS/Tailscale',
            Path('/opt/homebrew/bin/tailscale'), Path('/usr/local/bin/tailscale')]
