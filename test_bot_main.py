import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_main


class IPChangeTests(unittest.TestCase):
    def test_first_check_unchanged_change_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "ip.txt"
            with patch.object(bot_main, "get_public_ip", return_value="1.1.1.1"), patch.object(bot_main, "send_telegram") as send:
                bot_main.check_once("token", "chat", state)
                bot_main.check_once("token", "chat", state)
                send.assert_not_called()
            with patch.object(bot_main, "get_public_ip", return_value="8.8.8.8"), patch.object(bot_main, "send_telegram") as send:
                bot_main.check_once("token", "chat", state)
                bot_main.check_once("token", "chat", state)
                send.assert_called_once_with("token", "chat", "1.1.1.1", "8.8.8.8")

    def test_send_failure_preserves_baseline_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "ip.txt"
            state.write_text("1.1.1.1\n")
            with patch.object(bot_main, "get_public_ip", return_value="8.8.8.8"), patch.object(bot_main, "send_telegram", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    bot_main.check_once("token", "chat", state)
            self.assertEqual(state.read_text().strip(), "1.1.1.1")

    def test_ip_lookup_failure_does_not_send(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "ip.txt"
            with patch.object(bot_main, "get_public_ip", side_effect=RuntimeError("offline")), patch.object(bot_main, "send_telegram") as send:
                with self.assertRaises(RuntimeError):
                    bot_main.check_once("token", "chat", state)
                send.assert_not_called()
                self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
