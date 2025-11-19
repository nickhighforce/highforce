"""
Webhook Routes
Handles Nango webhook events and triggers background syncs
"""
import logging
import httpx
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends
from supabase import Client

from app.core.config import settings
from app.core.dependencies import get_http_client, get_supabase, get_rag_pipeline
from app.models.schemas import NangoWebhook
from app.services.nango import run_gmail_sync, run_tenant_sync
from app.services.nango import save_connection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nango", tags=["webhook"])


@router.post("/webhook")
async def nango_webhook(
    payload: dict,  # Accept raw dict to see what's coming in
    background_tasks: BackgroundTasks,
    http_client: httpx.AsyncClient = Depends(get_http_client),
    supabase: Client = Depends(get_supabase),
    rag_pipeline: Optional[any] = Depends(get_rag_pipeline)
):
    """
    Handle Nango webhook - triggers background sync.
    
    Webhook types:
    - auth: OAuth completion (success/failure)
    - sync: Incremental sync completion
    - forward: Passthrough API calls
    """
    logger.info(f"Received Nango webhook (raw): {payload}")
    
    # Parse webhook (flexible for different event types)
    webhook_type = payload.get("type")
    nango_connection_id = payload.get("connectionId")
    provider_key = payload.get("providerConfigKey")
    
    logger.info(f"Webhook parsed: type={webhook_type}, connection={nango_connection_id}, provider={provider_key}")

    # Handle auth events
    if webhook_type == "auth" and payload.get("success"):
        logger.info(f"[WEBHOOK_AUTH] ========== AUTH WEBHOOK RECEIVED ==========")
        logger.info(f"[WEBHOOK_AUTH] Connection ID: {nango_connection_id}")
        logger.info(f"[WEBHOOK_AUTH] Provider: {provider_key}")
        logger.info(f"[WEBHOOK_AUTH] Full payload: {payload}")

        try:
            # Extract user information from endUser
            end_user_id = None
            end_user_email = None
            company_id = None

            logger.debug(f"[WEBHOOK_AUTH] Extracting endUser from payload...")
            end_user = payload.get('endUser') or payload.get('end_user')

            if end_user:
                logger.debug(f"[WEBHOOK_AUTH] endUser found in payload: {end_user}")
                end_user_id = end_user.get("endUserId") or end_user.get("id")
                end_user_email = end_user.get("email")
                company_id = end_user.get("organization_id") or end_user.get("organizationId")

                logger.info(f"[WEBHOOK_AUTH] Extracted from payload:")
                logger.info(f"[WEBHOOK_AUTH]   user_id: {end_user_id}")
                logger.info(f"[WEBHOOK_AUTH]   user_email: {end_user_email}")
                logger.info(f"[WEBHOOK_AUTH]   company_id: {company_id}")
            else:
                logger.warning(f"[WEBHOOK_AUTH] ⚠️  No endUser in payload, will fetch from Nango API")

            # Fallback: fetch from Nango API if not in payload
            if not end_user_id or not company_id:
                logger.info(f"[WEBHOOK_AUTH] Missing data, fetching from Nango API...")
                logger.debug(f"[WEBHOOK_AUTH]   Missing user_id: {not end_user_id}")
                logger.debug(f"[WEBHOOK_AUTH]   Missing company_id: {not company_id}")

                conn_url = f"https://api.nango.dev/connection/{nango_connection_id}?provider_config_key={provider_key}"
                logger.debug(f"[WEBHOOK_AUTH] Fetching: {conn_url}")
                logger.debug(f"[WEBHOOK_AUTH] Using Nango secret: {settings.nango_secret[:10]}... (truncated for logging)")

                headers = {"Authorization": f"Bearer {settings.nango_secret}"}
                response = await http_client.get(conn_url, headers=headers)

                logger.debug(f"[WEBHOOK_AUTH] Nango API response status: {response.status_code}")
                response.raise_for_status()

                conn_data = response.json()
                logger.debug(f"[WEBHOOK_AUTH] Connection data keys: {list(conn_data.keys())}")

                end_user_data = conn_data.get("end_user", {}) if isinstance(conn_data.get("end_user"), dict) else {}
                logger.debug(f"[WEBHOOK_AUTH] end_user_data: {end_user_data}")

                if not end_user_id:
                    end_user_id = end_user_data.get("id")
                    logger.info(f"[WEBHOOK_AUTH] Fetched user_id from API: {end_user_id}")
                if not end_user_email:
                    end_user_email = end_user_data.get("email")
                    logger.info(f"[WEBHOOK_AUTH] Fetched user_email from API: {end_user_email}")
                if not company_id:
                    company_id = end_user_data.get("organization_id") or end_user_data.get("organizationId")
                    logger.info(f"[WEBHOOK_AUTH] Fetched company_id from API: {company_id}")

            # Validation
            if not end_user_id:
                logger.error(f"[WEBHOOK_AUTH] ❌ VALIDATION FAILED - No user_id")
                logger.error(f"[WEBHOOK_AUTH]   connection_id: {nango_connection_id}")
                logger.error(f"[WEBHOOK_AUTH]   provider: {provider_key}")
                logger.error(f"[WEBHOOK_AUTH]   Payload had endUser: {bool(payload.get('endUser') or payload.get('end_user'))}")
                return {"status": "error", "message": "Missing end_user information"}

            # If company_id not in payload, look it up from Master Supabase
            if not company_id:
                logger.info(f"[WEBHOOK_AUTH] company_id not in Nango payload, looking up in Master Supabase...")
                from app.core.config import settings as master_config
                from supabase import create_client

                try:
                    master_supabase = create_client(
                        master_config.master_supabase_url,
                        master_config.master_supabase_service_key
                    )

                    # Look up user's company from company_users table
                    result = master_supabase.table("company_users")\
                        .select("company_id")\
                        .eq("user_id", end_user_id)\
                        .eq("is_active", True)\
                        .limit(1)\
                        .execute()

                    if result.data and len(result.data) > 0:
                        company_id = result.data[0]["company_id"]
                        logger.info(f"[WEBHOOK_AUTH] ✅ Found company_id from Master Supabase: {company_id}")
                    else:
                        logger.error(f"[WEBHOOK_AUTH] ❌ User {end_user_id} not found in company_users table")
                        return {"status": "error", "message": "User not associated with any company"}
                except Exception as lookup_error:
                    logger.error(f"[WEBHOOK_AUTH] ❌ Failed to lookup company_id: {lookup_error}")
                    return {"status": "error", "message": "Failed to lookup company_id"}

            if not company_id:
                logger.error(f"[WEBHOOK_AUTH] ❌ VALIDATION FAILED - No company_id after lookup")
                logger.error(f"[WEBHOOK_AUTH]   user_id: {end_user_id}")
                logger.error(f"[WEBHOOK_AUTH]   connection_id: {nango_connection_id}")
                logger.error(f"[WEBHOOK_AUTH]   provider: {provider_key}")
                return {"status": "error", "message": "Missing company_id information"}

            logger.info(f"[WEBHOOK_AUTH] ✅ Validation passed - user_id={end_user_id}, company_id={company_id}")
            logger.info(f"[WEBHOOK_AUTH] Saving connection to database...")

            # Save connection with full user attribution
            await save_connection(
                company_id=company_id,
                provider_key=provider_key,
                connection_id=nango_connection_id,
                user_id=end_user_id,
                user_email=end_user_email
            )

            logger.info(f"[WEBHOOK_AUTH] ✅ SUCCESS - Connection saved!")
            logger.info(f"[WEBHOOK_AUTH]   Nango connection_id: {nango_connection_id}")
            logger.info(f"[WEBHOOK_AUTH]   user_id: {end_user_id}")
            logger.info(f"[WEBHOOK_AUTH]   company_id: {company_id}")
            logger.info(f"[WEBHOOK_AUTH]   provider: {provider_key}")
            logger.info(f"[WEBHOOK_AUTH] ========================================")

            return {"status": "connection_saved", "user": end_user_id, "company": company_id}

        except httpx.HTTPStatusError as e:
            logger.error(f"[WEBHOOK_AUTH] ❌ HTTP ERROR fetching from Nango")
            logger.error(f"[WEBHOOK_AUTH]   Status: {e.response.status_code}")
            logger.error(f"[WEBHOOK_AUTH]   Response: {e.response.text}")
            logger.exception("[WEBHOOK_AUTH] Full traceback:")
            return {"status": "error", "message": f"Nango API error: {e.response.status_code}"}

        except Exception as e:
            logger.error(f"[WEBHOOK_AUTH] ❌ UNEXPECTED ERROR handling auth webhook")
            logger.error(f"[WEBHOOK_AUTH]   Error type: {type(e).__name__}")
            logger.error(f"[WEBHOOK_AUTH]   Error message: {str(e)}")
            logger.error(f"[WEBHOOK_AUTH]   connection_id: {nango_connection_id}")
            logger.error(f"[WEBHOOK_AUTH]   provider: {provider_key}")
            logger.exception("[WEBHOOK_AUTH] Full traceback:")
            return {"status": "error", "message": str(e)}

    # Handle sync events - get company_id
    if webhook_type == "sync":
        logger.info(f"Sync webhook received: model={payload.get('model')}, success={payload.get('success')}")
        # For now, just acknowledge sync webhooks
        return {"status": "sync_acknowledged"}
    
    # Other webhook types - get company_id
    try:
        conn_url = f"https://api.nango.dev/connection/{provider_key}/{nango_connection_id}"
        headers = {"Authorization": f"Bearer {settings.nango_secret}"}
        response = await http_client.get(conn_url, headers=headers)
        response.raise_for_status()
        conn_data = response.json()
        company_id = conn_data.get("end_user", {}).get("id")

        if not company_id:
            logger.error(f"No end_user.id found for connection {nango_connection_id}")
            return {"status": "error", "message": "Missing end_user information"}

    except Exception as e:
        logger.error(f"Error fetching end_user from Nango: {e}")
        return {"status": "error", "message": str(e)}

    # Trigger background sync
    if payload.providerConfigKey == settings.nango_provider_key_gmail:
        background_tasks.add_task(run_gmail_sync, http_client, supabase, rag_pipeline, company_id, payload.providerConfigKey)
        logger.info(f"Triggered Gmail sync for tenant {company_id}")
    else:
        background_tasks.add_task(run_tenant_sync, http_client, supabase, rag_pipeline, company_id, payload.providerConfigKey)
        logger.info(f"Triggered Outlook sync for tenant {company_id}")

    return {"status": "accepted"}
