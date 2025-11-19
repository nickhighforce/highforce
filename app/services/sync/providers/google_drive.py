"""
Google Drive Connector
Handles Drive files with universal ingestion
"""
import logging
import httpx
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def normalize_drive_file(raw_file: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """
    Normalize Google Drive file metadata from Nango.

    Nango Google Drive file structure:
    {
        "id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
        "name": "Q4 Financial Report.pdf",
        "mimeType": "application/pdf",
        "createdTime": "2024-01-15T10:30:00.000Z",
        "modifiedTime": "2024-01-16T14:20:00.000Z",
        "size": "245678",
        "webViewLink": "https://drive.google.com/file/d/...",
        "webContentLink": "https://drive.google.com/uc?id=...&export=download",
        "owners": [{"emailAddress": "user@example.com", "displayName": "John Doe"}],
        "parents": ["0BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE"],
        "trashed": false
    }

    Args:
        raw_file: Raw file metadata from Nango
        company_id: Tenant/user ID

    Returns:
        Normalized file dict for universal ingestion
    """
    # Parse timestamps
    created_at = None
    modified_at = None

    if raw_file.get("createdTime"):
        try:
            created_at = datetime.fromisoformat(raw_file["createdTime"].replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"Failed to parse createdTime: {e}")

    if raw_file.get("modifiedTime"):
        try:
            modified_at = datetime.fromisoformat(raw_file["modifiedTime"].replace("Z", "+00:00"))
        except Exception as e:
            logger.warning(f"Failed to parse modifiedTime: {e}")

    # Extract owner info
    owner_email = ""
    owner_name = ""
    if raw_file.get("owners") and len(raw_file["owners"]) > 0:
        owner = raw_file["owners"][0]
        owner_email = owner.get("emailAddress", "")
        owner_name = owner.get("displayName", "")

    return {
        "file_id": raw_file.get("id"),
        "file_name": raw_file.get("name"),
        "mime_type": raw_file.get("mimeType"),
        "size": int(raw_file.get("size", 0)) if raw_file.get("size") else None,
        "web_view_link": raw_file.get("webViewLink"),
        "download_link": raw_file.get("webContentLink"),
        "created_at": created_at,
        "modified_at": modified_at,
        "owner_email": owner_email,
        "owner_name": owner_name,
        "parent_folders": raw_file.get("parents", []),
        "is_trashed": raw_file.get("trashed", False),
        "company_id": company_id
    }


async def download_drive_file(
    http_client: httpx.AsyncClient,
    access_token: str,
    file_id: str
) -> bytes:
    """
    Download a file from Google Drive.

    Args:
        http_client: HTTP client
        access_token: Google OAuth access token
        file_id: Drive file ID

    Returns:
        File bytes
    """
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

    response = await http_client.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    response.raise_for_status()
    return response.content


def is_supported_file_type(mime_type: str) -> bool:
    """
    Check if file type is supported for text extraction.

    Supported types:
    - Documents: PDF, Word, PowerPoint, Excel, Text
    - Images: PNG, JPEG, TIFF (with OCR)
    - Others: HTML, CSV, JSON, Markdown

    Google Docs native formats need export (handled separately).

    Args:
        mime_type: MIME type string

    Returns:
        True if supported
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
        "image/tiff",

        # Google Workspace formats (need export)
        "application/vnd.google-apps.document",  # Google Docs
        "application/vnd.google-apps.spreadsheet",  # Google Sheets
        "application/vnd.google-apps.presentation",  # Google Slides
    ]

    return mime_type in supported


def get_export_mime_type(google_mime_type: str) -> Optional[str]:
    """
    Get export MIME type for Google Workspace files.

    Args:
        google_mime_type: Google Workspace MIME type

    Returns:
        Export MIME type or None
    """
    export_map = {
        "application/vnd.google-apps.document": "text/plain",  # Docs → Plain text (no images!)
        "application/vnd.google-apps.spreadsheet": "text/csv",  # Sheets → CSV (lightweight)
        "application/vnd.google-apps.presentation": "text/plain",  # Slides → Plain text (no images!)
    }

    return export_map.get(google_mime_type)


async def export_google_workspace_file(
    http_client: httpx.AsyncClient,
    access_token: str,
    file_id: str,
    mime_type: str
) -> bytes:
    """
    Export Google Workspace file (Docs, Sheets, Slides) to standard format.

    Args:
        http_client: HTTP client
        access_token: Google OAuth access token
        file_id: Drive file ID
        mime_type: Export MIME type

    Returns:
        Exported file bytes
    """
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"

    response = await http_client.get(
        url,
        params={"mimeType": mime_type},
        headers={"Authorization": f"Bearer {access_token}"}
    )

    response.raise_for_status()
    return response.content
