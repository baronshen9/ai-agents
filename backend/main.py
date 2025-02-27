import logging
from .agents import multi_platform_bot

# Assuming FastAPI or Flask setup exists
# Add this as an optional bot runner

def main():
    # Configure logging if not already done in logging_config.py
    logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
    logger = logging.getLogger(__name__)

    logger.info("Starting multi-platform bot...")
    multi_platform_bot.run_multi_platform_bot()

if __name__ == "__main__":
    main()