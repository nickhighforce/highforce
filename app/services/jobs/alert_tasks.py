"""
Background Tasks for Real-Time Alert Detection

Uses Dramatiq task queue to process urgency detection asynchronously
without blocking document ingestion.
"""
import logging
import asyncio
from typing import Dict, Any

import dramatiq
from supabase import create_client, Client

from app.core.config import settings
from app.services.intelligence.realtime_detector import detect_urgency

logger = logging.getLogger(__name__)


def get_supabase_client() -> Client:
    """Get Supabase client for background tasks."""
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_KEY)


@dramatiq.actor(queue_name="alerts", max_retries=3, time_limit=60_000)  # 60 second timeout
def detect_document_urgency_task(
    document_id: int,
    title: str,
    content: str,
    metadata: Dict[str, Any],
    source: str,
    company_id: str
):
    """
    Background task to detect urgency in a document.

    This runs asynchronously after document ingestion to avoid blocking.

    Args:
        document_id: Document ID
        title: Document title
        content: Document content
        metadata: Document metadata
        source: Document source
        company_id: Tenant ID
    """
    try:
        logger.info(f"🔄 Starting urgency detection task for document {document_id}")

        supabase = get_supabase_client()

        # Run async detection in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            alert = loop.run_until_complete(
                detect_urgency(
                    document_id=document_id,
                    title=title,
                    content=content,
                    metadata=metadata,
                    source=source,
                    company_id=company_id,
                    supabase=supabase
                )
            )

            if alert:
                logger.info(f"✅ Alert created for document {document_id}: {alert['urgency_level']}")
            else:
                logger.debug(f"✅ No alert needed for document {document_id}")

        finally:
            loop.close()

    except Exception as e:
        logger.error(f"❌ Urgency detection task failed for document {document_id}: {e}", exc_info=True)
        raise  # Let Dramatiq handle retries


@dramatiq.actor(queue_name="alerts", max_retries=2, time_limit=300_000)  # 5 minute timeout
def batch_detect_urgency_task(
    company_id: str,
    limit: int = 100,
    only_recent: bool = True
):
    """
    Background task to detect urgency for multiple documents (backfill).

    Useful for:
    - Initial setup (analyze existing documents)
    - Reprocessing documents with new detection logic
    - Testing

    Args:
        company_id: Tenant ID
        limit: Max documents to process
        only_recent: If True, only process documents from last 7 days
    """
    try:
        logger.info(f"🔄 Starting batch urgency detection for tenant {company_id} (limit: {limit})")

        supabase = get_supabase_client()

        # Fetch documents that don't have urgency detected yet
        query = supabase.table("documents")\
            .select("id, title, content, metadata, source")\
            .eq("company_id", company_id)\
            .is_("urgency_level", "null")\
            .limit(limit)

        if only_recent:
            from datetime import datetime, timedelta
            cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
            query = query.gte("created_at", cutoff)

        result = query.execute()
        documents = result.data or []

        logger.info(f"📄 Found {len(documents)} documents to analyze")

        if not documents:
            logger.info("✅ No documents to process")
            return

        # Process documents one by one (could parallelize but rate limiting)
        alerts_created = 0
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            for doc in documents:
                try:
                    alert = loop.run_until_complete(
                        detect_urgency(
                            document_id=doc["id"],
                            title=doc.get("title", ""),
                            content=doc.get("content", ""),
                            metadata=doc.get("metadata", {}),
                            source=doc.get("source", "unknown"),
                            company_id=company_id,
                            supabase=supabase
                        )
                    )

                    if alert:
                        alerts_created += 1

                except Exception as e:
                    logger.error(f"Failed to process document {doc['id']}: {e}")
                    continue

        finally:
            loop.close()

        logger.info(f"✅ Batch processing complete: {alerts_created} alerts created from {len(documents)} documents")

    except Exception as e:
        logger.error(f"❌ Batch urgency detection failed for tenant {company_id}: {e}", exc_info=True)
        raise


@dramatiq.actor(queue_name="alerts", max_retries=1)
def cleanup_old_dismissed_alerts_task(days_old: int = 90):
    """
    Background task to clean up old dismissed alerts.

    Args:
        days_old: Delete dismissed alerts older than this many days
    """
    try:
        from datetime import datetime, timedelta

        logger.info(f"🧹 Cleaning up dismissed alerts older than {days_old} days")

        supabase = get_supabase_client()
        cutoff = (datetime.utcnow() - timedelta(days=days_old)).isoformat()

        # Delete old dismissed alerts
        result = supabase.table("document_alerts")\
            .delete()\
            .not_.is_("dismissed_at", "null")\
            .lt("dismissed_at", cutoff)\
            .execute()

        deleted_count = len(result.data) if result.data else 0
        logger.info(f"✅ Deleted {deleted_count} old dismissed alerts")

    except Exception as e:
        logger.error(f"❌ Alert cleanup task failed: {e}", exc_info=True)
