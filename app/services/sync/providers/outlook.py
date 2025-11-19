"""
Outlook connector for Nango unified email API
Handles normalization of Nango Outlook records and attachment downloading
"""
import httpx
import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


async def download_outlook_attachment(
    http_client: httpx.AsyncClient,
    access_token: str,
    user_id: str,
    message_id: str,
    attachment_id: str
) -> bytes:
    """
    Download an Outlook attachment using Microsoft Graph API.

    Args:
        http_client: HTTP client
        access_token: Microsoft Graph access token
        user_id: User ID who owns the message
        message_id: Outlook message ID
        attachment_id: Attachment ID from message metadata

    Returns:
        Attachment bytes
    """
    # Microsoft Graph endpoint for attachment content
    url = f"https://graph.microsoft.com/v1.0/users/{user_id}/mailFolders/inbox/messages/{message_id}/attachments/{attachment_id}/$value"

    response = await http_client.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    response.raise_for_status()

    # Microsoft Graph returns raw attachment bytes directly
    return response.content


def normalize_outlook_message(nango_record: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """
    Normalize a Nango Outlook email record into our unified schema.
    
    Nango provides a unified email format across providers, so this is simple!

    Args:
        nango_record: Email record from Nango's unified API
        company_id: Tenant identifier

    Returns:
        Normalized email dictionary
    """
    # Extract basic fields from Nango unified format
    email_id = nango_record.get("id", "")
    subject = nango_record.get("subject", "")
    sender = nango_record.get("sender", "")
    recipients = nango_record.get("recipients", "")
    date_str = nango_record.get("date", "")
    body = nango_record.get("body", "")
    thread_id = nango_record.get("threadId", "")
    attachments = nango_record.get("attachments", [])

    # Parse date
    received_datetime = None
    if date_str:
        try:
            received_datetime = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            pass

    # Build normalized message
    normalized = {
        "company_id": company_id,
        "message_id": email_id,
        "source": "outlook",
        "subject": subject,
        "sender_name": sender.split("<")[0].strip() if "<" in sender else sender,
        "sender_address": sender.split("<")[1].strip(">") if "<" in sender else sender,
        "to_addresses": [r.strip() for r in recipients.split(",") if r.strip()] if recipients else [],
        "received_datetime": received_datetime.isoformat() if received_datetime else None,
        "web_link": "",  # Nango doesn't provide this in unified format
        "full_body": body,
        "thread_id": thread_id,
        "attachments": attachments
    }

    return normalized

