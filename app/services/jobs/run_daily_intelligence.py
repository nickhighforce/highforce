"""
CLI Entry Point for Daily Intelligence Generation
Called by cron job every day at midnight
"""
import sys
import logging
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """
    Generate daily intelligence for all tenants for yesterday's data.
    Called by cron: 0 0 * * * (midnight daily)
    """
    from app.services.jobs.intelligence_tasks import generate_intelligence_for_all_tenants

    # Calculate yesterday's date
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(f"🌙 Daily Intelligence Cron Job Started")
    logger.info(f"   Processing date: {yesterday}")

    try:
        # Trigger batch generation
        generate_intelligence_for_all_tenants("daily", yesterday)

        logger.info(f"✅ Daily intelligence cron job completed successfully")
        sys.exit(0)

    except Exception as e:
        logger.error(f"❌ Daily intelligence cron job failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
