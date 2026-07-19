import unittest

from bot.main import ADMIN_COMMANDS, ALLOWED_UPDATES, GROUP_COMMANDS, OWNER_COMMANDS


class MainConfigurationTests(unittest.TestCase):
    def test_callback_queries_are_polled(self):
        self.assertIn("callback_query", ALLOWED_UPDATES)

    def test_command_menus_are_valid_and_unique(self):
        self.assertEqual([command.command for command in GROUP_COMMANDS], ["gpt"])
        admin_names = [command.command for command in ADMIN_COMMANDS]
        self.assertIn("settings", admin_names)
        self.assertIn("status", admin_names)
        self.assertNotIn("model_edit", admin_names)
        self.assertEqual(len(admin_names), len(set(admin_names)))
        owner_names = [command.command for command in OWNER_COMMANDS]
        self.assertIn("model_edit", owner_names)
        self.assertEqual(len(owner_names), len(set(owner_names)))


if __name__ == "__main__":
    unittest.main()
