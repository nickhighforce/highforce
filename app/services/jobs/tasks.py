"""
Dramatiq Background Tasks
Handles long-running sync operations (Gmail, Drive, Outlook) asynchronously
"""
import dramatiq
import asyncio
import logging
import httpx
from typing import Optional
from supabase import create_client

logger = logging.getLogger(__name__)


def get_sync_dependencies():
    """
    Create fresh instances of dependencies for background tasks.
    Dramatiq workers run in separate processes, so we can't share global clients.
    """
    from app.core.config import settings
    from app.core.config_master import master_config
    from app.services.rag import UniversalIngestionPipeline
    import app.core.dependencies as deps

    # Initialize master_supabase_client for multi-tenant mode
    if master_config.is_multi_tenant:
        logger.info(f"🏢 Worker initializing multi-tenant mode (Company ID: {master_config.company_id})")
        deps.master_supabase_client = create_client(
            master_config.master_supabase_url,
            master_config.master_supabase_service_key
        )
        logger.info("✅ Worker: Master Supabase client initialized")

    # Create fresh HTTP client
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0),  # Longer timeout for background jobs
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
    )

    # Create fresh Supabase client
    supabase = create_client(settings.supabase_url, settings.supabase_anon_key)

    # Create fresh RAG pipeline
    try:
        rag_pipeline = UniversalIngestionPipeline()
    except Exception as e:
        logger.error(f"Failed to initialize RAG pipeline in worker: {e}")
        rag_pipeline = None

    return http_client, supabase, rag_pipeline


async def _run_gmail_sync_with_cleanup(http_client: httpx.AsyncClient, supabase, rag_pipeline, user_id: str, provider_key: str, modified_after: Optional[str] = None):
    """
    Async wrapper that runs Gmail sync and handles HTTP client cleanup properly.
    """
    from app.services.sync import run_gmail_sync
    
    try:
        result = await run_gmail_sync(http_client, supabase, rag_pipeline, user_id, provider_key, modified_after)
        return result
    finally:
        # Cleanup HTTP client in the same event loop
        await http_client.aclose()


@dramatiq.actor(max_retries=3)
def sync_gmail_task(user_id: str, job_id: str, modified_after: Optional[str] = None):
    """
    Background job for Gmail sync.
    
    Args:
        user_id: User/tenant ID
        job_id: Sync job ID for status tracking
        modified_after: Optional ISO datetime filter
    """
    from app.services.sync import run_gmail_sync
    from app.core.config import settings
    
    logger.info(f"🚀 Starting Gmail sync job {job_id} for user {user_id}")
    
    http_client, supabase, rag_pipeline = get_sync_dependencies()
    
    try:
        # Update job status to running
        supabase.table("sync_jobs").update({
            "status": "running",
            "started_at": "now()"
        }).eq("id", job_id).execute()
        
        # Run the sync with proper cleanup
        result = asyncio.run(_run_gmail_sync_with_cleanup(
            http_client, supabase, rag_pipeline, 
            user_id, settings.nango_provider_key_gmail,
            modified_after
        ))
        
        # Update job status to completed
        supabase.table("sync_jobs").update({
            "status": "completed",
            "completed_at": "now()",
            "result": result
        }).eq("id", job_id).execute()
        
        logger.info(f"✅ Gmail sync job {job_id} complete: {result.get('messages_synced', 0)} messages")
        return result
        
    except Exception as e:
        logger.error(f"❌ Gmail sync job {job_id} failed: {e}")
        
        # Update job status to failed
        supabase.table("sync_jobs").update({
            "status": "failed",
            "completed_at": "now()",
            "error_message": str(e)
        }).eq("id", job_id).execute()
        
        raise  # Re-raise for Dramatiq retry logic
    
    finally:
        # Cleanup HTTP client
        asyncio.run(http_client.aclose())


@dramatiq.actor(max_retries=3)
def sync_drive_task(user_id: str, job_id: str, folder_ids: Optional[list] = None):
    """
    Background job for Google Drive sync.
    
    Args:
        user_id: User/tenant ID
        job_id: Sync job ID for status tracking
        folder_ids: Optional list of folder IDs to sync
    """
    from app.services.sync.orchestration.drive_sync import run_drive_sync
    from app.core.config import settings
    
    logger.info(f"🚀 Starting Drive sync job {job_id} for user {user_id}")
    
    http_client, supabase, rag_pipeline = get_sync_dependencies()
    
    try:
        # Update job status to running
        supabase.table("sync_jobs").update({
            "status": "running",
            "started_at": "now()"
        }).eq("id", job_id).execute()
        
        # Run the sync
        result = asyncio.run(run_drive_sync(
            http_client, supabase, rag_pipeline,
            user_id, settings.nango_provider_key_google_drive,
            folder_ids=folder_ids
        ))
        
        # Update job status to completed
        supabase.table("sync_jobs").update({
            "status": "completed",
            "completed_at": "now()",
            "result": result
        }).eq("id", job_id).execute()
        
        logger.info(f"✅ Drive sync job {job_id} complete: {result.get('files_synced', 0)} files")
        return result
        
    except Exception as e:
        logger.error(f"❌ Drive sync job {job_id} failed: {e}")
        
        # Update job status to failed
        supabase.table("sync_jobs").update({
            "status": "failed",
            "completed_at": "now()",
            "error_message": str(e)
        }).eq("id", job_id).execute()
        
        raise  # Re-raise for Dramatiq retry logic
    
    finally:
        # Cleanup HTTP client
        asyncio.run(http_client.aclose())


