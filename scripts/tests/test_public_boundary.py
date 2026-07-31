import unittest

from scripts.check_public_boundary import content_findings, path_findings


class PublicBoundaryTests(unittest.TestCase):
    def test_allows_placeholders_and_documentation_addresses(self):
        source = b"\n".join(
            (
                b"BUSINESSGPT_API_" + b"KEY=",
                b"BUSINESSGPT_BOT_" + b"TOKEN=<required>",
                b"BUSINESSGPT_BOT_OWNER_" + b"USER_IDS=${OWNER_IDS}",
                b"host=192.0.2.10",
                b"local=http://127.0.0.1:8000",
            )
        )
        self.assertEqual(content_findings(".env.example", source), [])

    def test_rejects_nonempty_secret_and_identity_settings(self):
        source = b"\n".join(
            (
                b"BUSINESSGPT_API_" + b"KEY=not-a-real-key",
                b"BUSINESSGPT_BOT_ALLOWED_" + b"CHAT_IDS=-123456789",
            )
        )
        findings = content_findings(".env.example", source)
        self.assertTrue(any("populated secret" in item for item in findings))
        self.assertTrue(any("populated identity" in item for item in findings))

    def test_rejects_external_ip_and_personal_path(self):
        personal_path = b"/Users/" + b"xofi"
        external_ip = b"8.8." + b"8.8"
        source = b"ssh user@" + external_ip + b"\npath=" + personal_path + b"/private\n"
        findings = content_findings("README.md", source)
        self.assertTrue(any("IPv4" in item for item in findings))
        self.assertTrue(any(personal_path.decode() in item for item in findings))

    def test_rejects_compiled_deployment_target(self):
        source = (
            b'target="${BUSINESSGPT_DEPLOY_'
            b'TARGET:-user@host}"\n'
        )
        self.assertIn(
            "compiled deployment target fallback",
            content_findings("deploy.sh", source),
        )

    def test_rejects_private_prompt_but_allows_neutral_history_placeholder(self):
        unsafe = b'DEFAULT_SYSTEM_' + b'PROMPT = "private persona"\n'
        safe = (
            b'DEFAULT_SYSTEM_' + b'PROMPT = '
            b'"Configure the system prompt at deployment time."\n'
        )
        self.assertIn(
            "literal default system prompt",
            content_findings("server.py", unsafe),
        )
        self.assertEqual(content_findings("server.py", safe), [])

    def test_rejects_runtime_artifact_paths(self):
        self.assertTrue(path_findings("data/bot.sqlite3"))
        self.assertTrue(path_findings("models/model.gguf"))
        self.assertEqual(path_findings(".env.example"), [])

    def test_vless_links_are_limited_to_synthetic_parser_tests(self):
        link = (
            b"vless:" + b"//12345678-1234-5678-1234-567812345678"
            b"@example.com:443"
        )
        self.assertTrue(content_findings("README.md", link))
        self.assertEqual(
            content_findings("tests/test_vless_config.py", link),
            [],
        )


if __name__ == "__main__":
    unittest.main()
