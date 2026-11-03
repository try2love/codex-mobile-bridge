"""Launch the signed starter, pass only the new App ends of anonymous pipes."""

from array import array
import contextlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import socket
import subprocess
import sys
import tempfile


def starter_path():
    if sys.platform != "darwin" or not getattr(sys, "frozen", False):
        return None
    binary = Path(sys.executable).resolve()
    if binary.name != "codex-mobile-gateway" or tuple(binary.parent.parts[-3:]) != (
        "Contents",
        "Resources",
        "gateway",
    ):
        return None
    return binary.parents[2] / "Helpers" / "CMBZCodePipeStarter.app"


def launch(application, signature):
    starter = starter_path()
    if starter is None:
        raise ValueError("ZCode 私有启动组件不可用")
    signature.verify_starter(starter)
    # macOS per-user temp directory is the location independently checked by the starter.
    root = subprocess.check_output(
        ["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], text=True, timeout=3
    ).strip()
    with tempfile.TemporaryDirectory(prefix="cmb-zpipe-", dir=str(Path(root).resolve())) as temp:
        directory = Path(temp)
        record = directory / "launch.json"
        nonce = secrets.token_hex(32)
        with os.fdopen(os.open(record, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as output:
            json.dump({"target": "zcode", "application": str(application), "nonce": nonce}, output)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            endpoint = directory / "s"
            listener.bind(str(endpoint))
            os.chmod(endpoint, 0o600)
            listener.listen(1)
            listener.settimeout(9)
            subprocess.run(
                [
                    "/usr/bin/open",
                    "-g",
                    "-j",
                    "-a",
                    str(starter),
                    "--env",
                    "CMB_ZCODE_LAUNCH_REQUEST=" + str(record),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=8,
            )
            with listener.accept()[0] as peer:
                peer.settimeout(3)
                identity = signature.require(peer, None, starter=starter)
                expect = ("CMB-ZPIPE/1 " + nonce + "\n").encode()
                proof = bytearray()
                while len(proof) < len(expect):
                    data = peer.recv(len(expect) - len(proof))
                    if not data:
                        raise EOFError("ZCode starter disconnected")
                    proof.extend(data)
                if bytes(proof) != expect or record.exists():
                    raise PermissionError("ZCode starter request mismatch")
                with contextlib.ExitStack() as own, contextlib.ExitStack() as child:
                    app_in, writer = os.pipe()
                    child.callback(os.close, app_in)
                    own.callback(os.close, writer)
                    reader, app_out = os.pipe()
                    own.callback(os.close, reader)
                    child.callback(os.close, app_out)
                    sent = peer.sendmsg(
                        [b"F"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [app_in, app_out]))]
                    )
                    if sent != 1:
                        raise OSError("Could not transfer ZCode channels")
                    # After SCM_RIGHTS succeeds, lifetime responsibility belongs to the caller.
                    # It must retain these sentinels even if exec/handshake is slow or fails.
                    own.pop_all()
                    return identity["pid"], writer, reader


def is_supported(application):
    bundle = starter_path()
    if bundle is None or not (bundle / "Contents/MacOS/CMBZCodePipeStarter").is_file():
        return False
    try:
        info = plistlib.loads((application / "Contents/Info.plist").read_bytes())
        return (
            info.get("CFBundleIdentifier") == "dev.zcode.app"
            and info.get("CFBundleShortVersionString") == "3.17.2"
        )
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False
