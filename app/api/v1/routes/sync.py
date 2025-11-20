"""
Sync Routes
Background job-based sync endpoints for Gmail, Drive, and Outlook
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from supabase import Client, create_client

from app.core.security import get_current_user_id, get_current_user_context
from app.core.dependencies import get_supabase
from app.services.background.tasks import sync_gmail_task, sync_drive_task, sync_outlook_task, sync_quickbooks_task
from app.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


async def check_can_manual_sync(
    user_id: str,
    company_id: str,
    provider: str,
    supabase: Client
) -> tuple[bool, str]:
    """
    Check if user can manually trigger sync.
    Checks both local flag AND master admin override.

    Returns:
        (can_sync: bool, reason: str)
    """
    # 1. Check local flag in company Supabase
    # CRITICAL: Use company_id to query connections (company-wide OAuth model)
    conn_result = supabase.table("connections")\
        .select("can_manual_sync, initial_sync_completed")\
        .eq("company_id", company_id)\
        .eq("provider_key", provider)\
        .maybe_single()\
        .execute()

    if not conn_result.data:
        # No connection exists yet - allow first sync
        return True, "first_sync"

    can_sync_locally = conn_result.data.get("can_manual_sync", True)

    # 2. Check admin override in master Supabase (if multi-tenant)
    can_sync_override = False
    override_source = None

    try:
        from app.core.config import Settings as MasterConfig
        master_config = MasterConfig()

        if master_config.is_multi_tenant:
            master_supabase = create_client(
                master_config.master_supabase_url,
                master_config.master_supabase_service_key
            )

            override_result = master_supabase.table("sync_permissions")\
                .select("can_manual_sync_override, override_reason")\
                .eq("company_id", company_id)\
                .maybe_single()\
                .execute()

            if override_result.data:
                override_value = override_result.data.get("can_manual_sync_override")
                if override_value is not None:  # NULL means no override
                    can_sync_override = override_value
                    override_source = "admin_override"
                    logger.info(f"🔓 Admin override for {company_id}: {override_value} ({override_result.data.get('override_reason')})")
    except Exception as e:
        logger.warning(f"Could not check admin override: {e}")

    # 3. Final decision
    can_sync = can_sync_locally or can_sync_override

    if can_sync_locally and not can_sync_override:
        return True, "local_permission"
    elif can_sync_override and not can_sync_locally:
        return True, "admin_override"
    elif can_sync_locally and can_sync_override:
        return True, "both_allowed"
    else:
        return False, "locked"


@router.post("/initial/{provider}")
@limiter.limit("100/hour")  # Increased for testing/debugging
async def trigger_initial_sync(
    provider: str,
    request: Request,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Trigger ONE-TIME historical sync (1 year backfill).
    After this, manual sync is LOCKED and button disappears.

    Admin can override via master dashboard if needed for troubleshooting.

    Supported providers: outlook, gmail, drive, quickbooks
    """
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]

    logger.info(f"Initial sync requested: {provider} for user {user_id}, company {company_id}")

    # Validate provider
    valid_providers = ["outlook", "gmail", "drive", "quickbooks"]
    if provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider. Must be one of: {', '.join(valid_providers)}"
        )

    # Check if sync is allowed
    can_sync, reason = await check_can_manual_sync(user_id, company_id, provider, supabase)

    if not can_sync:
        raise HTTPException(
            status_code=403,
            detail="Manual sync is locked. Initial sync already completed. Contact support if you need to re-sync."
        )

    # Lock manual sync (set to FALSE)
    # CRITICAL: Use company_id for company_id (company-wide OAuth model)
    supabase.table("connections")\
        .upsert({
            "company_id": company_id,
            "provider_key": provider,
            "connection_id": company_id,  # Use company_id as connection_id
            "can_manual_sync": False,
            "initial_sync_started_at": datetime.utcnow().isoformat(),
            "sync_lock_reason": "Initial historical sync started"
        }, on_conflict="company_id,provider_key")\
        .execute()

    # If admin override was used, log and remove it (one-time unlock)
    if reason == "admin_override":
        try:
            from app.core.config import Settings as MasterConfig
            master_config = MasterConfig()

            if master_config.is_multi_tenant:
                master_supabase = create_client(
                    master_config.master_supabase_url,
                    master_config.master_supabase_service_key
                )

                logger.info(f"🔓 Admin override used for {company_id}:{provider}. Removing override.")

                # Remove override after use (one-time unlock)
                master_supabase.table("sync_permissions")\
                    .update({"can_manual_sync_override": None})\
                    .eq("company_id", company_id)\
                    .execute()
        except Exception as e:
            logger.error(f"Failed to remove admin override: {e}")

    # Create sync job
    job = supabase.table("sync_jobs").insert({
        "company_id": company_id,  # CRITICAL: Required for multi-tenant isolation
        "user_id": user_id,
        "job_type": provider,
        "status": "queued"
    }).execute()

    job_id = job.data[0]["id"]

    # Enqueue background task based on provider
    # CRITICAL: Pass company_id as company_id (company-wide OAuth connections)
    if provider == "outlook":
        sync_outlook_task.send(company_id, job_id)
    elif provider == "gmail":
        # Calculate 1 year ago
        one_year_ago = (datetime.utcnow() - timedelta(days=365)).isoformat()
        sync_gmail_task.send(company_id, job_id, one_year_ago)
    elif provider == "drive":
        sync_drive_task.send(company_id, job_id, None)  # Sync entire drive
    elif provider == "quickbooks":
        sync_quickbooks_task.send(company_id, job_id)

    logger.info(f"🔒 Initial sync started for company {company_id}:{provider} (triggered by user {user_id}). Manual sync LOCKED. Job ID: {job_id}")

    return {
        "status": "started",
        "job_id": job_id,
        "provider": provider,
        "backfill_days": 365,
        "locked": True,
        "message": "Historical sync started. Manual sync is now locked. You'll receive an email when complete (4-8 hours)."
    }


