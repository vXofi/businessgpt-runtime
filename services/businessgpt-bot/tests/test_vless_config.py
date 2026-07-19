from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[3] / "scripts" / "vless_link_to_xray_config.py"
SPEC = importlib.util.spec_from_file_location("vless_link_to_xray_config", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VlessConfigTests(unittest.TestCase):
    def test_parses_xhttp_tls_link_without_leaking_fragment(self) -> None:
        config = MODULE.parse_vless_link(
            "vless://12345678-1234-5678-1234-567812345678@example.com:443"
            "?encryption=none&security=tls&fp=firefox&type=xhttp"
            "&host=cdn.example.com&mode=auto&path=%2Fprivate"
            "&alpn=h2%2Chttp%2F1.1#secret-label"
        )

        outbound = config["outbounds"][0]
        self.assertEqual(outbound["settings"]["vnext"][0]["address"], "example.com")
        self.assertEqual(outbound["streamSettings"]["tlsSettings"]["serverName"], "example.com")
        self.assertEqual(outbound["streamSettings"]["tlsSettings"]["fingerprint"], "firefox")
        self.assertEqual(outbound["streamSettings"]["xhttpSettings"]["path"], "/private")
        self.assertNotIn("secret-label", str(config))

    def test_rejects_other_transports(self) -> None:
        with self.assertRaisesRegex(ValueError, "XHTTP with TLS"):
            MODULE.parse_vless_link(
                "vless://12345678-1234-5678-1234-567812345678@example.com:443"
                "?encryption=none&security=tls&type=ws"
            )

    def test_writes_private_file_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "config.json"
            MODULE.write_private_json(output, {"ok": True}, force=False)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                MODULE.write_private_json(output, {"ok": False}, force=False)
