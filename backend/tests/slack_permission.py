import logging
import os
from dotenv import load_dotenv
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Initialize Slack client
slack_token = os.environ.get("SLACK_BOT_TOKEN")
slack_client = WebClient(token=slack_token)

def get_all_channels():
    """Fetch all public and private channels the bot is a member of."""
    all_channels = []
    cursor = None

    while True:
        try:
            response = slack_client.conversations_list(
                types="public_channel,private_channel",
                limit=1000,  # Max limit to reduce API calls
                cursor=cursor
            )
            channels = response["channels"]
            all_channels.extend(channels)
            logger.info(f"Fetched {len(channels)} channels, total so far: {len(all_channels)}")
            
            cursor = response["response_metadata"].get("next_cursor")
            if not cursor:
                break
        except SlackApiError as e:
            logger.error(f"Error fetching channels: {e.response['error']}")
            break

    return all_channels

# Test the function
channels = get_all_channels()
for channel in channels:
    print(f"Channel ID: {channel['id']}, Name: {channel['name']}, Private: {channel['is_private']}")