async def _run_outlook_sync_with_cleanup(http_client: httpx.AsyncClient, supabase, rag_pipeline, user_id: str, provider_key: str):
    """
    Async wrapper that runs sync and handles HTTP client cleanup properly.
    """
    from app.services.sync import run_tenant_sync
    
    try:
        result = await run_tenant_sync(http_client, supabase, rag_pipeline, user_id, provider_key)
        return result
    finally:
        # Cleanup HTTP client in the same event loop
        await http_client.aclose()


@dramatiq.actor(max_retries=3)
def sync_outlook_task(user_id: str, job_id: str):
    """
    Background job for Outlook sync.
    
    Args:
        user_id: User/tenant ID
        job_id: Sync job ID for status tracking
    """
    from app.core.config import settings
    
    logger.info(f"🚀 Starting Outlook sync job {job_id} for user {user_id}")
    
    http_client, supabase, rag_pipeline = get_sync_dependencies()
    
    try:
        # Update job status to running
        supabase.table("sync_jobs").update({
            "status": "running",
            "started_at": "now()"
        }).eq("id", job_id).execute()
        
        # Run the sync with proper cleanup
        result = asyncio.run(_run_outlook_sync_with_cleanup(
            http_client, supabase, rag_pipeline,
            user_id, settings.nango_provider_key_outlook
        ))
        
        # Update job status to completed
        supabase.table("sync_jobs").update({
            "status": "completed",
            "completed_at": "now()",
            "result": result
        }).eq("id", job_id).execute()
        
        logger.info(f"✅ Outlook sync job {job_id} complete: {result.get('messages_synced', 0)} messages")
        return result
        
    except Exception as e:
        logger.error(f"❌ Outlook sync job {job_id} failed: {e}")

        # Update job status to failed
        supabase.table("sync_jobs").update({
            "status": "failed",
            "completed_at": "now()",
            "error_message": str(e)
        }).eq("id", job_id).execute()

        raise  # Re-raise for Dramatiq retry logic


# Entity deduplication is now handled by Render cron job (see app/services/deduplication/run_dedup_cli.py)


# ============================================================================
# QUICKBOOKS SYNC
# ============================================================================

async def _run_quickbooks_sync_with_cleanup(http_client: httpx.AsyncClient, supabase, rag_pipeline, user_id: str, provider_key: str):
    """
    Async wrapper that runs QuickBooks sync and handles HTTP client cleanup properly.
    """
    from app.services.sync.orchestration.quickbooks_sync import run_quickbooks_sync

    try:
        result = await run_quickbooks_sync(http_client, supabase, rag_pipeline, user_id, provider_key)
        return result
    finally:
        # Cleanup HTTP client in the same event loop
        await http_client.aclose()


@dramatiq.actor(max_retries=3)
def sync_quickbooks_task(user_id: str, job_id: str):
    """
    Background job for QuickBooks sync.

    Fetches invoices, bills, payments, customers from QuickBooks via Nango.
    Ingests each record as a document into Supabase + Knowledge Graph.

    Args:
        user_id: User/tenant ID
        job_id: Sync job ID for status tracking
    """
    from app.core.config import settings

    logger.info(f"🚀 Starting QuickBooks sync job {job_id} for user {user_id}")

    http_client, supabase, rag_pipeline = get_sync_dependencies()

    try:
        # Update job status to running
        supabase.table("sync_jobs").update({
            "status": "running",
            "started_at": "now()"
        }).eq("id", job_id).execute()

        # Run the sync with proper cleanup
        result = asyncio.run(_run_quickbooks_sync_with_cleanup(
            http_client, supabase, rag_pipeline,
            user_id, settings.nango_provider_key_quickbooks or "quickbooks"
        ))

        # Update job status to completed
        supabase.table("sync_jobs").update({
            "status": "completed",
            "completed_at": "now()",
            "result": result
        }).eq("id", job_id).execute()

        logger.info(f"✅ QuickBooks sync job {job_id} complete: {result.get('records_synced', 0)} records")
        return result

    except Exception as e:
        logger.error(f"❌ QuickBooks sync job {job_id} failed: {e}")

        # Update job status to failed
        supabase.table("sync_jobs").update({
            "status": "failed",
            "completed_at": "now()",
            "error_message": str(e)
        }).eq("id", job_id).execute()

        raise  # Re-raise for Dramatiq retry logic

    finally:
        # Cleanup HTTP client
        asyncio.run(http_client.aclose())

