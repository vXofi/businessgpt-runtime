import contextlib
import io
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import migrate_businessgpt_env_vm
from scripts.migrate_businessgpt_env_vm import read_env_file, write_private_env
from scripts.remove_caddy_site_vm import remove_site_block, write_in_place


class DeploymentHelperTests(unittest.TestCase):
    def test_removes_only_the_named_caddy_block(self):
        source = """service-a.example {
    reverse_proxy service-a:5984
}

api.example {
    route {
        reverse_proxy api:8000
    }
}

bookkeeping.example {
    reverse_proxy books:8080
}
"""
        result = remove_site_block(source, "api.example")
        self.assertNotIn("api.example", result)
        self.assertIn("service-a.example", result)
        self.assertIn("bookkeeping.example", result)

    def test_caddy_writer_preserves_bind_mount_inode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Caddyfile"
            path.write_text("old\n", encoding="utf-8")
            inode = path.stat().st_ino
            write_in_place(path, "new\n")
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")

    def test_private_env_writer_is_atomic_and_rejects_multiline_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "service.env"
            write_private_env(path, {"TOKEN": "secret", "VALUE": "one"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(read_env_file(path)["TOKEN"], "secret")
            with self.assertRaisesRegex(ValueError, "newline"):
                write_private_env(path, {"TOKEN": "first\nsecond"})
            self.assertEqual(read_env_file(path)["TOKEN"], "secret")

    def test_env_migration_preserves_explicit_allowlists_and_does_not_print_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "bot.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute("create table chat_settings(chat_id integer primary key)")
            connection.execute("insert into chat_settings values (-100)")
            connection.commit()
            connection.close()

            api_values = {
                "BUSINESSGPT_API_KEY": "api-secret",
                "BUSINESSGPT_MODEL_PATH": "/models/model.gguf",
            }
            bot_values = {
                "BUSINESSGPT_BOT_TOKEN": "bot-secret",
                "BUSINESSGPT_BOT_ADMIN_USER_IDS": "123",
                "BUSINESSGPT_BOT_ALLOWED_CHAT_IDS": "-100,-200",
                "BUSINESSGPT_API_KEY": "api-secret",
                "BUSINESSGPT_API_URL": "http://businessgpt-api:8000/generate",
                "BUSINESSGPT_BOT_ALLOWED_API_URLS": (
                    "http://businessgpt-api:8000/generate,"
                    "http://future-api:8000/generate"
                ),
                "BUSINESSGPT_BOT_RECENT_CONTEXT_MESSAGES": "20",
                "BUSINESSGPT_BOT_FEEDBACK_PUBLIC_KEY_B64": "public-key",
            }

            def fake_container_env(name):
                return api_values if name == "businessgpt-api" else bot_values

            argv = [
                "migrate_businessgpt_env_vm.py",
                "--api-env",
                str(root / "api.env"),
                "--bot-env",
                str(root / "bot.env"),
                "--bot-db",
                str(database),
            ]
            output = io.StringIO()
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    migrate_businessgpt_env_vm,
                    "container_env",
                    side_effect=fake_container_env,
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(migrate_businessgpt_env_vm.main(), 0)

            migrated = read_env_file(root / "bot.env")
            self.assertEqual(migrated["BUSINESSGPT_BOT_ALLOWED_CHAT_IDS"], "-200,-100")
            self.assertEqual(
                migrated["BUSINESSGPT_BOT_ALLOWED_API_URLS"],
                bot_values["BUSINESSGPT_BOT_ALLOWED_API_URLS"],
            )
            self.assertEqual(migrated["BUSINESSGPT_BOT_RECENT_CONTEXT_MESSAGES"], "20")
            self.assertNotIn("api-secret", output.getvalue())
            self.assertNotIn("bot-secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
