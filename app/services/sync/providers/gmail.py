"""
Gmail message normalization helpers
Handles conversion of Nango Gmail records to internal schema
"""
import logging
import httpx
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def normalize_gmail_message(
    gmail_record: Dict[str, Any],
    company_id: str
) -> Dict[str, Any]:
    """
    Normalize a Gmail record from Nango into our schema.

    Nango GmailEmail model structure:
    {
        "id": "message_id",
        "sender": "sender@example.com",
        "recipients": ["recipient@example.com"],
        "date": "2024-01-01T00:00:00Z",
        "subject": "Subject line",
        "body": "Email body content",
        "attachments": [...],
        ...
    }

    Args:
        gmail_record: Raw Gmail record from Nango
        company_id: Tenant identifier

    Returns:
        Normalized message dictionary
    """
    # Extract sender information
    sender_raw = gmail_record.get("sender", "")
    # Gmail sender can be "Name <email@example.com>" or just "email@example.com"
    if "<" in sender_raw and ">" in sender_raw:
        # Parse "Name <email@example.com>"
        sender_name = sender_raw.split("<")[0].strip()
        sender_address = sender_raw.split("<")[1].split(">")[0].strip()
    else:
        sender_name = ""
        sender_address = sender_raw.strip()

    # Extract recipient addresses
    recipients = gmail_record.get("recipients", [])
    # Ensure recipients is always a list (Nango might send string)
    if isinstance(recipients, str):
        recipients = [recipients]
    elif not isinstance(recipients, list):
        recipients = []

    to_addresses = []
    for recipient in recipients:
        if isinstance(recipient, str):
            # Extract email from "Name <email>" format if present
            if "<" in recipient and ">" in recipient:
                email = recipient.split("<")[1].split(">")[0].strip()
            else:
                email = recipient.strip()
            to_addresses.append(email)

    # Parse date
    date_str = gmail_record.get("date")
    if date_str:
        try:
            received_datetime = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except Exception:
            received_datetime = None
    else:
        received_datetime = None

    # For Gmail, we'll use the sender's email as the user_id and user_principal_name
    # since Gmail doesn't have the same tenant/user structure as Outlook
    user_email = sender_address or "unknown@gmail.com"

    # Get full body (Nango provides full email body in 'body' field)
    full_body = gmail_record.get("body", "")

    # Extract attachments
    # Gmail API provides attachments as array with metadata
    # Example: [{"filename": "report.pdf", "mimeType": "application/pdf", "attachmentId": "abc123", "size": 123456}]
    attachments = gmail_record.get("attachments", [])
    if not isinstance(attachments, list):
        attachments = []

    return {
        "company_id": company_id,
        "user_id": user_email,  # Use email as user ID for Gmail
        "user_principal_name": user_email,
        "message_id": gmail_record.get("id"),
        "source": "gmail",
        "subject": gmail_record.get("subject", ""),
        "sender_name": sender_name,
        "sender_address": sender_address,
        "to_addresses": to_addresses,
        "received_datetime": received_datetime.isoformat() if received_datetime else None,
        "web_link": "",  # Gmail records from Nango may not include web link
        "full_body": full_body,  # Full email body content
        "change_key": "",  # Gmail doesn't use change keys
        "attachments": attachments,  # Attachment metadata
        "thread_id": gmail_record.get("threadId", "")  # Gmail thread ID for deduplication
    }


async def download_gmail_attachment(
    http_client: httpx.AsyncClient,
    access_token: str,
    message_id: str,
    attachment_id: str,
    user_id: str = "me"
) -> bytes:
    """
    Download a Gmail attachment using Gmail API.

    Args:
        http_client: HTTP client
        access_token: Gmail OAuth access token
        message_id: Gmail message ID
        attachment_id: Attachment ID from message metadata
        user_id: Gmail user ID (default: 'me' for authenticated user)

    Returns:
        Attachment bytes
    """
    url = f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}/attachments/{attachment_id}"

    response = await http_client.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    response.raise_for_status()

    # Gmail API returns attachment data base64url-encoded
    import base64
    data = response.json()
    attachment_data = data.get("data", "")

    # Decode base64url (URL-safe base64)
    # Gmail uses base64url encoding (replaces + with - and / with _)
    attachment_data = attachment_data.replace("-", "+").replace("_", "/")

    # Add padding if needed
    padding = len(attachment_data) % 4
    if padding:
        attachment_data += "=" * (4 - padding)

    return base64.b64decode(attachment_data)


def is_supported_attachment_type(mime_type: str) -> bool:
    """
    Check if attachment MIME type is supported for text extraction.

    Args:
        mime_type: MIME type string

    Returns:
        True if supported by Unstructured.io
    """
    supported = [
        # Documents
        "application/pdf",
        "application/msword",  # .doc
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
        "application/vnd.ms-powerpoint",  # .ppt
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
        "application/vnd.ms-excel",  # .xls
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx

        # Text
        "text/plain",
        "text/html",
        "text/markdown",
        "text/csv",
        "application/json",

        # Images (OCR)
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/tiff",
        "image/bmp",
    ]

    return mime_type in supported
