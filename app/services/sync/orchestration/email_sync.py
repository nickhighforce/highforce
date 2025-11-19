"""
Email sync orchestration engine
Coordinates Outlook and Gmail sync operations
"""
import base64
import logging
import threading
from typing import Any, Dict, List, Optional

import httpx
from supabase import Client

from app.services.rag import UniversalIngestionPipeline
from app.services.sync.database import get_connection, get_gmail_cursor, set_gmail_cursor
from app.services.sync.providers.gmail import normalize_gmail_message, download_gmail_attachment, is_supported_attachment_type
from app.services.sync.providers.outlook import normalize_outlook_message
from app.services.sync.oauth import nango_list_email_records
from app.services.sync.persistence import append_jsonl, ingest_to_cortex
from app.services.preprocessing.normalizer import ingest_document_universal
from app.services.preprocessing.spam_filter import should_filter_email
from app.core.config import settings

logger = logging.getLogger(__name__)

# Thread lock for cursor management (prevents race conditions in multi-threaded sync)
# Multiple threads fetching emails simultaneously could read the same cursor,
# causing duplicate processing. This lock ensures only one thread accesses
# cursor at a time.
_cursor_lock = threading.Lock()


# ============================================================================
# OUTLOOK SYNC
# ============================================================================

async def run_tenant_sync(
    http_client: httpx.AsyncClient,
    supabase: Client,
    cortex_pipeline: Optional[UniversalIngestionPipeline],
    company_id: str,
    provider_key: str
) -> Dict[str, Any]:
    """
    Run a full Outlook sync for a tenant using Nango unified API.
    
    Fetches pre-synced emails from Nango's database (no direct Microsoft Graph calls!).

    Args:
        http_client: Async HTTP client instance
        supabase: Supabase client instance
        cortex_pipeline: Cortex pipeline instance (or None if disabled)
        company_id: Tenant identifier
        provider_key: Provider configuration key

    Returns:
        Dictionary with sync statistics
    """
    logger.info(f"🚀 Starting Outlook sync for tenant {company_id} (via Nango unified API)")

    messages_synced = 0
    total_filtered = 0  # Track total spam filtered across all batches
    errors = []

    try:
        # Get connection ID
        connection_id = await get_connection(company_id, provider_key)
        if not connection_id:
            error_msg = f"No Outlook connection found for tenant {company_id}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {
                "status": "error",
                "company_id": company_id,
                "messages_synced": 0,
                "errors": errors
            }

        # Paginate through all Outlook records from Nango
        cursor = None
        has_more = True

        while has_more:
            try:
                # THREAD-SAFE: Lock cursor read/fetch to prevent race conditions
                with _cursor_lock:
                    # Fetch page of pre-synced records from Nango
                    # REDUCED to 10 to prevent memory/timeout issues with large email batches
                    result = await nango_list_email_records(
                        http_client,
                        provider_key,
                        connection_id,
                        cursor=cursor,
                        limit=10
                    )

                    records = result.get("records", [])
                    next_cursor = result.get("next_cursor")

                logger.info(f"📬 Fetched {len(records)} Outlook records from Nango (cursor: {cursor[:20] if cursor else 'none'}...)")

                # Process each record
                filtered_count = 0
                filtered_emails = []  # Track what got filtered for debugging
                for record in records:
                    try:
                        # Normalize Outlook message (Nango format → our format) - FULL EMAIL
                        normalized = normalize_outlook_message(record, company_id)

                        # Spam filtering (only if enabled) - uses TRUNCATED version for classification
                        if settings.enable_spam_filtering:
                            should_skip = should_filter_email({
                                'subject': normalized.get('subject', ''),
                                'body': normalized.get('full_body', ''),
                                'sender': normalized.get('sender_address', '')
                            })

                            if should_skip:
                                filtered_count += 1
                                filtered_emails.append({
                                    'subject': normalized.get('subject', 'No Subject')[:60],
                                    'sender': normalized.get('sender_address', 'Unknown')
                                })
                                continue  # Skip ingestion but log it
                        
                        # Universal ingestion (documents table + Neo4j + Qdrant) - FULL EMAIL
                        email_result = await ingest_to_cortex(cortex_pipeline, normalized, supabase)

                        # Optionally write to JSONL - FULL EMAIL
                        await append_jsonl(normalized)

                        messages_synced += 1

                        # Process attachments (if any) - NEW: Uses pre-downloaded content from Nango!
                        attachments = normalized.get("attachments", [])
                        if attachments and cortex_pipeline and supabase:
                            logger.info(f"   📎 Processing {len(attachments)} attachments for Outlook message {normalized['message_id']}")
                            
                            # Get parent email document ID for linking (BRILLIANT IDEA FROM USER!)
                            parent_doc_id = email_result.get('document_id') if email_result else None
                            parent_email_content = normalized.get('full_body', '')  # Email content for context

                            for attachment in attachments:
                                try:
                                    filename = attachment.get("filename", "attachment")
                                    mime_type = attachment.get("mimeType", "")
                                    attachment_id = attachment.get("attachmentId") or attachment.get("id")
                                    size = attachment.get("size", 0)
                                    is_inline = attachment.get("isInline", False)
                                    content_id = attachment.get("contentId")
                                    user_id = attachment.get("userId", "me")  # CRITICAL: userId for multi-mailbox support!

                                    # Skip if not supported
                                    if not is_supported_attachment_type(mime_type):
                                        logger.debug(f"      ⏭️  Skipping unsupported attachment: {filename} ({mime_type})")
                                        continue

                                    # Skip large files (>10MB)
                                    if size > 10 * 1024 * 1024:
                                        logger.warning(f"      ⏭️  Skipping large attachment: {filename} ({size} bytes)")
                                        continue

                                    # Download attachment via Nango action (proper binary handling!)
                                    logger.info(f"      📥 Downloading via Nango action: {filename} ({'inline CID' if is_inline else 'regular'})")
                                    
                                    from app.services.sync.oauth import nango_fetch_attachment
                                    
                                    attachment_bytes = await nango_fetch_attachment(
                                        http_client=http_client,
                                        provider_key=provider_key,
                                        connection_id=connection_id,
                                        thread_id=normalized["message_id"],
                                        attachment_id=attachment_id,
                                        user_id=user_id  # Pass actual userId for multi-mailbox!
                                    )

                                    # Universal ingestion (Unstructured.io parses it!)
                                    attachment_title = f"[Outlook Attachment] {filename}"
                                    if is_inline and content_id:
                                        attachment_title = f"[Outlook Embedded] {filename} (CID: {content_id})"
                                    
                                    await ingest_document_universal(
                                        supabase=supabase,
                                        cortex_pipeline=cortex_pipeline,
                                        company_id=company_id,
                                        source="outlook",
                                        source_id=f"{normalized['message_id']}_{attachment_id}",  # Unique ID
                                        document_type="attachment",
                                        title=attachment_title,
                                        file_bytes=attachment_bytes,
                                        filename=filename,
                                        file_type=mime_type,
                                        raw_data=attachment,  # Preserve attachment metadata
                                        metadata={
                                            "email_subject": normalized.get("subject"),
                                            "email_id": normalized["message_id"],
                                            "sender": normalized.get("sender_address"),
                                            "attached_to": "email",
                                            "is_inline": is_inline,
                                            "content_id": content_id
                                        },
                                        # LINK TO PARENT EMAIL (USER'S BRILLIANT IDEA!)
                                        parent_document_id=parent_doc_id,
                                        parent_email_content=parent_email_content
                                    )

                                    logger.info(f"      ✅ Outlook attachment ingested: {filename}")

                                except Exception as e:
                                    error_msg = f"Error processing Outlook attachment {filename}: {e}"
                                    logger.error(f"      ❌ {error_msg}")
                                    errors.append(error_msg)

                    except Exception as e:
                        error_msg = f"Error processing Outlook message: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                
                if filtered_count > 0:
                    total_filtered += filtered_count
                    logger.info(f"🚫 Filtered {filtered_count} spam/newsletter emails from this batch")
                    # Log first 5 filtered emails for debugging
                    for i, email in enumerate(filtered_emails[:5]):
                        logger.info(f"   {i+1}. '{email['subject']}' from {email['sender']}")
                    if len(filtered_emails) > 5:
                        logger.info(f"   ... and {len(filtered_emails) - 5} more")

                # THREAD-SAFE: Lock cursor update
                with _cursor_lock:
                    # Update cursor for next page
                    if next_cursor:
                        cursor = next_cursor
                        logger.info(f"   ⏭️  Moving to next page (cursor: {cursor[:30]}...)")
                    else:
                        logger.info(f"   ✅ No more pages - sync complete!")
                        has_more = False

            except Exception as e:
                error_msg = f"Error fetching Outlook page from Nango: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                has_more = False

        # Final summary with spam filter stats
        total_processed = messages_synced + total_filtered
        filter_percent = (total_filtered / total_processed * 100) if total_processed > 0 else 0

        logger.info(f"✅ Outlook sync completed for tenant {company_id}")
        logger.info(f"   📊 Total processed: {total_processed} emails")
        logger.info(f"   ✅ Ingested: {messages_synced} business emails")
        logger.info(f"   🚫 Filtered: {total_filtered} spam/newsletters ({filter_percent:.1f}%)")

        return {
            "status": "success" if not errors else "partial_success",
            "company_id": company_id,
            "messages_synced": messages_synced,
            "emails_filtered": total_filtered,
            "total_processed": total_processed,
            "errors": errors
        }

    except Exception as e:
        error_msg = f"Fatal error during Outlook sync: {e}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "status": "error",
            "company_id": company_id,
            "messages_synced": messages_synced,
            "errors": errors
        }


