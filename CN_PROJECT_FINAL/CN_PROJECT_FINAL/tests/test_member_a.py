# test_member_a.py - Member A: protocol parser and authentication

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.protocol import parse_message, format_reject
from server.auth import authenticate


class TestProtocol(unittest.TestCase):

    def test_valid_request(self):
        result = parse_message(
            "REQUEST user1 10.0.0.1 10.0.0.2 6 120"
        )

        self.assertEqual(result["action"], "REQUEST")
        self.assertEqual(result["user"], "user1")
        self.assertEqual(result["mbps"], 6.0)
        self.assertEqual(result["seconds"], 120.0)

    def test_invalid_ip_rejected(self):
        with self.assertRaises(ValueError):
            parse_message(
                "REQUEST user1 invalid 10.0.0.2 6 120"
            )

    def test_invalid_command_rejected(self):
        with self.assertRaises(ValueError):
            parse_message("UNKNOWN user1")

    def test_missing_arguments_rejected(self):
        with self.assertRaises(ValueError):
            parse_message("REQUEST user1 10.0.0.1")

    def test_zero_and_negative_values_rejected(self):
        with self.assertRaises(ValueError):
            parse_message("REQUEST user1 10.0.0.1 10.0.0.2 0 60")
        with self.assertRaises(ValueError):
            parse_message("REQUEST user1 10.0.0.1 10.0.0.2 5 -1")

    def test_cancel_and_status_parse(self):
        self.assertEqual(parse_message("CANCEL 3")["reservation_id"], "3")
        self.assertEqual(parse_message("STATUS 3")["action"], "STATUS")

    def test_unknown_reject_reason_becomes_internal_error(self):
        self.assertEqual(format_reject("SOMETHING_ELSE"), "REJECT INTERNAL_ERROR")
        self.assertEqual(format_reject("CONTROLLER_ERROR"), "REJECT CONTROLLER_ERROR")


class TestAuthentication(unittest.TestCase):

    def test_valid_user1_credentials(self):
        self.assertTrue(
            authenticate("user1", "demo-token-user1")
        )

    def test_valid_user2_credentials(self):
        self.assertTrue(
            authenticate("user2", "demo-token-user2")
        )

    def test_wrong_token_rejected(self):
        self.assertFalse(
            authenticate("user1", "wrong-token")
        )

    def test_unknown_user_rejected(self):
        self.assertFalse(
            authenticate("unknown", "some-token")
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
