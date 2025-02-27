import asyncpg
import logging

# Configure logging
logger = logging.getLogger(__name__)

async def init_db_pool(db_config, loop=None):
    """Initialize the asyncpg database pool with tuned connection limits, 
    and create the table if it doesn’t exist."""
    # pool = await asyncpg.create_pool(**db_config, loop=loop)
    # Tune pool size: min 1, max 5 connections per pool
    pool = await asyncpg.create_pool(
        **db_config,
        loop=loop,
        min_size=1,  # Minimum connections kept alive
        max_size=5,  # Maximum connections (adjust based on max_connections)
        max_queries=50000,  # Limits query backlog
        max_inactive_connection_lifetime=300  # Closes inactive connections after 5 minutes
    )
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
    logger.info(f"Database pool initialized with min_size=1, max_size=5")
    return pool

async def store_message(db_pool, platform, chat_id, user_id, user_name, message_text, slack_ts=None):
    """Store a message in the PostgreSQL database, with Slack timestamp to avoid duplicates."""
    async with db_pool.acquire() as conn:
        try:
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
            logger.info(f"Stored {platform} message in {chat_id}: {message_text}")
        except Exception as e:
            logger.error(f"Database message insertion error for {platform} in {chat_id}: {e}")

async def fetch_channel_history(db_pool, platform, chat_id):
    """Fetch the last 50 messages for a specific platform and channel."""
    async with db_pool.acquire() as conn:
        try:
            history_rows = await conn.fetch(
                "SELECT user_name, message_text FROM group_messages WHERE platform = $1 AND chat_id = $2 ORDER BY timestamp DESC LIMIT 50",
                platform, chat_id
            )
            return history_rows
        except Exception as e:
            logger.error(f"Database history retrieval error for {platform} in {chat_id}: {e}")
            return []

async def fetch_all_history(db_pool):
    """Fetch the last 50 messages across all platforms for LLM learning."""
    async with db_pool.acquire() as conn:
        try:
            history_rows = await conn.fetch(
                "SELECT platform, chat_id, user_name, message_text FROM group_messages ORDER BY timestamp DESC LIMIT 50"
            )
            return history_rows
        except Exception as e:
            logger.error(f"Database all history retrieval error: {e}")
            return []
        