# ============================================================================
# GMAIL SYNC
# ============================================================================

async def run_gmail_sync(
    http_client: httpx.AsyncClient,
    supabase: Client,
    cortex_pipeline: Optional[UniversalIngestionPipeline],
    company_id: str,
    provider_key: str,
    modified_after: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run a full Gmail sync for a tenant using Nango unified API.

    Args:
        http_client: Async HTTP client instance
        supabase: Supabase client instance
        cortex_pipeline: Cortex pipeline instance (or None if disabled)
        company_id: Tenant identifier
        provider_key: Nango provider configuration key
        modified_after: Optional ISO datetime to filter records

    Returns:
        Dictionary with sync statistics
    """
    logger.info(f"Starting Gmail sync for tenant {company_id}")

    messages_synced = 0
    errors = []

    try:
        # Get connection ID
        connection_id = await get_connection(company_id, provider_key)
        if not connection_id:
            error_msg = f"No Gmail connection found for tenant {company_id}"
            logger.error(error_msg)
            errors.append(error_msg)
            return {
                "status": "error",
                "company_id": company_id,
                "messages_synced": 0,
                "errors": errors
            }

        # Get stored cursor for incremental sync
        stored_cursor = await get_gmail_cursor(company_id, provider_key)
        cursor = stored_cursor

        # Override with modified_after for manual testing if provided
        # Note: modified_after is a FILTER, not a limit - it will still paginate through
        # all emails after that date. For full syncs, don't use modified_after at all.
        if modified_after:
            cursor = None
            logger.info(f"Using modified_after filter: {modified_after}")
        elif not stored_cursor:
            # First sync - sync all emails (no date filter)
            logger.info(f"First sync detected - syncing all emails")

        # Paginate through all Gmail records
        has_more = True
        while has_more:
            try:
                # THREAD-SAFE: Lock cursor read/fetch to prevent race conditions
                # Without this, multiple threads could read same cursor simultaneously
                # and process duplicate emails
                with _cursor_lock:
                    # Fetch page of records
                    result = await nango_list_email_records(
                        http_client,
                        provider_key,
                        connection_id,
                        cursor=cursor,
                        limit=100,
                        modified_after=modified_after
                    )

                    records = result.get("records", [])
                    next_cursor = result.get("next_cursor")

                logger.info(f"Fetched {len(records)} Gmail records (cursor: {cursor[:20] if cursor else 'none'}...)")

                # Process each record
                for record in records:
                    try:
                        # Normalize Gmail message
                        normalized = normalize_gmail_message(record, company_id)

                        # Ingest email body using UNIVERSAL FLOW (documents table + Qdrant)
                        cortex_result = await ingest_to_cortex(
                            cortex_pipeline,
                            normalized,
                            supabase  # Pass supabase for universal ingestion
                        )

                        # Optionally write to JSONL
                        await append_jsonl(normalized)

                        messages_synced += 1

                        # Process attachments (if any)
                        attachments = normalized.get("attachments", [])
                        if attachments and cortex_pipeline and supabase:
                            logger.info(f"   📎 Processing {len(attachments)} attachments for message {normalized['message_id']}")

                            # Get Gmail access token via Nango
                            access_token = await get_graph_token_via_nango(
                                http_client,
                                provider_key,
                                connection_id
                            )

                            for attachment in attachments:
                                try:
                                    filename = attachment.get("filename", "attachment")
                                    mime_type = attachment.get("mimeType", "")
                                    attachment_id = attachment.get("attachmentId") or attachment.get("id")
                                    size = attachment.get("size", 0)

                                    # Skip if not supported
                                    if not is_supported_attachment_type(mime_type):
                                        logger.debug(f"      ⏭️  Skipping unsupported attachment: {filename} ({mime_type})")
                                        continue

                                    # Skip large files (>10MB)
                                    if size > 10 * 1024 * 1024:
                                        logger.warning(f"      ⏭️  Skipping large attachment: {filename} ({size} bytes)")
                                        continue

                                    # Download attachment
                                    logger.info(f"      📥 Downloading: {filename}")
                                    attachment_bytes = await download_gmail_attachment(
                                        http_client,
                                        access_token,
                                        normalized["message_id"],
                                        attachment_id
                                    )

                                    # Universal ingestion (Unstructured.io parses it!)
                                    await ingest_document_universal(
                                        supabase=supabase,
                                        cortex_pipeline=cortex_pipeline,
                                        company_id=company_id,
                                        source="gmail",
                                        source_id=f"{normalized['message_id']}_{attachment_id}",  # Unique ID
                                        document_type="attachment",
                                        title=f"[Attachment] {filename}",
                                        file_bytes=attachment_bytes,
                                        filename=filename,
                                        file_type=mime_type,
                                        raw_data=attachment,  # Preserve attachment metadata
                                        metadata={
                                            "email_subject": normalized.get("subject"),
                                            "email_id": normalized["message_id"],
                                            "sender": normalized.get("sender_address"),
                                            "attached_to": "email"
                                        }
                                    )

                                    logger.info(f"      ✅ Attachment ingested: {filename}")

                                except Exception as e:
                                    error_msg = f"Error processing attachment {filename}: {e}"
                                    logger.error(f"      ❌ {error_msg}")
                                    errors.append(error_msg)

                    except Exception as e:
                        error_msg = f"Error processing Gmail message: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)

                # THREAD-SAFE: Lock cursor update to prevent race conditions
                with _cursor_lock:
                    # Update cursor for next page
                    if next_cursor:
                        cursor = next_cursor
                        # Save cursor after each page for incremental sync
                        await set_gmail_cursor(company_id, provider_key, cursor)
                    else:
                        has_more = False

            except Exception as e:
                error_msg = f"Error fetching Gmail page: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                has_more = False

        logger.info(f"Gmail sync completed for tenant {company_id}: {messages_synced} messages")

        return {
            "status": "success" if not errors else "partial_success",
            "company_id": company_id,
            "messages_synced": messages_synced,
            "errors": errors
        }

    except Exception as e:
        error_msg = f"Fatal error during Gmail sync: {e}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "status": "error",
            "company_id": company_id,
            "messages_synced": messages_synced,
            "errors": errors
        }
