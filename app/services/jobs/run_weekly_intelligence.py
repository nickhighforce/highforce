"""
CLI Entry Point for Weekly Intelligence Generation
Called by cron job every Monday at 1am
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
    Generate weekly intelligence for all tenants for last week.
    Called by cron: 0 1 * * 1 (Monday 1am)
    """
    from app.services.jobs.intelligence_tasks import generate_intelligence_for_all_tenants

    # Calculate last Monday's date
    today = datetime.utcnow().date()
    days_since_monday = (today.weekday() - 0) % 7  # 0 = Monday
    last_monday = today - timedelta(days=days_since_monday + 7)

    last_monday_str = last_monday.strftime("%Y-%m-%d")

    logger.info(f"📅 Weekly Intelligence Cron Job Started")
    logger.info(f"   Processing week starting: {last_monday_str}")

    try:
        # Trigger batch generation
        generate_intelligence_for_all_tenants("weekly", last_monday_str)

        logger.info(f"✅ Weekly intelligence cron job completed successfully")
        sys.exit(0)

    except Exception as e:
        logger.error(f"❌ Weekly intelligence cron job failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
