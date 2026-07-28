import logging
from datetime import datetime, timedelta, timezone
import aiosqlite

logger = logging.getLogger("PeaceMirorBot.database")

INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    violations INTEGER DEFAULT 0,
    last_violation TIMESTAMP,
    is_muted BOOLEAN DEFAULT 0,
    muted_until TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bad_words (
    word TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS mutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    muted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    muted_until TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT 1
);

CREATE TABLE IF NOT EXISTS admin_activity (
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    username TEXT,
    last_active TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id, chat_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_name TEXT NOT NULL,
    message_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def init_db(db_path: str) -> bool:
    """Initializes the SQLite database tables."""
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(INIT_SQL)
            # Migration: Add peace_points column to users table if missing
            try:
                await db.execute("ALTER TABLE users ADD COLUMN peace_points INTEGER DEFAULT 0")
            except Exception:
                pass  # Column already exists
            await db.commit()
        logger.info("Database schema initialized.")
        return True
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        return False


async def record_violation(db_path: str, user_id: int, username: str) -> int:
    """Increments user's violation counter and updates timestamp. Returns new violation count."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT violations FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()

        if row:
            new_count = row[0] + 1
            await db.execute(
                "UPDATE users SET violations = ?, username = ?, last_violation = ? WHERE user_id = ?",
                (new_count, username, now, user_id)
            )
        else:
            new_count = 1
            await db.execute(
                "INSERT INTO users (user_id, username, violations, last_violation) VALUES (?, ?, ?, ?)",
                (user_id, username, new_count, now)
            )
        await db.commit()
        return new_count


async def get_user_stats(db_path: str, user_id: int) -> dict:
    """Fetches stats for a user."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return {"user_id": user_id, "violations": 0, "is_muted": 0, "muted_until": None}


async def reset_user_stats(db_path: str, user_id: int) -> bool:
    """Resets violations and mute status for a user."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET violations = 0, is_muted = 0, muted_until = NULL WHERE user_id = ?",
            (user_id,)
        )
        await db.commit()
        return True


async def reset_all_users_stats(db_path: str) -> bool:
    """Resets violations and mute status for ALL users in DB."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE users SET violations = 0, is_muted = 0, muted_until = NULL")
        await db.execute("UPDATE mutes SET is_active = 0")
        await db.commit()
        return True


async def set_user_mute(db_path: str, chat_id: int, user_id: int, username: str, muted_until: datetime) -> None:
    """Records an active mute in DB."""
    until_str = muted_until.isoformat()
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET is_muted = 1, muted_until = ? WHERE user_id = ?",
            (until_str, user_id)
        )
        # Deactivate old active mutes for this user in this chat
        await db.execute(
            "UPDATE mutes SET is_active = 0 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        await db.execute(
            "INSERT INTO mutes (chat_id, user_id, username, muted_at, muted_until, is_active) VALUES (?, ?, ?, ?, ?, 1)",
            (chat_id, user_id, username, now_str, until_str)
        )
        await db.commit()


async def clear_user_mute(db_path: str, chat_id: int, user_id: int) -> None:
    """Clears user mute in DB."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE users SET is_muted = 0, muted_until = NULL WHERE user_id = ?",
            (user_id,)
        )
        await db.execute(
            "UPDATE mutes SET is_active = 0 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        await db.commit()


async def get_expired_mutes(db_path: str) -> list[dict]:
    """Returns all active mutes whose muted_until timestamp is in the past."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, chat_id, user_id, username FROM mutes WHERE is_active = 1 AND muted_until <= ?",
            (now_str,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def add_bad_word(db_path: str, word: str) -> bool:
    """Adds a custom bad word to DB (lower-case)."""
    word_clean = word.strip().lower()
    if not word_clean:
        return False
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute("INSERT INTO bad_words (word) VALUES (?)", (word_clean,))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_bad_word(db_path: str, word: str) -> bool:
    """Removes a custom bad word from DB."""
    word_clean = word.strip().lower()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM bad_words WHERE word = ?", (word_clean,))
        await db.commit()
        return cursor.rowcount > 0


async def get_bad_words(db_path: str) -> list[str]:
    """Retrieves all custom bad words from DB."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT word FROM bad_words ORDER BY word ASC") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def reset_inactive_violations(db_path: str, days: int = 7) -> int:
    """Resets violation counts for users whose last violation was more than `days` ago."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "UPDATE users SET violations = 0 WHERE last_violation <= ? AND violations > 0",
            (cutoff,)
        )
        await db.commit()
        return cursor.rowcount


