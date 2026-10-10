"""Known Linux Tailscale CLI installation locations."""
from pathlib import Path


def candidates():
    return [Path('/usr/bin/tailscale'), Path('/usr/local/bin/tailscale'), Path('/snap/bin/tailscale')]
