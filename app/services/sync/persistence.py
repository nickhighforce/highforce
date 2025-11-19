"""
Email persistence helpers
Handles Supabase storage, JSONL debugging, and Cortex ingestion
"""
import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from supabase import Client

from app.services.rag import UniversalIngestionPipeline
from app.services.preprocessing.normalizer import ingest_document_universal
from app.services.identity import resolve_identity
from app.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# JSONL DEBUGGING
# ============================================================================

async def append_jsonl(email: Dict[str, Any]):
    """
    Append normalized email to JSONL file for debugging.

    Args:
        email: Normalized email dictionary
    """
    if not settings.save_jsonl:
        return

    try:
        with open("./outbox.jsonl", "a") as f:
            f.write(json.dumps(email) + "\n")
    except Exception as e:
        logger.error(f"Error writing to JSONL: {e}")


# ============================================================================
# HighForce INGESTION
# ============================================================================

async def ingest_to_cortex(
    cortex_pipeline: Optional[UniversalIngestionPipeline],
    email: Dict[str, Any],
    supabase: Optional[Client] = None
):
    """
    Ingest email using UNIVERSAL INGESTION FLOW.

    This uses the new unified system that:
    1. Normalizes email into universal format
    2. Saves to documents table (Supabase)
    3. Ingests to Qdrant vector store

    Args:
        cortex_pipeline: Cortex pipeline instance (or None if disabled)
        email: Normalized email dictionary
        supabase: Supabase client (for documents table)

    Returns:
        Dict with ingestion results or None if pipeline not initialized/failed
    """
    if not cortex_pipeline:
        logger.debug("Cortex pipeline not initialized, skipping ingestion")
        return None

    if not supabase:
        logger.warning("Supabase client not provided, using old ingestion flow")
        # Fallback to old method
        try:
            result = await cortex_pipeline.ingest_document(
                content=email.get("full_body", ""),
                document_name=email.get("subject", "No Subject"),
                source=email.get("source", "gmail"),
                document_type="email",
                reference_time=email.get("received_datetime"),
                metadata={
                    "message_id": email.get("message_id"),
                    "sender_name": email.get("sender_name", ""),
                    "sender_address": email.get("sender_address", ""),
                    "to_addresses": email.get("to_addresses", []),
                    "company_id": email.get("company_id"),
                    "user_id": email.get("user_id"),
                    "web_link": email.get("web_link", "")
                }
            )
            return result
        except Exception as e:
            logger.error(f"Error ingesting email: {e}")
            return None

    try:
        logger.info(f"📧 Universal ingestion: {email.get('subject', 'No Subject')[:50]}...")

        # Parse received_datetime
        received_dt = None
        if email.get("received_datetime"):
            try:
                if isinstance(email["received_datetime"], str):
                    received_dt = datetime.fromisoformat(email["received_datetime"].replace('Z', '+00:00'))
                else:
                    received_dt = email["received_datetime"]
            except Exception as e:
                logger.warning(f"Failed to parse received_datetime: {e}")

        # Resolve sender identity (map platform-specific ID to canonical identity)
        sender_identity = None
        sender_canonical_id = None
        sender_canonical_name = None

        if email.get("sender_address"):
            try:
                sender_identity = await resolve_identity(
                    supabase=supabase,
                    company_id=email.get("company_id"),
                    platform=email.get("source", "gmail"),  # 'gmail' or 'outlook'
                    email=email.get("sender_address"),
                    platform_user_id=email.get("sender_address"),  # Use email as platform ID
                    display_name=email.get("sender_name")
                )
                sender_canonical_id = sender_identity.get("canonical_identity_id")
                sender_canonical_name = sender_identity.get("canonical_name")

                logger.info(
                    f"🔗 Identity resolved: {email.get('sender_address')} → "
                    f"{sender_canonical_name} ({sender_canonical_id})"
                )
            except Exception as e:
                logger.warning(f"Failed to resolve sender identity: {e}")

        # Use universal ingestion function
        logger.info(f"🔍 DEBUG: About to ingest email - company_id: {email.get('company_id')}, message_id: {email.get('message_id')}")

        # CANONICAL ID: Use thread_id for email deduplication (groups conversation)
        from app.services.sync.canonical import get_canonical_id

        canonical_source_id = get_canonical_id(
            source=email.get("source", "gmail"),
            thread_id=email.get("thread_id", ""),
            fallback_id=email.get("message_id")
        )

        logger.info(f"   📝 Canonical ID: {canonical_source_id}")

        result = await ingest_document_universal(
            supabase=supabase,
            cortex_pipeline=cortex_pipeline,
            company_id=email.get("company_id"),
            source=email.get("source", "gmail"),
            source_id=canonical_source_id,
            document_type="email",
            title=email.get("subject", "No Subject"),
            content=email.get("full_body", ""),
            raw_data=email,  # Preserve full email structure
            source_created_at=received_dt,
            metadata={
                "sender_name": email.get("sender_name", ""),
                "sender_address": email.get("sender_address", ""),
                "to_addresses": email.get("to_addresses", []),
                "user_id": email.get("user_id"),
                "web_link": email.get("web_link", ""),
                # IDENTITY RESOLUTION: Add canonical identity metadata
                "canonical_identity_id": sender_canonical_id,
                "canonical_name": sender_canonical_name,
                # THREAD DEDUPLICATION: Add thread metadata
                "thread_id": email.get("thread_id", ""),
                "message_id": email.get("message_id", "")
            }
        )
        
        logger.info(f"🔍 DEBUG: Ingestion result: {result}")

        if result['status'] == 'success':
            logger.info(f"✅ Universal ingestion successful")
        else:
            logger.error(f"❌ Universal ingestion failed: {result.get('error')}")

        return result

    except Exception as e:
        logger.error(f"Error in universal ingestion: {e}")
        return None
