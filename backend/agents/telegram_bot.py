import logging
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters
from openai import OpenAI
import os
import asyncpg
from dotenv import load_dotenv
from functools import partial

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize OpenAI client
openai_api_key = os.environ["OPENAI_API_KEY"]
client = OpenAI(api_key=openai_api_key)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, db_pool) -> None:
    message_text = update.message.text
    user_id = update.message.from_user.id
    user_name = update.message.from_user.first_name
    chat_id = update.message.chat_id

    # Ignore messages from the bot itself
    if user_id == context.bot.id:
        return

    try:
        # Insert the message into the database
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO group_messages (chat_id, user_id, user_name, message_text) VALUES ($1, $2, $3, $4)", 
                             chat_id, user_id, user_name, message_text)
    except Exception as e:
        logger.error(f"Database message insertion error: {e}")
        # Optional: send an error message to the group

    # Check if it's a question (ends with "?")
    if message_text.endswith("?"):
        try:
            # Retrieve the last 50 messages as context
            async with db_pool.acquire() as conn:
                history_rows = await conn.fetch("SELECT user_name, message_text FROM group_messages WHERE chat_id = $1 ORDER BY timestamp DESC LIMIT 50", chat_id)

            # Construct the prompt
            chat_history_str = "\n".join(f"{row['user_name']}: {row['message_text']}" for row in history_rows)
            prompt = f"Group chat history:\n{chat_history_str}\n\nQuestion:\n{message_text}"

            # Call OpenAI API
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a group chat assistant, answering questions based on recent group messages. You have access to the last 50 messages as context."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500,
                temperature=0.7
            )
            answer = response.choices[0].message.content.strip()

            # Reply in the group
            await context.bot.send_message(chat_id=chat_id, text=answer)
            logger.info(f"Reply: {answer}")
        except asyncpg.PostgresError as e:
            logger.error(f"Database history retrieval error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="There’s a problem with the database, please try again later.")
        except openai.error.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="There’s a problem with the AI service, please try again later.")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            await context.bot.send_message(chat_id=chat_id, text="An error occurred, please try again later.")

def main() -> None:
    # Initialize the Telegram Bot application
    application = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()

    # Set up database connection pool
    db_config = {
        'host': os.environ["DB_HOST"],
        'port': int(os.environ["DB_PORT"]),
        'database': os.environ["DB_NAME"],
        'user': os.environ["DB_USER"],
        'password': os.environ["DB_PASSWORD"]
    }
    import asyncio
    loop = asyncio.get_event_loop()
    pool = loop.run_until_complete(asyncpg.create_pool(**db_config))

    # Add message handler, only process group messages
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, partial(handle_message, db_pool=pool)))

    # Start the bot
    logger.info("Bot has started...")
    application.run_polling(allowed_updates=None)

if __name__ == '__main__':
    main()