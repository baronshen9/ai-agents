import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database configuration
DB_CONFIG = {
    'host': os.environ.get("DB_HOST", "localhost"),
    'port': int(os.environ.get("DB_PORT", 5432)),
    'database': os.environ.get("DB_NAME", "ai_trading"),
    'user': os.environ.get("DB_USER", "postgres"),
    'password': os.environ.get("DB_PASSWORD", "")
}

# API keys and tokens
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")

# Bot names
TELEGRAM_BOT_NAME = "@TradeSessionAssistBot"
SLACK_BOT_NAME = "@U08EEVBTENB"
