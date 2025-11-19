"""
Nango API client
Handles token retrieval and Gmail unified API calls
"""
import json
import logging
from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# NANGO TOKEN RETRIEVAL
# ============================================================================

async def get_graph_token_via_nango(
    http_client: httpx.AsyncClient,
    provider_key: str,
    connection_id: str
) -> str:
    """
    Get Microsoft Graph access token via Nango.

    Args:
        http_client: Async HTTP client instance
        provider_key: Nango provider configuration key
        connection_id: Nango connection ID

    Returns:
        Access token string

    Raises:
        HTTPException: If token retrieval fails
    """
    # When using Connect SDK with end_user model, use /connections (plural) endpoint with query param
    url = f"https://api.nango.dev/connections/{connection_id}?provider_config_key={provider_key}"
    headers = {"Authorization": f"Bearer {settings.nango_secret}"}
    
    # Debug: log secret prefix/suffix to verify it's correct
    secret_preview = f"{settings.nango_secret[:4]}...{settings.nango_secret[-4:]}" if settings.nango_secret else "MISSING"
    logger.info(f"Nango API call: {url} with secret {secret_preview}")

    try:
        response = await http_client.get(url, headers=headers)
        response.raise_for_status()
        
        # Debug: log response content
        response_text = response.text
        logger.info(f"Nango response status: {response.status_code}, body length: {len(response_text)}, first 200 chars: {response_text[:200]}")
        
        data = response.json()
        return data["credentials"]["access_token"]
    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to get Nango token: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=500, detail="Failed to retrieve access token from Nango")
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error from Nango: {e}. Response text: {response_text[:500]}")
        raise HTTPException(status_code=500, detail=f"Invalid JSON from Nango: {str(e)}")
    except Exception as e:
        logger.error(f"Error getting Nango token: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# NANGO UNIFIED EMAIL API (Gmail, Outlook, etc.)
# ============================================================================

async def nango_list_email_records(
    http_client: httpx.AsyncClient,
    provider_key: str,
    connection_id: str,
    cursor: Optional[str] = None,
    limit: int = 100,
    modified_after: Optional[str] = None
) -> Dict[str, Any]:
    """
    List email records from Nango unified API (Gmail, Outlook, etc.).
    
    Nango syncs emails on its servers and provides a unified API to fetch them.
    This function fetches pre-synced emails - no direct provider API calls needed!

    Args:
        http_client: Async HTTP client instance
        provider_key: Nango provider configuration key (e.g., 'gmail', 'outlook')
        connection_id: Nango connection ID
        cursor: Optional cursor for pagination
        limit: Number of records per page
        modified_after: Optional ISO datetime to filter records

    Returns:
        Dictionary with 'records' list and optional 'next_cursor'

    Raises:
        HTTPException: If request fails
    """
    url = "https://api.nango.dev/v1/emails"
    params = {
        "limit": limit
    }

    if cursor:
        params["cursor"] = cursor
    if modified_after:
        params["modified_after"] = modified_after

    headers = {
        "Authorization": f"Bearer {settings.nango_secret}",
        "Connection-Id": connection_id,
        "Provider-Config-Key": provider_key
    }

    try:
        response = await http_client.get(url, headers=headers, params=params)
        response.raise_for_status()

        # Log FULL raw response for debugging
        response_text = response.text
        logger.info(f"=" * 80)
        logger.info(f"NANGO RAW RESPONSE - FULL PAYLOAD")
        logger.info(f"=" * 80)
        logger.info(f"URL: {url}")
        logger.info(f"Params: {params}")
        logger.info(f"Response Length: {len(response_text)} bytes")
        logger.info(f"Full Response:\n{response_text}")
        logger.info(f"=" * 80)

        # Handle empty response
        if not response_text or response_text.strip() == "":
            logger.warning("Nango returned empty response - sync may not have run yet")
            return {"records": [], "next_cursor": None}

        try:
            data = response.json()

            # Log individual email record structure (first 3 records for inspection)
            records = data.get("records", [])
            if records:
                logger.info(f"FIRST 3 EMAIL RECORDS FROM NANGO:")
                for i, record in enumerate(records[:3], 1):
                    logger.info(f"--- Email Record #{i} ---")
                    logger.info(json.dumps(record, indent=2))
                    logger.info(f"--- End Record #{i} ---")

            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Nango response as JSON: {e}")
            logger.error(f"Response text: {response_text}")
            return {"records": [], "next_cursor": None}

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to fetch email records from Nango: {e.response.status_code} - {e.response.text}")
        raise HTTPException(status_code=500, detail="Failed to fetch email records from Nango")
    except Exception as e:
        logger.error(f"Error fetching email records: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Backward compatibility alias
nango_list_gmail_records = nango_list_email_records


# ============================================================================
# NANGO ACTIONS (e.g., fetch-attachment)
# ============================================================================

async def nango_fetch_attachment(
    http_client: httpx.AsyncClient,
    provider_key: str,
    connection_id: str,
    thread_id: str,
    attachment_id: str,
    user_id: str = "me"
) -> bytes:
    """
    Fetch attachment content via Nango's PROXY (not action!).
    
    Nango proxy routes requests to Microsoft Graph with automatic auth handling.
    No 2MB output limit like actions have - perfect for large PDFs/DOCX!

    Args:
        http_client: Async HTTP client instance
        provider_key: Nango provider configuration key (e.g., 'outlook')
        connection_id: Nango connection ID
        thread_id: Email message ID
        attachment_id: Attachment ID
        user_id: Microsoft Graph user ID (default "me" for authenticated user)

    Returns:
        Raw bytes of attachment content

    Raises:
        HTTPException: If request fails
    """
    # Use Nango PROXY to call Microsoft Graph /$value endpoint
    # For multi-user mailboxes, use /users/{userId}/ (not /me/!)
    url = f"https://api.nango.dev/proxy/v1.0/users/{user_id}/messages/{thread_id}/attachments/{attachment_id}/$value"
    
    headers = {
        "Authorization": f"Bearer {settings.nango_secret}",
        "Connection-Id": connection_id,
        "Provider-Config-Key": provider_key
    }

    logger.info(f"🔽 Nango proxy: messageId={thread_id[:30]}..., attachmentId={attachment_id[:30]}...")

    try:
        # Simple GET request - Nango proxy handles everything!
        response = await http_client.get(url, headers=headers, timeout=120.0)
        response.raise_for_status()
        
        # Response body IS the attachment content (raw binary)
        attachment_bytes = response.content
        
        logger.info(f"   ✅ Fetched via proxy: {len(attachment_bytes)} bytes")
        return attachment_bytes

    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to fetch attachment via Nango proxy: {e.response.status_code} - {e.response.text[:500]}")
        raise HTTPException(status_code=500, detail="Failed to fetch attachment from Nango proxy")
    except Exception as e:
        logger.error(f"Error fetching attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))
