import logging
from telegram import Update
from telegram.ext import ContextTypes
from ..core import database
from . import llm_agent

logger = logging.getLogger(__name__)

# Telegram handler
async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE, db_pool, client, bot_name):
    message_text = update.message.text
    user_id = str(update.message.from_user.id)
    user_name = update.message.from_user.first_name
    chat_id = str(update.message.chat_id)

    await database.store_message(db_pool, "telegram", chat_id, user_id, user_name, message_text)

    if bot_name in message_text:
        async def telegram_response(chat_id, text):
            await context.bot.send_message(chat_id=chat_id, text=text)
        await llm_agent.answer_question("telegram", chat_id, message_text, db_pool, telegram_response, client)
