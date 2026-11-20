import os
from dotenv import load_dotenv

load_dotenv("config.env")


def _int(name: str, default: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


class Config:
    """Central configuration loaded from environment variables."""

    API_ID = _int("API_ID", 0)
    API_HASH = os.environ.get("API_HASH", "")
    BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
    OWNER_ID = _int("OWNER_ID", 0)
    OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "")

    MONGO_URI = os.environ.get("MONGO_URI", "")
    MONGO_DB = os.environ.get("MONGO_DB", "chesa_auto")

    LOG_CHANNEL = _int("LOG_CHANNEL", 0)
    LOG_GC = LOG_CHANNEL

    UPDATE_CHANNEL = os.environ.get("UPDATE_CHANNEL", "")
    UPDATE_CHANNEL_LINK = os.environ.get("UPDATE_CHANNEL_LINK", "")

    GAME_BOT_USERNAME = os.environ.get("GAME_BOT_USERNAME", "GameFactoryBot")
    GAME_NAME = os.environ.get("GAME_NAME", "Chesa")

    WEBAPP_PORT = _int("PORT", 8080)
    AUTOPLAY_DEPTH = _int("AUTOPLAY_DEPTH", 3)
    AUTOPLAY_ENABLED = os.environ.get("AUTOPLAY_ENABLED", "true").lower() == "true"

    HEROKU_APP_NAME = os.environ.get("HEROKU_APP_NAME", "")
    # Full public https URL of the webapp, e.g. https://chesa-auto-1a2b3c4d.herokuapp.com/webapp/
    # New Heroku apps get a random suffix in their domain, so set this explicitly.
    WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip()

    @classmethod
    def validate(cls) -> list:
        """Return a list of missing required env vars (empty == OK)."""
        missing = []
        if not cls.API_ID:
            missing.append("API_ID")
        if not cls.API_HASH:
            missing.append("API_HASH")
        if not cls.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not cls.OWNER_ID:
            missing.append("OWNER_ID")
        if not cls.MONGO_URI:
            missing.append("MONGO_URI")
        return missing


config = Config()
