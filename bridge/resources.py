"""Source and bundled data locations shared by entrypoints and SSH adapters.

PyInstaller keeps this module under <bundle>/bridge, preserving the same
package-relative layout as the source checkout. Callers supply trusted paths.
"""
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent


def project_root():
    """Repository root in source mode, resource root in a frozen runtime."""
    return PACKAGE_ROOT.parent


def source_text(relative_path):
    """Read an actual bridge source payload, not an import wrapper for SSH."""
    return (PACKAGE_ROOT / relative_path).read_text(encoding='utf-8')
