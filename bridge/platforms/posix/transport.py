"""Unix-domain byte streams shared by macOS and Linux."""
import socket


def connect_stream(path, timeout=5):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.settimeout(None)
        return sock
    except OSError:
        sock.close()
        raise
