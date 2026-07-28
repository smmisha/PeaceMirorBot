import logging
import re
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
    chat_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    PRIMARY KEY (chat_id, word)
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
CREATE TABLE IF NOT EXISTS allowed_words (
    chat_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    PRIMARY KEY (chat_id, word)
);

CREATE TABLE IF NOT EXISTS captchas (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, user_id)
);
"""


async def init_db(db_path: str) -> bool:
    """Initializes the SQLite database tables."""
    try:
        async with aiosqlite.connect(db_path) as db:
            # WAL: соединение открывается на каждый запрос, а фоновые задачи ходят в БД
            # параллельно с обработкой сообщений — без него ловим "database is locked"
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(INIT_SQL)
            # Migration: Add peace_points column to users table if missing
            try:
                await db.execute("ALTER TABLE users ADD COLUMN peace_points INTEGER DEFAULT 0")
            except Exception:
                pass  # Column already exists
            # Migration: флаг «публичное сообщение капчи уже удалено».
            # Запись о непройденной капче теперь живёт до верификации или кика,
            # иначе после перезапуска бота юзер оставался в муте навсегда.
            try:
                await db.execute("ALTER TABLE captchas ADD COLUMN message_deleted INTEGER DEFAULT 0")
            except Exception:
                pass
            await db.commit()

            # Migration: стоп-листы стали пер-чатовыми
            await _migrate_wordlists_to_per_chat(db)
        logger.info("Database schema initialized.")
        return True
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        return False


async def _known_chat_ids(db) -> list[int]:
    """Все чаты, которые бот когда-либо видел (по следам в остальных таблицах)."""
    async with db.execute(
        """
        SELECT DISTINCT chat_id FROM (
            SELECT chat_id FROM chat_history
            UNION SELECT chat_id FROM mutes
            UNION SELECT chat_id FROM admin_activity
            UNION SELECT chat_id FROM captchas
        )
        """
    ) as cursor:
        return [row[0] for row in await cursor.fetchall()]


async def _migrate_wordlists_to_per_chat(db) -> None:
    """
    Переводит bad_words/allowed_words со старой глобальной схемы (word PRIMARY KEY)
    на пер-чатовую (chat_id, word).

    Старые слова были общими для всех чатов, поэтому копируются в каждый чат,
    который бот уже знает, — иначе после обновления настроенные стоп-листы
    молча перестали бы действовать.
    """
    for table in ("bad_words", "allowed_words"):
        async with db.execute(f"PRAGMA table_info({table})") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        if "chat_id" in columns:
            continue

        async with db.execute(f"SELECT word FROM {table}") as cursor:
            legacy_words = [row[0] for row in await cursor.fetchall()]

        chat_ids = await _known_chat_ids(db)

        await db.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
        await db.execute(
            f"CREATE TABLE {table} ("
            f"chat_id INTEGER NOT NULL, word TEXT NOT NULL, PRIMARY KEY (chat_id, word))"
        )
        if legacy_words and chat_ids:
            await db.executemany(
                f"INSERT OR IGNORE INTO {table} (chat_id, word) VALUES (?, ?)",
                [(chat_id, word) for chat_id in chat_ids for word in legacy_words]
            )
        await db.execute(f"DROP TABLE {table}_legacy")
        await db.commit()

        logger.info(
            f"Migrated {table} to per-chat schema: "
            f"{len(legacy_words)} word(s) copied into {len(chat_ids)} chat(s)."
        )


def normalize_username(username: str | None) -> str | None:
    """
    Приводит имя к единому виду для хранения: "@ник" для настоящих username и
    отображаемое имя как есть. Раньше "@" клеился и к «Иван Петров», из-за чего
    в /top появлялось "@@Иван Петров", а поиск по упоминанию не находил юзера.
    """
    if not username:
        return None
    name = username.strip()
    if not name or name.startswith("ID:"):
        return name or None
    bare = name.lstrip("@")
    if not bare:
        return None
    # настоящий username: только буквы/цифры/подчёркивания, без пробелов
    if re.fullmatch(r'[A-Za-z0-9_]{3,32}', bare):
        return f"@{bare}"
    return name if not name.startswith("@") else bare


async def ensure_user_exists(db_path: str, user_id: int, username: str | None = None) -> None:
    """Inserts or updates user in DB so @username can be resolved by admin commands."""
    uname = normalize_username(username)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(excluded.username, users.username)
            """,
            (user_id, uname)
        )
        await db.commit()


async def record_violation(db_path: str, user_id: int, username: str) -> int:
    """Increments user's violation counter and updates timestamp. Returns new violation count."""
    username = normalize_username(username)
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


