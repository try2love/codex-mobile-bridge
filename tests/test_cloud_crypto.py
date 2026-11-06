import base64
import json
import unittest
from pathlib import Path

from bridge.features.network import cloud_crypto as crypto


class CloudCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import noise.connection  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("requires requirements-relay.txt")
        cls.v = json.loads((Path(__file__).parent / "fixtures/cloud_vectors.json").read_text())

    def field(self, name):
        return base64.b64decode(self.v[name])

    def cipher(self, prologue=None):
        v = self.v
        p = crypto.control_prologue(v["origin"], v["gateway_id"], v["binding_id"], v["channel_id"],
                                    self.field("desktop_public"), self.field("mobile_public"))
        self.assertEqual(p, self.field("control_prologue"))
        return crypto.ControlCipher(self.field("desktop_private"), self.field("mobile_public"),
                                     prologue or p, _test_ephemeral=self.field("desktop_ephemeral"))

    def test_bidirectional_vectors_and_replay(self):
        c = self.cipher()
        c.handshake_read(self.field("kk_first"))
        self.assertEqual(c.handshake_write(), self.field("kk_second"))
        self.assertEqual(c.encrypt(b'{"kind":"ready"}'), self.field("desktop_record"))
        self.assertEqual(c.decrypt(self.field("mobile_record")), b'{"kind":"key_confirm"}')
        with self.assertRaises(ValueError):
            c.decrypt(self.field("mobile_record"))
        with self.assertRaises(ValueError):
            c.encrypt(b"after failure")

    def test_no_early_data_and_cross_binding(self):
        with self.assertRaises(ValueError):
            self.cipher().encrypt(b"business before approval")
        wrong = bytearray(self.field("control_prologue")); wrong[-1] ^= 1
        with self.assertRaises(ValueError):
            self.cipher(bytes(wrong)).handshake_read(self.field("kk_first"))

    def test_authenticated_preview_metadata(self):
        e = self.v["envelope"]
        out = crypto.decrypt_preview(self.v["origin"], self.field("mobile_preview_private"),
                                      self.field("desktop_preview_public"), e, e["expires_at"]-1)
        self.assertEqual(out, self.field("preview_plaintext"))
        for change in ({"expires_at": e["expires_at"]+1}, {"notification_id": self.v["channel_id"]},
                       {"binding_id": self.v["gateway_id"]}, {"v": 0}, {"title": "leak"}):
            with self.assertRaises(Exception):
                crypto.decrypt_preview(self.v["origin"], self.field("mobile_preview_private"),
                                       self.field("desktop_preview_public"), dict(e, **change), e["expires_at"]-1)

    def test_fresh_ephemerals_and_bounds(self):
        v = self.v; e = v["envelope"]
        args = (v["origin"], self.field("desktop_preview_private"), self.field("mobile_preview_public"), e, b"preview")
        self.assertNotEqual(crypto.encrypt_preview(*args), crypto.encrypt_preview(*args))
        with self.assertRaises(ValueError):
            crypto.encrypt_preview(*args[:-1], b"x"*1025)
        for value in ("http://relay.example.test", "https://relay.example.test/", "https://RELAY.example.test",
                      "https://relay.example.test:443", "https://user@relay.example.test", "https://relay.example.test?x"):
            with self.assertRaises(ValueError):
                crypto.exact_origin(value)

    def test_production_form_preview_vector(self):
        v = json.loads((Path(__file__).parent / "fixtures/cloud_preview.json").read_text())
        credentials, envelope = v["credentials"], v["envelope"]
        private = base64.b64decode(credentials["private_key"])
        desktop = base64.b64decode(credentials["desktop_public_key"])
        raw = crypto.decrypt_preview(credentials["origin"], private, desktop, envelope, v["now"])
        self.assertEqual(raw, base64.b64decode(v["plaintext_base64"]))
        self.assertEqual(json.loads(raw), v["plaintext"])
        encrypted = crypto.encrypt_preview(credentials["origin"], base64.b64decode(v["desktop_private"]),
                                           crypto.public_key(private), envelope, raw,
                                           _test_ephemeral=base64.b64decode(v["ephemeral_private"]))
        self.assertEqual(encrypted, envelope["ciphertext"])
        self.assertEqual(v["plaintext"]["event_id"], "cloud:" + envelope["notification_id"])
        self.assertEqual(len(raw) <= 1024, True)
        self.assertFalse(any(value in json.dumps(envelope) for value in (v["plaintext"]["title"], v["plaintext"]["body"])))


if __name__ == "__main__":
    unittest.main()
