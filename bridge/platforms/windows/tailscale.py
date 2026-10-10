"""Known Windows Tailscale CLI installation locations."""
import os
from pathlib import Path


def candidates():
    return [Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'Tailscale/tailscale.exe']
