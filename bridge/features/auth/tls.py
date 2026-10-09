"""Verified HTTPS with portable roots for the bundled Python runtime."""
import os
import ssl
import sys
from pathlib import Path

BUNDLED_CA = Path(__file__).with_name('cacert.pem')


def client_context():
    # Keep system trust and explicit SSL_CERT_FILE/SSL_CERT_DIR configuration.
    context = ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    cafile = paths.cafile if os.environ.get(paths.openssl_cafile_env) else None
    capath = paths.capath if os.environ.get(paths.openssl_capath_env) else None
    if cafile or capath:
        # Some bundled/Apple SSL builds do not load these environment paths themselves.
        context.load_verify_locations(cafile=cafile, capath=capath)
    if getattr(sys, 'frozen', False):
        # A frozen OpenSSL may point at CA paths which exist only on the build host.
        # Missing/corrupt bundle data must fail closed, never bypass verification.
        context.load_verify_locations(cafile=str(BUNDLED_CA))
    return context
