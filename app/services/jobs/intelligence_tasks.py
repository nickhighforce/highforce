"""
Intelligence Generation Background Tasks
Dramatiq actors for generating daily, weekly, and monthly intelligence summaries
"""
import dramatiq
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from app.core.dependencies import supabase_client
from app.core.config import settings
from app.services.intelligence.aggregator import (
    calculate_daily_metrics,
    calculate_weekly_trends,
    calculate_monthly_insights,
    generate_ai_summary
)

# Neo4j driver for entity queries
from neo4j import AsyncGraphDatabase
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


# ============================================================================
# DAILY INTELLIGENCE TASK
# ============================================================================

@dramatiq.actor(max_retries=2, time_limit=600_000)  # 10 minute timeout
def generate_daily_intelligence_task(company_id: str, target_date: Optional[str] = None):
    """
    Generate daily intelligence summary for a specific tenant and date.

    Args:
        company_id: Tenant ID to generate intelligence for
        target_date: Date string in YYYY-MM-DD format (default: yesterday)

    Runs: Daily at midnight via cron job
    """
    import asyncio

    # Parse date or default to yesterday
    if target_date:
        try:
            date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date format: {target_date}")
            return
    else:
        date_obj = (datetime.utcnow() - timedelta(days=1)).date()

    logger.info(f"🌙 Starting daily intelligence generation for {company_id} on {date_obj}")

    try:
        # Run async calculation
        asyncio.run(_generate_daily_intelligence_async(company_id, date_obj))
        logger.info(f"✅ Daily intelligence completed for {company_id} on {date_obj}")

    except Exception as e:
        logger.error(f"❌ Daily intelligence failed for {company_id} on {date_obj}: {e}", exc_info=True)
        raise


async def _generate_daily_intelligence_async(company_id: str, target_date: date):
    """Async implementation of daily intelligence generation."""

    # Initialize clients
    supabase = supabase_client
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=("neo4j", settings.neo4j_password)
    )

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        # 1. Calculate metrics
        metrics = await calculate_daily_metrics(
            supabase=supabase,
            neo4j_driver=neo4j_driver,
            company_id=company_id,
            target_date=target_date
        )

        # 2. Generate AI summary
        ai_summary = await generate_ai_summary(
            metrics=metrics,
            period_type="daily",
            openai_client=openai_client
        )

        metrics["ai_summary"] = ai_summary

        # 3. Store in database
        result = supabase.table("daily_intelligence")\
            .upsert(metrics, on_conflict="company_id,date")\
            .execute()

        logger.info(f"✅ Stored daily intelligence: {result.data[0]['id'] if result.data else 'unknown'}")

    finally:
        await neo4j_driver.close()


# ============================================================================
# WEEKLY INTELLIGENCE TASK
# ============================================================================

@dramatiq.actor(max_retries=2, time_limit=1200_000)  # 20 minute timeout
def generate_weekly_intelligence_task(company_id: str, week_start: Optional[str] = None):
    """
    Generate weekly intelligence summary for a specific tenant and week.

    Args:
        company_id: Tenant ID to generate intelligence for
        week_start: Monday date string in YYYY-MM-DD format (default: last Monday)

    Runs: Every Monday at 1am via cron job
    """
    import asyncio

    # Parse date or default to last Monday
    if week_start:
        try:
            date_obj = datetime.strptime(week_start, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date format: {week_start}")
            return
    else:
        # Get last Monday
        today = datetime.utcnow().date()
        days_since_monday = (today.weekday() - 0) % 7  # 0 = Monday
        date_obj = today - timedelta(days=days_since_monday + 7)  # Last Monday

    # Ensure it's a Monday
    if date_obj.weekday() != 0:
        logger.error(f"week_start must be a Monday, got {date_obj} ({date_obj.strftime('%A')})")
        return

    logger.info(f"📅 Starting weekly intelligence generation for {company_id}, week of {date_obj}")

    try:
        # Run async calculation
        asyncio.run(_generate_weekly_intelligence_async(company_id, date_obj))
        logger.info(f"✅ Weekly intelligence completed for {company_id}, week of {date_obj}")

    except Exception as e:
        logger.error(f"❌ Weekly intelligence failed for {company_id}, week of {date_obj}: {e}", exc_info=True)
        raise


async def _generate_weekly_intelligence_async(company_id: str, week_start: date):
    """Async implementation of weekly intelligence generation."""

    # Initialize clients
    supabase = supabase_client
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=("neo4j", settings.neo4j_password)
    )

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        # 1. Calculate trends
        metrics = await calculate_weekly_trends(
            supabase=supabase,
            neo4j_driver=neo4j_driver,
            company_id=company_id,
            week_start=week_start
        )

        # 2. Generate AI summary
        weekly_summary = await generate_ai_summary(
            metrics=metrics,
            period_type="weekly",
            openai_client=openai_client
        )

        metrics["weekly_summary"] = weekly_summary

        # 3. Store in database
        result = supabase.table("weekly_intelligence")\
            .upsert(metrics, on_conflict="company_id,week_start")\
            .execute()

        logger.info(f"✅ Stored weekly intelligence: {result.data[0]['id'] if result.data else 'unknown'}")

    finally:
        await neo4j_driver.close()


