import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

raw_admin_id = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(raw_admin_id) if raw_admin_id.isdigit() else None

DB_PATH = os.getenv("DB_PATH", "peacemirror_database.db").strip()

# Mute durations in minutes for escalation ladder: [Step 2 (5m), Step 3 (15m), Step 4 (2h), Step 5+ (24h+)]
MUTE_STEP_2_MINUTES = int(os.getenv("MUTE_STEP_2_MINUTES", "5"))
MUTE_STEP_3_MINUTES = int(os.getenv("MUTE_STEP_3_MINUTES", "15"))
MUTE_STEP_4_MINUTES = int(os.getenv("MUTE_STEP_4_MINUTES", "120"))
MUTE_STEP_5_MINUTES = int(os.getenv("MUTE_STEP_5_MINUTES", "1440"))

# Reset inactive user violations after N days
RESET_INACTIVE_DAYS = int(os.getenv("RESET_INACTIVE_DAYS", "7"))

# Groq API Key
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("PeaceMirorBot")
