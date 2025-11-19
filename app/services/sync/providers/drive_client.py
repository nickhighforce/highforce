"""
Nango Google Drive Client
Uses Nango proxy to download/export files from Drive
"""
import logging
import httpx
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


async def nango_fetch_file(
    http_client: httpx.AsyncClient,
    provider_key: str,
    connection_id: str,
    file_id: str,
    mime_type: Optional[str] = None,
    export_mime_type: Optional[str] = None
) -> bytes:
    """
    Download or export a file from Google Drive via Nango proxy.

    For Google Workspace files (Docs, Sheets, Slides), uses export endpoint.
    For regular files, uses download endpoint.

    Args:
        http_client: HTTP client
        provider_key: Nango provider key
        connection_id: Nango connection ID
        file_id: Google Drive file ID
        mime_type: Original file MIME type
        export_mime_type: MIME type to export to (for Google Workspace files)

    Returns:
        File bytes
    """
    # Use export endpoint for Google Workspace files
    if export_mime_type:
        url = f"https://api.nango.dev/proxy/drive/v3/files/{file_id}/export"
        params = {"mimeType": export_mime_type}
        logger.debug(f"Exporting Google Workspace file {file_id} as {export_mime_type}")
    else:
        # Regular file download
        url = f"https://api.nango.dev/proxy/drive/v3/files/{file_id}"
        params = {"alt": "media"}
        logger.debug(f"Downloading file {file_id}")

    response = await http_client.get(
        url,
        params=params,
        headers={
            "Authorization": f"Bearer {settings.nango_secret}",
            "Connection-Id": connection_id,
            "Provider-Config-Key": provider_key
        }
    )

    response.raise_for_status()
    return response.content
