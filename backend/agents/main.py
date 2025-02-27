import logging
import signal
import threading
import time
from telegram import Update
from telegram.ext import Application as TelegramApp, ContextTypes, MessageHandler, filters
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from openai import OpenAI
import os
import asyncpg
from dotenv import load_dotenv
from functools import partial
import asyncio

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize OpenAI client
openai_api_key = os.environ["OPENAI_API_KEY"]
client = OpenAI(api_key=openai_api_key)

# Initialize Slack client
slack_token = os.environ.get("SLACK_BOT_TOKEN")
slack_client = WebClient(token=slack_token) if slack_token else None
SLACK_CHANNEL_ID = "C08ESP65NKF"

# Bot names
TELEGRAM_BOT_NAME = "@TradeSessionAssistBot"
SLACK_BOT_NAME = "@U08EEVBTENB"

async def init_db_pool(db_config):
    """Initialize the database pool and create the table if it doesn’t exist."""
    pool = await asyncpg.create_pool(**db_config)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS group_messages (
                id SERIAL PRIMARY KEY,
                platform VARCHAR(50) NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                user_name VARCHAR(255) NOT NULL,
                message_text TEXT NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                slack_ts TEXT UNIQUE  -- Unique Slack timestamp to avoid duplicates
            );
            CREATE INDEX IF NOT EXISTS idx_group_messages_platform_chat_id_timestamp 
            ON group_messages (platform, chat_id, timestamp);
        """)
    return pool

async def store_message(db_pool, platform, chat_id, user_id, user_name, message_text, slack_ts=None):
    """Store a message in the PostgreSQL database, with Slack timestamp to avoid duplicates."""
    try:
        async with db_pool.acquire() as conn:
            if slack_ts:
                # Use UPSERT to avoid duplicates based on slack_ts
                await conn.execute(
                    """
                    INSERT INTO group_messages (platform, chat_id, user_id, user_name, message_text, slack_ts)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (slack_ts) DO NOTHING
                    """,
                    platform, chat_id, user_id, user_name, message_text, slack_ts
                )
            else:
                await conn.execute(
                    "INSERT INTO group_messages (platform, chat_id, user_id, user_name, message_text) VALUES ($1, $2, $3, $4, $5)",
                    platform, chat_id, user_id, user_name, message_text
                )
        logger.info(f"Stored {platform} message: {message_text}")
    except Exception as e:
        logger.error(f"Database message insertion error for {platform}: {e}")

async def fetch_channel_history(db_pool, platform, chat_id):
    """Fetch the last 50 messages for a specific platform and channel."""
    try:
        async with db_pool.acquire() as conn:
            history_rows = await conn.fetch(
                "SELECT user_name, message_text FROM group_messages WHERE platform = $1 AND chat_id = $2 ORDER BY timestamp DESC LIMIT 50",
                platform, chat_id
            )
        return history_rows
    except Exception as e:
        logger.error(f"Database history retrieval error for {platform}: {e}")
        return []

async def fetch_all_history(db_pool):
    """Fetch the last 50 messages across all platforms for LLM learning."""
    try:
        async with db_pool.acquire() as conn:
            history_rows = await conn.fetch(
                "SELECT platform, chat_id, user_name, message_text FROM group_messages ORDER BY timestamp DESC LIMIT 50"
            )
        return history_rows
    except Exception as e:
        logger.error(f"Database all history retrieval error: {e}")
        return []

async def answer_question(platform, chat_id, message_text, db_pool, response_func):
    """Answer a question based on channel-specific history, using all history for LLM context."""
    channel_history = await fetch_channel_history(db_pool, platform, chat_id)
    channel_history_str = "\n".join(f"{row['user_name']}: {row['message_text']}" for row in channel_history)

    all_history = await fetch_all_history(db_pool)
    all_history_str = "\n".join(f"{row['platform']} - {row['chat_id']} - {row['user_name']}: {row['message_text']}" for row in all_history)

    # instructions = ""

    # system_prompt = ()
    

    prompt = (
        f"All chat history (for learning):\n{all_history_str}\n\n"
        f"Current channel history (for answering):\n{channel_history_str}\n\n"
        f"Question:\n{message_text}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an assistant analyzing trader-customer communication across multiple platforms. Use all history to understand patterns, but answer based only on the current channel's context."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        answer = response.choices[0].message.content.strip()
        await response_func(chat_id, answer)
        logger.info(f"Reply on {platform}: {answer}")
    except openai.error.APIError as e:
        logger.error(f"OpenAI API error: {e}")
        await response_func(chat_id, "There’s a problem with the AI service, please try again later.")
    except Exception as e:
        logger.error(f"Unexpected error on {platform}: {e}")
        await response_func(chat_id, "An error occurred, please try again later.")

# Telegram handler
async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE, db_pool):
    message_text = update.message.text
    user_id = str(update.message.from_user.id)
    user_name = update.message.from_user.first_name
    chat_id = str(update.message.chat_id)

    await store_message(db_pool, "telegram", chat_id, user_id, user_name, message_text)

    #if message_text.endswith("?"):
    if TELEGRAM_BOT_NAME in message_text:
        async def telegram_response(chat_id, text):
            await context.bot.send_message(chat_id=chat_id, text=text)
        await answer_question("telegram", chat_id, message_text, db_pool, telegram_response)

async def fetch_slack_channels():
    """Fetch all public and private channels the bot is a member of."""
    all_channels = []
    cursor = None

    while True:
        try:
            response = slack_client.conversations_list(
                types="public_channel,private_channel",
                limit=1000,
                cursor=cursor
            )
            channels = response["channels"]
            all_channels.extend(channels)
            logger.info(f"Fetched {len(channels)} channels, total so far: {len(all_channels)}")
            cursor = response["response_metadata"].get("next_cursor")
            if not cursor:
                break
        except SlackApiError as e:
            logger.error(f"Error fetching Slack channels: {e.response['error']}")
            break

    member_channels = [channel for channel in all_channels if channel["is_member"]]
    logger.info(f"Bot is a member of {len(member_channels)} channels - {[channel['name'] for channel in member_channels]}")
    return member_channels

# Slack polling function
async def poll_slack_messages(db_pool, interval=5, channel_refresh_interval=3600):
    """Poll all Slack channels the bot is in for new messages every 'interval' seconds, refreshing channels every 'channel_refresh_interval' seconds."""
    channel_last_ts = {}
    last_channel_fetch_time = 0
    member_channels = []

    while True:
        current_time = time.time()
        # Refresh channel list every hour (3600 seconds)
        if current_time - last_channel_fetch_time >= channel_refresh_interval:
            member_channels = await fetch_slack_channels()
            last_channel_fetch_time = current_time
            logger.info(f"Refreshed Slask channel list: {len(member_channels)} channles")

        if not member_channels:
            logger.info(f"No Slack channels to poll; fetching channels now...")
            member_channels = await fetch_slack_channels()
            last_channel_fetch_time = current_time

        try:
            bot_user_id = slack_client.auth_test()["user_id"]
            # print(bot_user_id)

            for channel in member_channels:
                channel_id = channel["id"]
                last_ts = channel_last_ts.get(channel_id)

                # Fetch recent messages
                result = slack_client.conversations_history(channel=channel_id, limit=10, oldest=last_ts)
                messages = result["messages"]
                messages.reverse()  # Process oldest to newest

                for msg in messages:
                    # print(msg)
                    ts = msg.get("ts")
                    if last_ts and float(ts) <= float(last_ts):
                        continue  # Skip already processed messages

                    message_text = msg.get("text")
                    user_id = msg.get("user")
                    chat_id = channel_id

                    if not user_id or not message_text or msg.get("user") == bot_user_id:  # Ignore bot messages
                        continue

                    try:
                        user_name = slack_client.users_info(user=user_id)["user"]["real_name"]
                    except SlackApiError as e:
                        logger.error(f"Failed to fetch Slack user info: {e}")
                        user_name = "Unknown User"

                    await store_message(db_pool, "slack", chat_id, user_id, user_name, message_text, slack_ts=ts)

                    if SLACK_BOT_NAME in message_text:
                        async def slack_response(chat_id, text):
                            try:
                                slack_client.chat_postMessage(channel=chat_id, text=text)
                            except SlackApiError as e:
                                logger.error(f"Slack API error sending message: {e}")
                        await answer_question("slack", chat_id, message_text, db_pool, slack_response)

                    channel_last_ts[channel_id] = ts  # Update timestamp to avoid reprocessing

                logger.debug(f"Polled Slack channel {channel_id}, last_ts: {channel_last_ts.get(channel_id)}")
        except SlackApiError as e:
            logger.error(f"Slack polling error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in Slack polling: {e}")

        await asyncio.sleep(interval)

def run_slack_polling(db_config):
    """Run Slack polling in a separate thread with its own event loop and database pool."""
    slack_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(slack_loop)
    db_pool = slack_loop.run_until_complete(init_db_pool(db_config))
    try:
        slack_loop.run_until_complete(poll_slack_messages(db_pool, interval=5, channel_refresh_interval=3600))
    finally:
        slack_loop.run_until_complete(db_pool.close())
        slack_loop.close()
        logger.info("Slack thread shutdown complete")

def main():
    # Set up database connection pool
    db_config = {
        'host': os.environ["DB_HOST"],
        'port': int(os.environ["DB_PORT"]),
        'database': os.environ["DB_NAME"],
        'user': os.environ["DB_USER"],
        'password': os.environ["DB_PASSWORD"]
    }
    loop = asyncio.get_event_loop()
    telegram_pool = loop.run_until_complete(init_db_pool(db_config))

    # Initialize Telegram application
    telegram_app = TelegramApp.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, partial(handle_telegram_message, db_pool=telegram_pool)))

    # Start Slack polling if token and channel ID are provided
    if slack_token:
        slack_thread = threading.Thread(target=run_slack_polling, args=(db_config,), daemon=True)
        slack_thread.start()
    else:
        logger.warning("Slack token not provided; Slack polling will not run.")

    # Register signal handlers for graceful shutdown
    async def shutdown(signal):
        logger.info(f"Received {signal}, shutting down...")
        telegram_app.stop()
        await telegram_pool.close()
        logger.info("Telegram database pool closed")
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown(sig)))

    # Start Telegram bot
    logger.info("Telegram and Slack bots have started...")
    telegram_app.run_polling(allowed_updates=None)

if __name__ == "__main__":
    main()