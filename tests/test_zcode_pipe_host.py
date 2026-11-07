"""A gateway restart must not close the pipes of an active ZCode instance."""

import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bridge.clients.zcode import pipe_host as host

ROOT = Path(__file__).resolve().parents[1]


class ServiceBoundary(unittest.TestCase):
    def test_source_gateway_never_offers_managed_control(self):
        client = host.PipeHostClient("/unused", "/fictional/ZCode.app")
        self.assertFalse(client.supported())
        self.assertIsNone(client.owner())
        with self.assertRaises(ValueError):
            client.start()
        self.assertIsNone(client.signature)

    def test_moved_holder_must_be_the_same_product_and_exact_prior_broker(self):
        signature = host.SignedGatewayPeer.__new__(host.SignedGatewayPeer)
        layout = Path("Contents/Resources/gateway/codex-mobile-gateway")
        signature.image = str(Path("/Applications/Test.app") / layout)
        previous = Path("/Applications/previous.app")
        digest = b"d" * 20
        with patch("bridge.platforms.macos.signed_peer.subprocess.run") as codesign, \
                patch.object(signature, "_starter_requirement") as starter:
            codesign.return_value.stdout = ""
            codesign.return_value.stderr = 'designated => identifier "com.example.test"'
            signature._verify_previous_build(str(previous / layout), digest)
            self.assertEqual(codesign.call_args.args[0], [
                "/usr/bin/codesign", "--verify", "--strict", '-R=identifier "com.example.test"', str(previous)])
            starter.assert_called_once_with(previous / "Contents/Helpers/CMBZCodePipeStarter.app", digest)
            codesign.reset_mock()
            for bad in (Path("/tmp/previous.app") / layout, previous / "Contents/MacOS/codex-mobile-gateway"):
                with self.assertRaises(PermissionError):
                    signature._verify_previous_build(str(bad), digest)
            codesign.assert_not_called()

    @patch("bridge.platforms.macos.signed_peer.os", SimpleNamespace(getuid=lambda: 501))
    @patch("bridge.platforms.macos.signed_peer.socket", SimpleNamespace(AF_UNIX=1))
    def test_relocation_does_not_authorize_a_different_gateway_or_pid_generation(self):
        import struct

        signature = host.SignedGatewayPeer.__new__(host.SignedGatewayPeer)
        signature.image = "/Applications/Test.app/Contents/Resources/gateway/codex-mobile-gateway"
        signature.native = Mock()
        proc = {'pid': 321, 'uid': 501, 'zombie': False, 'birth': (9, 7), 'started': (1, 0),
                'image': '/Applications/previous.app/Contents/Resources/gateway/codex-mobile-gateway'}
        signature.native.identity.return_value = proc
        channel = Mock(family=1)
        channel.getsockopt.return_value = struct.pack("=8I", 0, 501, 0, 501, 0, 321, 0, 7)
        with self.assertRaisesRegex(PermissionError, "executable or process generation"):
            signature.require(channel, 321)
        relocated = Mock()
        proc['birth'] = (9, 8)
        with self.assertRaisesRegex(PermissionError, "executable or process generation"):
            signature.require(channel, 321, relocated_holder=relocated)
        relocated.assert_not_called()

    def test_only_supported_native_request_shapes_reach_the_pipe(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as tmp:
            holder = host.Holder(tmp, None, None)
            holder.owner = {"pid": 123}
            holder.transport = Mock()
            with patch.object(holder, "ensure_live"):
                for native in [
                    {"action": "Runtime.evaluate"},
                    {"action": "send", "expression": "untrusted"},
                ]:
                    with self.assertRaises(ValueError):
                        holder.handle({"command": "operation", "owner": holder.owner, "native": native})
                with self.assertRaises(ValueError):
                    holder.handle(
                        {"command": "operation", "owner": {"pid": 124}, "native": {"action": "send"}}
                    )
                holder.transport.call.assert_not_called()


# Only for the isolated process-lifetime test. The shipped entry point verifies both signers
# using the kernel peer audit token; this fixture exercises the real data-dir and FD lifecycle.
def fixture_holder(profile):
    root = Path(profile)
    app = root / "Empty.app"
    exe = app / "Contents/MacOS/ZCode"

    class SignedFixture:
        native = host.Native()

        def require(self, conn, pid):
            if conn.getsockopt(0, 2) != pid:
                raise PermissionError("unexpected test peer")
            return {"image": sys.executable, "pid": pid}

        def close(self):
            pass

    class TransportFixture:
        def __init__(self, w, r, app):
            self.w, self.r = w, r

        def call(self, req):
            if req["action"] == "history":
                raise subprocess.TimeoutExpired("fixture identity probe", 1)
            if req["action"] != "application":
                raise ValueError("fixture only")
            os.write(self.w, b"q")
            if not select.select([self.r], [], [], 1)[0] or os.read(self.r, 1) != b"q":
                raise ValueError("lost private pipe")
            return {"ready": True}

    def launch():
        r, w = os.pipe()
        rr, ww = os.pipe()
        proc = subprocess.Popen([str(exe), str(root / "finish")], stdin=r, stdout=ww)
        os.close(r)
        os.close(ww)
        return proc.pid, w, rr

    with patch.object(host, "SignedGatewayPeer", SignedFixture), patch.object(
        host, "PipeTransport", TransportFixture
    ), patch.object(host.zcode_pipe, "is_supported", return_value=True), patch.object(
        host,
        "commands",
        side_effect=lambda pids: {pids[0]: sys.executable + " serve --data-dir " + profile},
    ):
        host.serve(profile, app, launch)


def fixture_client(profile, action):
    root = Path(profile)
    record = host.read_json(root / "zcode-pipe-host.json")
    if action != "unauthorized":
        host.private_json(root / "gateway-control.json", {"pid": os.getpid()})
    with socket.socket(socket.AF_UNIX) as conn:
        conn.connect(record["endpoint"])
        if action == "timed-out-operation":
            req = {"command":"operation","owner":record["owner"],"native":{"action":"history"}}
        elif action == "authorize-and-disconnect":
            req = {"command": "operation", "owner": record["owner"], "native": {"action": "application"}}
        else:
            req = {"command": "status" if action == "unauthorized" else action}
        host.exchange_write(conn, {"nonce": record["nonce"], "request": req}, 1)
        if action == "authorize-and-disconnect":
            return
        try:
            reply = host.exchange_read(conn, time.monotonic() + 6)
        except ConnectionResetError:
            if action == "unauthorized":
                return
            raise
        if action in ("timed-out-operation", "unauthorized"):
            assert reply["ok"] is False
        else:
            assert reply["ok"], reply.get("error")
            print(json.dumps(reply["value"]))


@unittest.skipUnless(sys.platform == "darwin", "macOS kqueue and Unix peer PID")
class HolderLifetime(unittest.TestCase):
    def test_disconnected_client_and_new_gateway_keep_live_app(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".tmp") as folder:
            root = Path(folder)
            target = root / "Empty.app/Contents/MacOS/ZCode"
            target.parent.mkdir(parents=True)
            # This stub exits early on pipe EOF, just like Electron. It exits normally
            # when given a private test completion marker, independent of the gateway.
            source = b"""#include <poll.h>
#include <unistd.h>
int main(int argc,char** argv) {
 struct pollfd fd={0,POLLIN,0}; char c;
 for (int n=0;n<250;n++) {
   if (!access(argv[1],F_OK)) return 0;
   if (poll(&fd,1,50)>0 && (read(0,&c,1)!=1 || write(1,&c,1)!=1)) return 2;
 }
 return 3;
}"""
            subprocess.run(["/usr/bin/clang", "-x", "c", "-", "-o", str(target)], input=source, check=True)
            env = {**os.environ, "PYTHONPATH": str(ROOT)}
            fixture = [sys.executable, "-B", str(Path(__file__).resolve())]
            daemon = subprocess.Popen([*fixture, "fixture-holder", folder], env=env, stderr=subprocess.PIPE)

            def client(action):
                return subprocess.check_output(
                    [*fixture, "fixture-client", folder, action], env=env, timeout=5
                )

            try:
                deadline = time.monotonic() + 3
                while not (root / "zcode-pipe-host.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.03)
                if not (root / "zcode-pipe-host.json").exists():
                    self.fail(
                        "no daemon record: "
                        + (
                            daemon.stderr.read().decode()
                            if daemon.poll() is not None
                            else "startup timeout"
                        )
                    )
                first = json.loads(client("initialize"))
                client("authorize-and-disconnect")
                client("timed-out-operation")
                self.assertEqual(json.loads(client("status"))["owner"], first)
                client("unauthorized")
                self.assertEqual(json.loads(client("initialize")), first)
                self.assertIsNone(daemon.poll())
            finally:
                (root / "finish").touch()
                _, error = daemon.communicate(timeout=4)
            self.assertEqual(daemon.returncode, 0, error.decode(errors="replace"))
            self.assertFalse((root / "zcode-pipe-host.json").exists())


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "fixture-holder":
        fixture_holder(sys.argv[2])
    elif len(sys.argv) >= 4 and sys.argv[1] == "fixture-client":
        fixture_client(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