@router.get("/once")
@limiter.limit("100/hour")  # Increased for testing/debugging
async def sync_once(
    request: Request,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Start Outlook sync as background job.

    SECURITY: Only syncs for the authenticated company.
    Multi-tenant isolation enforced by company_id.

    Returns immediately with job_id for status tracking.
    """
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]

    logger.info(f"Enqueueing Outlook sync for company {company_id} (user {user_id})")
    try:
        # Create job record
        job = supabase.table("sync_jobs").insert({
            "company_id": company_id,  # CRITICAL: Required for multi-tenant isolation
            "user_id": user_id,
            "job_type": "outlook",
            "status": "queued"
        }).execute()

        job_id = job.data[0]["id"]

        # Enqueue background task (use company_id for connection lookup)
        sync_outlook_task.send(company_id, job_id)

        logger.info(f"✅ Outlook sync job {job_id} queued")

        return {
            "status": "queued",
            "job_id": job_id,
            "message": "Outlook sync started in background. Use GET /sync/jobs/{job_id} to check status."
        }
    except Exception as e:
        logger.error(f"Error enqueueing Outlook sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/once/gmail")
@limiter.limit("100/hour")  # Increased for testing/debugging
async def sync_once_gmail(
    request: Request,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase),
    modified_after: Optional[str] = Query(None, description="ISO datetime to filter records")
):
    """
    Start Gmail sync as background job.

    SECURITY: Only syncs for the authenticated company.
    Multi-tenant isolation enforced by company_id.

    Returns immediately with job_id for status tracking.
    """
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]

    logger.info(f"Enqueueing Gmail sync for company {company_id} (user {user_id})")
    if modified_after:
        logger.info(f"Using modified_after filter: {modified_after}")

    try:
        # Create job record
        job = supabase.table("sync_jobs").insert({
            "company_id": company_id,  # CRITICAL: Required for multi-tenant isolation
            "user_id": user_id,
            "job_type": "gmail",
            "status": "queued"
        }).execute()

        job_id = job.data[0]["id"]

        # Enqueue background task (use company_id for connection lookup)
        sync_gmail_task.send(company_id, job_id, modified_after)

        logger.info(f"✅ Gmail sync job {job_id} queued")

        return {
            "status": "queued",
            "job_id": job_id,
            "message": "Gmail sync started in background. Use GET /sync/jobs/{job_id} to check status."
        }
    except Exception as e:
        logger.error(f"Error enqueueing Gmail sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/once/drive")
@limiter.limit("100/hour")  # Increased for testing/debugging
async def sync_once_drive(
    request: Request,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase),
    folder_ids: Optional[str] = Query(None, description="Comma-separated folder IDs to sync (empty = entire Drive)")
):
    """
    Start Google Drive sync as background job.
    Returns immediately with job_id for status tracking.

    Examples:
    - Sync entire Drive: GET /sync/once/drive
    - Sync specific folders: GET /sync/once/drive?folder_ids=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE,0BxiMVs...
    """
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]

    logger.info(f"Enqueueing Drive sync for company {company_id} (user {user_id})")

    # Parse folder IDs
    folder_list = None
    if folder_ids:
        folder_list = [fid.strip() for fid in folder_ids.split(",") if fid.strip()]
        logger.info(f"Syncing specific folders: {folder_list}")
    else:
        logger.info("Syncing entire Drive")

    try:
        # Create job record
        job = supabase.table("sync_jobs").insert({
            "company_id": company_id,  # CRITICAL: Required for multi-tenant isolation
            "user_id": user_id,
            "job_type": "drive",
            "status": "queued"
        }).execute()

        job_id = job.data[0]["id"]

        # Enqueue background task (use company_id for connection lookup)
        sync_drive_task.send(company_id, job_id, folder_list)
        
        logger.info(f"✅ Drive sync job {job_id} queued")
        
        return {
            "status": "queued",
            "job_id": job_id,
            "message": "Drive sync started in background. Use GET /sync/jobs/{job_id} to check status."
        }
    except Exception as e:
        logger.error(f"Error enqueueing Drive sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/once/quickbooks")
@limiter.limit("100/hour")  # Increased for testing/debugging
async def sync_once_quickbooks(
    request: Request,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Start QuickBooks sync as background job.

    Fetches invoices, bills, payments, customers from QuickBooks.
    Each record is ingested as a document into Supabase + Knowledge Graph.

    Returns immediately with job_id for status tracking.
    """
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]

    logger.info(f"Enqueueing QuickBooks sync for company {company_id} (user {user_id})")

    try:
        # Create job record
        job = supabase.table("sync_jobs").insert({
            "company_id": company_id,  # CRITICAL: Required for multi-tenant isolation
            "user_id": user_id,
            "job_type": "quickbooks",
            "status": "queued"
        }).execute()

        job_id = job.data[0]["id"]

        # Enqueue background task (use company_id for connection lookup)
        sync_quickbooks_task.send(company_id, job_id)

        logger.info(f"✅ QuickBooks sync job {job_id} queued")

        return {
            "status": "queued",
            "job_id": job_id,
            "message": "QuickBooks sync started in background. Use GET /sync/jobs/{job_id} to check status."
        }
    except Exception as e:
        logger.error(f"Error enqueueing QuickBooks sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase)
):
    """
    Get status of a background sync job.

    Returns:
    - status: queued, running, completed, failed
    - started_at: When job started processing
    - completed_at: When job finished
    - result: Job results (messages_synced, files_synced, etc)
    - error_message: Error details if failed
    """
    try:
        result = supabase.table("sync_jobs").select("*").eq("id", job_id).eq("user_id", user_id).single().execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return result.data
    except Exception as e:
        logger.error(f"Error fetching job status: {e}")
        raise HTTPException(status_code=500, detail=str(e))
