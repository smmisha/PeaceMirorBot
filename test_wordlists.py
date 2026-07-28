"""Тесты пер-чатовых стоп-листов и миграции со старой глобальной схемы."""

import asyncio
import os
import sqlite3
import tempfile
import unittest

import database


def run(coro):
    return asyncio.run(coro)


class TestPerChatWordlists(unittest.TestCase):

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db_path)  # init_db создаст файл сам

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            path = self.db_path + suffix
            if os.path.exists(path):
                os.unlink(path)

    def test_lists_are_isolated_between_chats(self):
        async def scenario():
            await database.init_db(self.db_path)
            chat_a, chat_b = -1001, -1002

            await database.add_bad_word(self.db_path, chat_a, "клоун")
            await database.add_allowed_word(self.db_path, chat_a, "тварь")

            self.assertEqual(await database.get_bad_words(self.db_path, chat_a), ["клоун"])
            self.assertEqual(await database.get_allowed_words(self.db_path, chat_a), ["тварь"])
            # В соседнем чате пусто — списки не общие
            self.assertEqual(await database.get_bad_words(self.db_path, chat_b), [])
            self.assertEqual(await database.get_allowed_words(self.db_path, chat_b), [])

            # Одно слово можно завести в разных чатах независимо
            self.assertTrue(await database.add_bad_word(self.db_path, chat_b, "клоун"))
            # Повтор в том же чате — отказ
            self.assertFalse(await database.add_bad_word(self.db_path, chat_a, "клоун"))

            # Удаление в одном чате не трогает другой
            self.assertTrue(await database.remove_bad_word(self.db_path, chat_a, "клоун"))
            self.assertEqual(await database.get_bad_words(self.db_path, chat_a), [])
            self.assertEqual(await database.get_bad_words(self.db_path, chat_b), ["клоун"])
            # Удаление отсутствующего слова — False, а не исключение
            self.assertFalse(await database.remove_bad_word(self.db_path, chat_a, "клоун"))

        run(scenario())

    def test_migration_copies_legacy_words_into_known_chats(self):
        """Старые глобальные слова должны разъехаться по всем известным чатам."""
        # Готовим БД в СТАРОЙ схеме: word как единственный ключ, без chat_id
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE bad_words (word TEXT PRIMARY KEY);
            CREATE TABLE allowed_words (word TEXT PRIMARY KEY);
            CREATE TABLE chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                user_name TEXT NOT NULL, message_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO bad_words (word) VALUES ('клоун'), ('дурак');
            INSERT INTO allowed_words (word) VALUES ('тварь');
            INSERT INTO chat_history (chat_id, user_name, message_text)
                VALUES (-1001, 'Иван', 'привет'), (-1002, 'Пётр', 'ку');
            """
        )
        conn.commit()
        conn.close()

        async def scenario():
            self.assertTrue(await database.init_db(self.db_path))

            for chat_id in (-1001, -1002):
                self.assertEqual(
                    await database.get_bad_words(self.db_path, chat_id),
                    ["дурак", "клоун"],
                    f"стоп-лист не перенёсся в чат {chat_id}"
                )
                self.assertEqual(
                    await database.get_allowed_words(self.db_path, chat_id), ["тварь"]
                )

            # В незнакомый чат ничего не протекло
            self.assertEqual(await database.get_bad_words(self.db_path, -9999), [])

            # Повторный запуск init_db не должен ломать уже мигрированную схему
            self.assertTrue(await database.init_db(self.db_path))
            self.assertEqual(
                await database.get_bad_words(self.db_path, -1001), ["дурак", "клоун"]
            )

        run(scenario())

    def test_fresh_db_has_per_chat_schema(self):
        async def scenario():
            await database.init_db(self.db_path)

        run(scenario())
        conn = sqlite3.connect(self.db_path)
        for table in ("bad_words", "allowed_words"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            self.assertIn("chat_id", columns, f"{table} создана без chat_id")
        conn.close()


if __name__ == "__main__":
    unittest.main()
