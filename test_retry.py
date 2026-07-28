import asyncio
import unittest
from telegram.error import NetworkError, BadRequest
import telegram_utils


class TestWithRetry(unittest.TestCase):
    def test_recovers_after_network_failures(self):
        telegram_utils.RETRY_DELAY = 0  # без пауз в тесте
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise NetworkError("httpx.ProxyError: 503 Service Unavailable")
            return "отправлено"

        self.assertEqual(asyncio.run(telegram_utils.with_retry(flaky, "тест")), "отправлено")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_and_returns_none(self):
        telegram_utils.RETRY_DELAY = 0

        async def always_down():
            raise NetworkError("503")

        self.assertIsNone(asyncio.run(telegram_utils.with_retry(always_down, "тест")))

    def test_does_not_retry_permanent_errors(self):
        telegram_utils.RETRY_DELAY = 0
        calls = {"n": 0}

        async def forbidden():
            calls["n"] += 1
            raise BadRequest("user not found")

        with self.assertRaises(BadRequest):
            asyncio.run(telegram_utils.with_retry(forbidden, "тест"))
        self.assertEqual(calls["n"], 1, "постоянные ошибки повторять нельзя")


if __name__ == "__main__":
    unittest.main()