async def record_admin_activity(db_path: str, user_id: int, chat_id: int, username: str) -> None:
    """Records or updates last activity timestamp for an admin in a specific chat."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO admin_activity (user_id, chat_id, username, last_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                username = excluded.username,
                last_active = excluded.last_active
            """,
            (user_id, chat_id, username, now_str)
        )
        await db.commit()


async def get_active_admins(db_path: str, chat_id: int) -> list[dict]:
    """Returns admins for a chat ordered by most recently active first."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, last_active FROM admin_activity WHERE chat_id = ? ORDER BY last_active DESC",
            (chat_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_active_user_mute(db_path: str, chat_id: int, user_id: int) -> dict | None:
    """Returns active mute record if user currently has an unexpired mute in DB."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mutes WHERE chat_id = ? AND user_id = ? AND is_active = 1 AND muted_until > ?",
            (chat_id, user_id, now_str)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)

        async with db.execute(
            "SELECT * FROM users WHERE user_id = ? AND is_muted = 1 AND muted_until > ?",
            (user_id, now_str)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)

    return None


async def get_setting(db_path: str, key: str, default: str = "") -> str:
    """Gets a setting value from settings table."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_setting(db_path: str, key: str, value: str) -> None:
    """Sets a setting value in settings table."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()


def get_rank_title(points: int) -> tuple[str, str]:
    """Returns (badge_emoji, title_string) based on accumulated peace points."""
    if points < 20:
        return "🐣", "Участник чата"
    elif points < 100:
        return "🤝", "Добродушный соратник"
    elif points < 300:
        return "✨", "Хранитель Уюта"
    elif points < 700:
        return "🕊️", "Миротворец"
    else:
        return "👑", "Легендарный Миротворец"


async def add_peace_points(db_path: str, user_id: int, username: str, points: int) -> int:
    """Adds points to user's peace_points counter. Returns updated total points."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO users (user_id, username, peace_points) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET peace_points = MAX(0, peace_points + ?), username = COALESCE(?, username)",
            (user_id, username, max(0, points), points, username)
        )
        await db.commit()
        async with db.execute("SELECT peace_points FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_top_peacekeepers(db_path: str, limit: int = 10) -> list[dict]:
    """Returns top users with highest peace_points."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, peace_points, violations FROM users WHERE peace_points > 0 ORDER BY peace_points DESC LIMIT ?",
            (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def cleanup_old_chat_history(db_path: str):
    """Deletes chat_history messages from previous days (keeping only current day's messages). Mutes/bans are unaffected."""
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM chat_history WHERE created_at < datetime('now', 'start of day', 'localtime')")
            await db.commit()
    except Exception as e:
        logger.error(f"Error cleaning up old chat history: {e}")


async def save_chat_message(db_path: str, chat_id: int, user_name: str, message_text: str):
    """Saves a chat message to persistent SQLite storage for /summary, auto-clearing previous days' messages."""
    try:
        await cleanup_old_chat_history(db_path)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO chat_history (chat_id, user_name, message_text) VALUES (?, ?, ?)",
                (chat_id, user_name, message_text[:300])
            )
            await db.commit()
    except Exception as e:
        logger.error(f"Error saving chat message to database: {e}")


async def get_recent_chat_history(db_path: str, chat_id: int, limit: int = 150) -> list[dict]:
    """Retrieves current day's chat messages from persistent SQLite storage for /summary."""
    try:
        await cleanup_old_chat_history(db_path)
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT user_name, message_text, created_at FROM chat_history WHERE chat_id = ? AND created_at >= datetime('now', 'start of day', 'localtime') ORDER BY id DESC LIMIT ?",
                (chat_id, limit)
            )
            rows = await cursor.fetchall()
            history = []
            for row in reversed(rows):
                created_str = str(row["created_at"])
                time_str = created_str.split()[1][:5] if " " in created_str else "00:00"
                history.append({
                    "name": row["user_name"],
                    "text": row["message_text"],
                    "time": time_str
                })
            return history
    except Exception as e:
        logger.error(f"Error reading chat history from database: {e}")
        return []
