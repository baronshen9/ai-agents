from functools import partial
import logging
import asyncio
import threading
import signal
from telegram.ext import Application as TelegramApp, MessageHandler, filters
from ..core import config, database
from . import telegram_bot, slack_bot, llm_agent
from slack_sdk import WebClient

logger = logging.getLogger(__name__)

def run_slack_polling(db_config, slack_client, openai_client):
    """Run Slack polling in a separate thread with its own event loop and database pool."""
    slack_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(slack_loop)
    db_pool = slack_loop.run_until_complete(database.init_db_pool(db_config))
    try:
        slack_loop.run_until_complete(slack_bot.poll_slack_messages(db_pool, slack_client, config.SLACK_BOT_NAME, openai_client))
    finally:
        slack_loop.run_until_complete(db_pool.close())
        slack_loop.close()
        logger.info("Slack thread shutdown complete")

async def shutdown(signal, telegram_app, slack_thread, telegram_pool):
    """Handle graceful shutdown for both Telegram and Slack."""
    logger.info(f"Received {signal}, shutting down...")
    telegram_app.stop()
    if slack_thread and slack_thread.is_alive():
        slack_thread.join(timeout=5)
    if telegram_pool:
        await telegram_pool.close()
        logger.info("Telegram database pool closed")
    asyncio.get_event_loop().stop()

def run_multi_platform_bot():
    # Main thread (Telegram) event loop
    loop = asyncio.get_event_loop()
    telegram_pool = loop.run_until_complete(database.init_db_pool(config.DB_CONFIG))

    # Initialize OpenAI client
    openai_client = llm_agent.get_openai_client(config.OPENAI_API_KEY)

    # Initialize Slack client
    slack_client = WebClient(token=config.SLACK_BOT_TOKEN) if config.SLACK_BOT_TOKEN else None

    # Initialize Telegram application
    telegram_app = TelegramApp.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    telegram_app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, 
                                            partial(telegram_bot.handle_telegram_message, db_pool=telegram_pool, client=openai_client, bot_name=config.TELEGRAM_BOT_NAME)))

    # Start Slack polling in a separate thread if token is provided
    slack_thread = None
    if config.SLACK_BOT_TOKEN:
        slack_thread = threading.Thread(target=run_slack_polling, args=(config.DB_CONFIG, slack_client, openai_client), daemon=True)
        slack_thread.start()
    else:
        logger.warning("Slack token not provided; Slack polling will not run.")

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown(sig, telegram_app, slack_thread, telegram_pool)))

    # Start Ops Assistant
    logger.info("Ops Assistant bots have started...")
    try:
        telegram_app.run_polling(allowed_updates=None)
    finally:
        loop.run_until_complete(telegram_pool.close())
        loop.close()
        logger.info("Main thread shutdown complete")
        