import logging
import textwrap
from openai import OpenAI
from ..core import database

logger = logging.getLogger(__name__)

def get_openai_client(api_key):
    """Initialize and return the OpenAI client."""
    return OpenAI(api_key=api_key)

async def answer_question(platform, chat_id, message_text, db_pool, response_func, client):
    """Answer a question based on channel-specific history, using all history for LLM context."""
    channel_history = await database.fetch_channel_history(db_pool, platform, chat_id)
    channel_history_str = "\n".join(f"{row['user_name']}: {row['message_text']}" for row in channel_history)

    all_history = await database.fetch_all_history(db_pool)
    all_history_str = "\n".join(f"{row['platform']} - {row['chat_id']} - {row['user_name']}: {row['message_text']}" for row in all_history)

    instructions = textwrap.dedent("""\
        You are a professional order confirmation agent who converts trading chats into a clear, 
        succinct order confirmation. Follow these guidelines:

        1. Begin with a brief, attention-grabbing headline using trade-related emojis (e.g., 💹, 📈).
        2. Order Summary: Provide key details in a short summary—names of the trader and customer, 
        asset, order quantity, price, stop-loss (if applicable), and wallet address (if provided).
        3. Confirmation Check: Pay special attention to whether the customer has explicitly confirmed 
        the order. If no clear confirmation is found, note that the customer's confirmation is pending.
        4. Generate messages that adapts to the Slack chat, for markdown formatting does not render in the chat window.

        Always ensure the output is clear and concise, highlighting any missing customer confirmation.
    """)

    system_prompt = (
        f"{instructions}\n\n"
        f"You are also analyzing trader-customer communication across multiple platforms. "
        f"Use all history to understand patterns, but answer based only on the current {platform} channel’s context."
    )

    prompt = (
        f"All chat history (for learning):\n{all_history_str}\n\n"
        f"Current {platform} channel history (for answering):\n{channel_history_str}\n\n"
        f"Question:\n{message_text}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        answer = response.choices[0].message.content.strip()
        await response_func(chat_id, answer)
        logger.info(f"Reply on {platform} in {chat_id}: {answer}")
    except openai.error.APIError as e:
        logger.error(f"OpenAI API error: {e}")
        await response_func(chat_id, "There’s a problem with the AI service, please try again later.")
    except Exception as e:
        logger.error(f"Unexpected error on {platform} in {chat_id}: {e}")
        await response_func(chat_id, "An error occurred, please try again later.")