async def add_bad_word(db_path: str, chat_id: int, word: str) -> bool:
    """Adds a custom bad word to this chat's stop-list (lower-case)."""
    word_clean = word.strip().lower()
    if not word_clean:
        return False
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT INTO bad_words (chat_id, word) VALUES (?, ?)", (chat_id, word_clean)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_bad_word(db_path: str, chat_id: int, word: str) -> bool:
    """Removes a custom bad word from this chat's stop-list."""
    word_clean = word.strip().lower()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM bad_words WHERE chat_id = ? AND word = ?", (chat_id, word_clean)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_bad_words(db_path: str, chat_id: int) -> list[str]:
    """Retrieves this chat's custom bad words."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT word FROM bad_words WHERE chat_id = ? ORDER BY word ASC", (chat_id,)
        ) as cursor:
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
    """
    Returns active mute record if user currently has an unexpired mute in THIS chat.

    Запасной запрос к таблице users убран: он не учитывал chat_id, и мут,
    выданный в одном чате, возобновлялся при входе пользователя в любой другой.
    """
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM mutes WHERE chat_id = ? AND user_id = ? AND is_active = 1 AND muted_until > ?",
            (chat_id, user_id, now_str)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


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
    username = normalize_username(username)
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


def local_day_start_utc() -> str:
    """
    Начало сегодняшнего дня по ЛОКАЛЬНОМУ времени, выраженное в UTC — в том же
    формате, в котором SQLite пишет CURRENT_TIMESTAMP ("YYYY-MM-DD HH:MM:SS").

    Раньше здесь стояло datetime('now','start of day','localtime'), которое при
    UTC+3 даёт 03:00 UTC: сообщения, отправленные с 03:00 до 06:00 по Киеву,
    не попадали в /summary и безвозвратно удалялись из истории.
    """
    now_local = datetime.now().astimezone()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _utc_stamp_to_local_time(created_at: str) -> str:
    """Переводит UTC-метку из БД в локальное HH:MM для вывода в сводке."""
    try:
        dt = datetime.strptime(str(created_at)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%H:%M")
    except (ValueError, TypeError):
        return "00:00"


async def cleanup_old_chat_history(db_path: str):
    """Deletes chat_history messages from previous days (keeping only current day's messages). Mutes/bans are unaffected."""
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM chat_history WHERE created_at < ?", (local_day_start_utc(),))
            await db.commit()
    except Exception as e:
        logger.error(f"Error cleaning up old chat history: {e}")


async def save_chat_message(db_path: str, chat_id: int, user_name: str, message_text: str):
    """Saves a chat message to persistent SQLite storage for /summary."""
    try:
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
                "SELECT user_name, message_text, created_at FROM chat_history "
                "WHERE chat_id = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
                (chat_id, local_day_start_utc(), limit)
            )
            rows = await cursor.fetchall()
            history = []
            for row in reversed(rows):
                history.append({
                    "name": row["user_name"],
                    "text": row["message_text"],
                    "time": _utc_stamp_to_local_time(row["created_at"])
                })
            return history
    except Exception as e:
        logger.error(f"Error reading chat history from database: {e}")
        return []


async def add_allowed_word(db_path: str, chat_id: int, word: str) -> bool:
    """Adds a custom allowed word to this chat's whitelist."""
    word_clean = word.strip().lower()
    if not word_clean:
        return False
    async with aiosqlite.connect(db_path) as db:
        try:
            await db.execute(
                "INSERT INTO allowed_words (chat_id, word) VALUES (?, ?)", (chat_id, word_clean)
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def remove_allowed_word(db_path: str, chat_id: int, word: str) -> bool:
    """Removes a custom allowed word from this chat's whitelist."""
    word_clean = word.strip().lower()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM allowed_words WHERE chat_id = ? AND word = ?", (chat_id, word_clean)
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_allowed_words(db_path: str, chat_id: int) -> list[str]:
    """Retrieves this chat's custom allowed words."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT word FROM allowed_words WHERE chat_id = ? ORDER BY word ASC", (chat_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]


async def save_active_captcha(db_path: str, chat_id: int, user_id: int, message_id: int):
    """Saves or updates active captcha message for a user in SQLite."""
    now_str = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO captchas (chat_id, user_id, message_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                message_id = excluded.message_id,
                created_at = excluded.created_at
            """,
            (chat_id, user_id, message_id, now_str)
        )
        await db.commit()


async def get_active_captcha(db_path: str, chat_id: int, user_id: int) -> dict | None:
    """Возвращает незакрытую капчу пользователя, если она есть."""
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id, user_id, message_id, created_at FROM captchas WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def remove_active_captcha(db_path: str, chat_id: int, user_id: int):
    """Removes active captcha entry for a user from SQLite."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM captchas WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        await db.commit()


async def get_expired_captchas(db_path: str, timeout_seconds: int = 180) -> list[dict]:
    """Returns captchas whose public message is older than timeout_seconds and not yet deleted."""
    cutoff_str = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id, user_id, message_id FROM captchas "
            "WHERE created_at <= ? AND COALESCE(message_deleted, 0) = 0",
            (cutoff_str,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def mark_captcha_message_deleted(db_path: str, chat_id: int, user_id: int) -> None:
    """Помечает публичное сообщение капчи удалённым, но саму капчу оставляет непройденной."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE captchas SET message_deleted = 1 WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id)
        )
        await db.commit()


async def get_captchas_to_kick(db_path: str, timeout_seconds: int = 86400) -> list[dict]:
    """Возвращает пользователей, не прошедших капчу дольше timeout_seconds (для кика)."""
    cutoff_str = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT chat_id, user_id FROM captchas WHERE created_at <= ?",
            (cutoff_str,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def find_user_by_name_or_alias(db_path: str, chat_id: int, query_str: str) -> tuple[int, str] | None:
    """
    Finds user_id and username by @username, raw nickname, or display name across users and mutes.
    """
    clean = query_str.strip().lower()
    if not clean:
        return None
    if clean.startswith("@"):
        clean = clean[1:]
    with_at = f"@{clean}"

    async with aiosqlite.connect(db_path) as db:
        # 1. Exact match on username in users table
        async with db.execute(
            "SELECT user_id, username FROM users WHERE LOWER(username) IN (?, ?)",
            (with_at, clean)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1] or with_at

        # 2. Match in mutes table
        async with db.execute(
            "SELECT user_id, username FROM mutes WHERE chat_id = ? AND LOWER(username) IN (?, ?)",
            (chat_id, with_at, clean)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1] or with_at

        # 3. Partial substring match in users table
        async with db.execute(
            "SELECT user_id, username FROM users WHERE LOWER(username) LIKE ?",
            (f"%{clean}%",)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1] or with_at

    return None

