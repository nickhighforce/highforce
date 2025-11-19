"""
CLI Entry Point for Monthly Intelligence Generation
Called by cron job on 1st of each month at 2am
"""
import sys
import logging
from datetime import datetime, timedelta, date

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """
    Generate monthly intelligence for all tenants for last month.
    Called by cron: 0 2 1 * * (1st of month 2am)
    """
    from app.services.jobs.intelligence_tasks import generate_intelligence_for_all_tenants

    # Calculate first day of last month
    today = datetime.utcnow().date()
    first_of_this_month = date(today.year, today.month, 1)
    last_month = (first_of_this_month - timedelta(days=1)).replace(day=1)

    last_month_str = last_month.strftime("%Y-%m-%d")

    logger.info(f"📊 Monthly Intelligence Cron Job Started")
    logger.info(f"   Processing month: {last_month.strftime('%B %Y')}")

    try:
        # Trigger batch generation
        generate_intelligence_for_all_tenants("monthly", last_month_str)

        logger.info(f"✅ Monthly intelligence cron job completed successfully")
        sys.exit(0)

    except Exception as e:
        logger.error(f"❌ Monthly intelligence cron job failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