# ============================================================================
# MONTHLY INTELLIGENCE TASK
# ============================================================================

@dramatiq.actor(max_retries=2, time_limit=1800_000)  # 30 minute timeout
def generate_monthly_intelligence_task(company_id: str, month: Optional[str] = None):
    """
    Generate monthly intelligence summary for a specific tenant and month.

    Args:
        company_id: Tenant ID to generate intelligence for
        month: Month string in YYYY-MM-01 format (default: last month)

    Runs: 1st of each month at 2am via cron job
    """
    import asyncio

    # Parse date or default to last month
    if month:
        try:
            date_obj = datetime.strptime(month, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Invalid date format: {month}")
            return
    else:
        # Get first day of last month
        today = datetime.utcnow().date()
        first_of_this_month = date(today.year, today.month, 1)
        date_obj = (first_of_this_month - timedelta(days=1)).replace(day=1)

    # Ensure it's the 1st of the month
    if date_obj.day != 1:
        logger.error(f"month must be the 1st of a month, got {date_obj}")
        return

    logger.info(f"📊 Starting monthly intelligence generation for {company_id}, month {date_obj.strftime('%B %Y')}")

    try:
        # Run async calculation
        asyncio.run(_generate_monthly_intelligence_async(company_id, date_obj))
        logger.info(f"✅ Monthly intelligence completed for {company_id}, month {date_obj.strftime('%B %Y')}")

    except Exception as e:
        logger.error(f"❌ Monthly intelligence failed for {company_id}, month {date_obj.strftime('%B %Y')}: {e}", exc_info=True)
        raise


async def _generate_monthly_intelligence_async(company_id: str, month: date):
    """Async implementation of monthly intelligence generation."""

    # Initialize clients
    supabase = supabase_client
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=("neo4j", settings.neo4j_password)
    )

    openai_client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        # 1. Calculate insights
        metrics = await calculate_monthly_insights(
            supabase=supabase,
            neo4j_driver=neo4j_driver,
            company_id=company_id,
            month=month
        )

        # 2. Generate AI summary
        executive_summary = await generate_ai_summary(
            metrics=metrics,
            period_type="monthly",
            openai_client=openai_client
        )

        metrics["executive_summary"] = executive_summary

        # 3. Store in database
        result = supabase.table("monthly_intelligence")\
            .upsert(metrics, on_conflict="company_id,month")\
            .execute()

        logger.info(f"✅ Stored monthly intelligence: {result.data[0]['id'] if result.data else 'unknown'}")

    finally:
        await neo4j_driver.close()


# ============================================================================
# BATCH GENERATION (For all active tenants)
# ============================================================================

@dramatiq.actor(max_retries=1, time_limit=3600_000)  # 1 hour timeout
def generate_intelligence_for_all_tenants(period: str, target_date: Optional[str] = None):
    """
    Generate intelligence for all active tenants.

    Args:
        period: "daily", "weekly", or "monthly"
        target_date: Optional date string (YYYY-MM-DD)

    This is the main entry point called by cron jobs.
    Fetches all active tenants and enqueues individual tasks.
    """
    logger.info(f"🌍 Starting batch intelligence generation: {period}")

    supabase = supabase_client
    if not supabase:
        raise RuntimeError("Supabase client not initialized")

    try:
        # Get all unique tenant IDs from documents table
        result = supabase.rpc('get_unique_company_ids').execute()

        if not result.data:
            logger.warning("No tenants found in database")
            return

        company_ids = [row['company_id'] for row in result.data]
        logger.info(f"Found {len(company_ids)} tenants to process")

        # Enqueue individual tasks
        for company_id in company_ids:
            if period == "daily":
                generate_daily_intelligence_task.send(company_id, target_date)
            elif period == "weekly":
                generate_weekly_intelligence_task.send(company_id, target_date)
            elif period == "monthly":
                generate_monthly_intelligence_task.send(company_id, target_date)
            else:
                logger.error(f"Unknown period: {period}")
                continue

        logger.info(f"✅ Enqueued {len(company_ids)} {period} intelligence tasks")

    except Exception as e:
        logger.error(f"❌ Batch intelligence generation failed: {e}", exc_info=True)
        raise
