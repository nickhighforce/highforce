"""
Microsoft Graph API helpers
Handles user listing, mailbox syncing, and message normalization
"""
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List

import httpx

from app.services.sync.database import get_user_cursor, save_user_cursor

logger = logging.getLogger(__name__)


# ============================================================================
# RETRY LOGIC
# ============================================================================

async def retry_with_backoff(
    func,
    *args,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    **kwargs
):
    """
    Retry function with exponential backoff.
    Handles 429 rate limits and 5xx server errors.
    """
    delay = initial_delay
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            last_exception = e
            if e.response.status_code == 429:
                # Respect Retry-After header
                retry_after = e.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = delay * 2
                logger.warning(f"Rate limited, retrying in {delay}s (attempt {attempt + 1}/{max_retries + 1})")
            elif 500 <= e.response.status_code < 600:
                logger.warning(f"Server error {e.response.status_code}, retrying in {delay}s (attempt {attempt + 1}/{max_retries + 1})")
            else:
                # Don't retry on other status codes
                raise

            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                raise last_exception
        except Exception as e:
            logger.error(f"Unexpected error in retry_with_backoff: {e}")
            raise

    raise last_exception


# ============================================================================
# MICROSOFT GRAPH API
# ============================================================================

async def list_all_users(http_client: httpx.AsyncClient, access_token: str) -> List[Dict[str, str]]:
    """
    List all users in the tenant using Microsoft Graph.

    Args:
        http_client: Async HTTP client instance
        access_token: Microsoft Graph access token

    Returns:
        List of user dictionaries with 'id' and 'userPrincipalName'
    """
    users = []
    url = "https://graph.microsoft.com/v1.0/users"
    headers = {"Authorization": f"Bearer {access_token}"}

    async def fetch_page(page_url: str):
        response = await http_client.get(page_url, headers=headers)
        response.raise_for_status()
        return response.json()

    try:
        while url:
            data = await retry_with_backoff(fetch_page, url)

            for user in data.get("value", []):
                users.append({
                    "id": user.get("id"),
                    "userPrincipalName": user.get("userPrincipalName")
                })

            # Handle pagination
            url = data.get("@odata.nextLink")

        logger.info(f"Retrieved {len(users)} users from Microsoft Graph")
        return users
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        raise


async def sync_user_mailbox(
    http_client: httpx.AsyncClient,
    access_token: str,
    company_id: str,
    provider_key: str,
    user_id: str,
    user_principal_name: str
) -> List[Dict[str, Any]]:
    """
    Sync a user's mailbox using Microsoft Graph delta API.

    Args:
        http_client: Async HTTP client instance
        access_token: Microsoft Graph access token
        company_id: Tenant identifier
        provider_key: Provider configuration key
        user_id: User ID
        user_principal_name: User principal name

    Returns:
        List of raw message dictionaries from Graph API
    """
    messages = []
    headers = {"Authorization": f"Bearer {access_token}"}

    # Check if we have an existing delta link
    delta_link = await get_user_cursor(company_id, provider_key, user_id)

    if delta_link:
        url = delta_link
        logger.info(f"Using existing delta link for user {user_principal_name}")
    else:
        # Delta queries don't support $select - they return a default set of properties
        url = f"https://graph.microsoft.com/v1.0/users/{user_id}/messages/delta"
        logger.info(f"Starting initial sync for user {user_principal_name}")

    async def fetch_page(page_url: str):
        response = await http_client.get(page_url, headers=headers)
        response.raise_for_status()
        return response.json()

    try:
        new_delta_link = None

        while url:
            data = await retry_with_backoff(fetch_page, url)

            # Collect messages
            messages.extend(data.get("value", []))

            # Check for next page or delta link
            if "@odata.nextLink" in data:
                url = data["@odata.nextLink"]
            elif "@odata.deltaLink" in data:
                new_delta_link = data["@odata.deltaLink"]
                url = None  # Exit loop
            else:
                url = None

        # Save the new delta link for next sync
        if new_delta_link:
            await save_user_cursor(
                company_id,
                provider_key,
                user_id,
                user_principal_name,
                new_delta_link
            )

        logger.info(f"Synced {len(messages)} messages for user {user_principal_name}")
        return messages
    except httpx.HTTPStatusError as e:
        # Log the full error response from Microsoft Graph
        error_details = e.response.text if hasattr(e.response, 'text') else str(e)
        logger.error(f"❌ Microsoft Graph error for {user_principal_name}: {e.response.status_code}")
        logger.error(f"   URL: {e.request.url}")
        logger.error(f"   Response: {error_details[:500]}")  # First 500 chars
        raise
    except Exception as e:
        logger.error(f"Error syncing mailbox for user {user_principal_name}: {e}")
        raise


def normalize_message(
    raw_message: Dict[str, Any],
    company_id: str,
    user_id: str,
    user_principal_name: str
) -> Dict[str, Any]:
    """
    Normalize a raw Microsoft Graph message into our schema.

    Args:
        raw_message: Raw message dictionary from Graph API
        company_id: Tenant identifier
        user_id: User ID
        user_principal_name: User principal name

    Returns:
        Normalized message dictionary
    """
    # Extract sender information
    sender = raw_message.get("from", {}).get("emailAddress", {})
    sender_name = sender.get("name", "")
    sender_address = sender.get("address", "")

    # Extract recipient addresses
    to_recipients = raw_message.get("toRecipients", [])
    to_addresses = [r.get("emailAddress", {}).get("address") for r in to_recipients if r.get("emailAddress")]

    # Parse received datetime
    received_dt = raw_message.get("receivedDateTime")
    if received_dt:
        # Graph returns ISO 8601 format
        try:
            received_datetime = datetime.fromisoformat(received_dt.replace("Z", "+00:00"))
        except Exception:
            received_datetime = None
    else:
        received_datetime = None

    # Extract full body content
    body_obj = raw_message.get("body", {})
    if isinstance(body_obj, dict):
        full_body = body_obj.get("content", "")
    else:
        full_body = ""

    return {
        "company_id": company_id,
        "user_id": user_id,
        "user_principal_name": user_principal_name,
        "message_id": raw_message.get("id"),
        "source": "outlook",
        "subject": raw_message.get("subject", ""),
        "sender_name": sender_name,
        "sender_address": sender_address,
        "to_addresses": to_addresses,
        "received_datetime": received_datetime.isoformat() if received_datetime else None,
        "web_link": raw_message.get("webLink", ""),
        "full_body": full_body,  # Full email body content (HTML or text)
        "change_key": raw_message.get("changeKey", "")
    }
