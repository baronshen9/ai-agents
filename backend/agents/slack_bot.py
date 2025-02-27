import logging
from slack_sdk.errors import SlackApiError
import asyncio
import time
from ..core import database
from . import llm_agent

logger = logging.getLogger(__name__)

async def fetch_slack_channels(client):
    """Fetch all public and private channels the bot is a member of."""
    all_channels = []
    cursor = None

    while True:
        try:
            response = client.conversations_list(
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
async def poll_slack_messages(db_pool, slack_client, bot_name, openai_client, interval=5, channel_refresh_interval=3600):
    """Poll all Slack channels the bot is in for new messages."""
    channel_last_ts = {}
    last_channel_fetch_time = 0
    member_channels = []

    while True:
        current_time = time.time()
        # Refresh channel list every hour (3600 seconds)
        if current_time - last_channel_fetch_time >= channel_refresh_interval:
            member_channels = await fetch_slack_channels(slack_client)
            last_channel_fetch_time = current_time
            logger.info(f"Refreshed Slack channel list: {len(member_channels)} channels")

        if not member_channels:
            member_channels = await fetch_slack_channels(slack_client)
            last_channel_fetch_time = current_time

        try:
            bot_user_id = slack_client.auth_test()["user_id"]

            for channel in member_channels:
                channel_id = channel["id"]
                last_ts = channel_last_ts.get(channel_id)

                # Fetch recent messages
                result = slack_client.conversations_history(channel=channel_id, limit=10, oldest=last_ts)
                messages = result["messages"]
                messages.reverse()

                for msg in messages:
                    ts = msg.get("ts")
                    if last_ts and float(ts) <= float(last_ts):
                        continue  # Skip already processed messages

                    message_text = msg.get("text")
                    user_id = msg.get("user")
                    chat_id = channel_id

                    if not user_id or not message_text or msg.get("user") == bot_user_id:
                        continue

                    try:
                        user_name = slack_client.users_info(user=user_id)["user"]["real_name"]
                    except SlackApiError as e:
                        logger.error(f"Failed to fetch Slack user info: {e}")
                        user_name = "Unknown User"

                    await database.store_message(db_pool, "slack", chat_id, user_id, user_name, message_text, slack_ts=ts)

                    if bot_name in message_text:
                        async def slack_response(chat_id, text):
                            try:
                                slack_client.chat_postMessage(channel=chat_id, text=text)
                            except SlackApiError as e:
                                logger.error(f"Slack API error sending message: {e}")
                        await llm_agent.answer_question("slack", chat_id, message_text, db_pool, slack_response, openai_client)

                    channel_last_ts[channel_id] = ts  # Update timestamp to avoid reprocessing

                logger.debug(f"Polled Slack channel {channel_id}, last_ts: {channel_last_ts.get(channel_id)}")
        except SlackApiError as e:
            logger.error(f"Slack polling error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in Slack polling: {e}")

        await asyncio.sleep(interval)
