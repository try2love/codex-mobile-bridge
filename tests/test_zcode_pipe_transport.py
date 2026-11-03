"""A disposable CDP pipe peer. It consumes no model, credentials or network."""

import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from bridge.clients.zcode.pipe_rpc import PipeTransport


@contextmanager
def peer(handler):
    r, w = os.pipe()
    rr, ww = os.pipe()
    transport = PipeTransport(w, rr, "/fixture/ZCode.app")
    calls = []
    errors = []

    def run():
        try:
            with os.fdopen(r, "rb", buffering=0) as source, os.fdopen(ww, "wb", buffering=0) as dest:
                buf = b""
                while True:
                    raw = source.read(65536)
                    if not raw:
                        break
                    buf += raw
                    while b"\0" in buf:
                        req, buf = buf.split(b"\0", 1)
                        msg = json.loads(req)
                        calls.append(msg)
                        for response in handler(transport, msg):
                            dest.write(json.dumps(response).encode() + b"\0")
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        yield transport, calls
    finally:
        os.close(w)
        thread.join(1)
        os.close(rr)
        assert not thread.is_alive() and not errors, errors


def normal(transport, msg):
    result = {}
    method = msg["method"]
    s = msg.get("sessionId")
    if method == "Target.getTargets":
        result = {
            "targetInfos": [
                {"type": "page", "targetId": "wrong", "url": "https://example.invalid/"},
                {"type": "page", "targetId": "exact", "url": transport.window_url + "?x=1"},
            ]
        }
    elif method == "Target.attachToTarget":
        assert msg["params"] == {"targetId": "exact", "flatten": True}
        result = {"sessionId": "target-session"}
    elif method == "Runtime.evaluate":
        assert s == "target-session" and "location.href" in msg["params"]["expression"]
        result = {"result": {"value": {"ready": True}}}
        yield {"id": msg["id"], "sessionId": "unrelated", "result": {"result": {"value": False}}}
    elif method == "Target.detachFromTarget":
        assert msg["params"] == {"sessionId": "target-session"} and s is None
    else:
        raise AssertionError(method)
    yield {"id": msg["id"], **({"sessionId": s} if s else {}), "result": result}


@unittest.skipUnless(os.name == "posix", "POSIX byte pipe fixture")
class PipeTests(unittest.TestCase):
    def test_parallel_calls_serialize_and_filter_foreign_sessions(self):
        with peer(normal) as (t, seen), ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(lambda _: t.probe(), range(4))), [{"ready": True}] * 4)
            self.assertEqual(
                [m["method"] for m in seen],
                [
                    "Target.getTargets",
                    "Target.attachToTarget",
                    "Runtime.evaluate",
                    "Target.detachFromTarget",
                ]
                * 4,
            )
            self.assertEqual(len({m["id"] for m in seen}), len(seen))

    def test_only_explicit_attach_allows_a_slow_first_native_snapshot(self):
        with peer(normal) as (t, _):
            def budget(request, deadline):
                return deadline - time.monotonic(), request['expiresAt'] / 1000 - time.time()
            with patch.object(t, '_with_window', side_effect=budget):
                for action, expected in [('attach', 25), ('send', 12), ('detail', 12)]:
                    for limit in t.call({'action': action}):
                        self.assertLessEqual(limit, expected + .01)
                        self.assertGreater(limit, expected - 1)
                for limit in t.call({'action': 'attach', 'expiresAt': int(time.time()*1000) + 2000}):
                    self.assertLessEqual(limit, 2)
                    self.assertGreater(limit, 1)

    def test_wrong_and_duplicate_targets_never_attach(self):
        for count in (0, 2):

            def target(t, m):
                yield {
                    "id": m["id"],
                    "result": {
                        "targetInfos": [{"type": "page", "url": t.window_url, "targetId": "m"}] * count
                    },
                }

            with peer(target) as (t, seen):
                with self.assertRaises(ValueError):
                    t.probe()
                self.assertEqual(len(seen), 1)

    def test_timed_out_write_is_not_replayed_and_late_response_is_filtered(self):
        delayed = []

        def delay(t, m):
            if m["method"] == "Runtime.evaluate" and not delayed:
                delayed.extend(normal(t, m))
                return
            if m["method"] == "Target.detachFromTarget" and delayed:
                yield from delayed[:]
            yield from normal(t, m)

        with peer(delay) as (t, seen):
            with self.assertRaises(TimeoutError):
                t.call({"action": "send", "expiresAt": int(time.time() * 1000) + 120})
            self.assertEqual(t.probe(), {"ready": True})
            self.assertEqual(sum(m["method"] == "Runtime.evaluate" for m in seen), 2)

    def test_no_generic_runtime_requests(self):
        with peer(normal) as (t, seen):
            with self.assertRaises(ValueError):
                t.call({"action": "Runtime.evaluate", "expression": "ignored"})
            self.assertFalse(seen)


if __name__ == "__main__":
    unittest.main()
