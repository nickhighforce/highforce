"""
Google Drive Sync Engine
Syncs entire Drive or specific folders using Nango
"""
import logging
from typing import Dict, Any, List, Optional
import httpx
from supabase import Client

from app.core.config import settings
from app.services.rag import UniversalIngestionPipeline
from app.services.sync.database import get_connection
from app.services.sync.providers.google_drive import (
    normalize_drive_file,
    is_supported_file_type,
    get_export_mime_type
)
from app.services.sync.oauth import nango_fetch_file
from app.services.preprocessing.normalizer import ingest_document_universal

logger = logging.getLogger(__name__)


async def nango_list_drive_files(
    http_client: httpx.AsyncClient,
    provider_key: str,
    connection_id: str,
    folder_ids: Optional[List[str]] = None,
    page_token: Optional[str] = None,
    modified_after: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """
    List Drive files using Nango proxy to Drive API (direct, no sync needed).

    Args:
        http_client: HTTP client
        provider_key: Nango provider key
        connection_id: Connection ID
        folder_ids: Optional list of folder IDs to filter by
        page_token: Pagination token from Drive API
        modified_after: ISO timestamp - only fetch files modified after this time
        limit: Results per page

    Returns:
        Dict with files list and nextPageToken
    """
    # Use Nango proxy to call Drive API directly
    url = "https://api.nango.dev/proxy/drive/v3/files"

    # Build query for Drive API
    query_parts = ["trashed = false"]
    
    # Add modified time filter for incremental sync
    if modified_after:
        query_parts.append(f"modifiedTime > '{modified_after}'")
    
    if folder_ids:
        folder_conditions = " or ".join([f"'{fid}' in parents" for fid in folder_ids])
        query_parts.append(f"({folder_conditions})")
    
    params = {
        "fields": "files(id,name,mimeType,webViewLink,parents,modifiedTime,createdTime,size,owners),nextPageToken",
        "pageSize": str(limit),
        "corpora": "user",  # User's Drive (not shared drives)
        "q": " and ".join(query_parts)
    }

    if page_token:
        params["pageToken"] = page_token

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
    
    data = response.json()
    
    # Transform Drive API response to match expected format
    files = data.get("files", [])
    next_page_token = data.get("nextPageToken")
    
    # Convert to our expected format
    records = []
    for file in files:
        records.append({
            "id": file.get("id"),
            "name": file.get("name"),
            "mimeType": file.get("mimeType"),
            "webViewLink": file.get("webViewLink"),
            "parents": file.get("parents", []),
            "modifiedTime": file.get("modifiedTime"),
            "createdTime": file.get("createdTime"),
            "size": file.get("size"),
            "owners": file.get("owners", []),
            "trashed": False
        })
    
    return {
        "records": records,
        "next_cursor": next_page_token
    }


async def get_drive_access_token(
    http_client: httpx.AsyncClient,
    provider_key: str,
    connection_id: str
) -> str:
    """
    Get Drive access token from Nango.

    Args:
        http_client: HTTP client
        provider_key: Nango provider key
        connection_id: Connection ID

    Returns:
        Access token
    """
    url = f"https://api.nango.dev/connection/{connection_id}"

    response = await http_client.get(
        url,
        params={"provider_config_key": provider_key},
        headers={"Authorization": f"Bearer {settings.nango_secret}"}
    )

    response.raise_for_status()
    data = response.json()

    return data["credentials"]["access_token"]


async def run_drive_sync(
    http_client: httpx.AsyncClient,
    supabase: Client,
    cortex_pipeline: Optional[UniversalIngestionPipeline],
    company_id: str,
    provider_key: str,
    folder_ids: Optional[List[str]] = None,
    download_files: bool = True
) -> Dict[str, Any]:
    """
    Sync Google Drive files for a tenant (incremental sync supported).

    Flow:
    1. Check last sync time from documents table
    2. Fetch only new/updated files from Drive (modifiedTime > last_sync)
    3. For each supported file:
       a. Download file content via /fetch-document
       b. Ingest via universal ingestion (Unstructured.io parses it)
    4. Pagination until all files synced

    Args:
        http_client: HTTP client
        supabase: Supabase client
        cortex_pipeline: Qdrant ingestion pipeline
        company_id: Tenant/user ID
        provider_key: Nango provider key
        folder_ids: Optional folder IDs to sync (None = entire Drive)
        download_files: Whether to download and parse files (vs just metadata)

    Returns:
        Sync statistics
    """
    logger.info(f"🚀 Starting Drive sync for tenant {company_id}")

    # Get last sync time for incremental sync
    last_sync_time = None
    try:
        result = supabase.table("documents").select("source_modified_at").eq(
            "company_id", company_id
        ).eq(
            "source", "googledrive"
        ).order("source_modified_at", desc=True).limit(1).execute()
        
        if result.data and result.data[0].get("source_modified_at"):
            last_sync_time = result.data[0]["source_modified_at"]
            logger.info(f"   📅 Incremental sync: fetching files modified after {last_sync_time}")
        else:
            logger.info(f"   📅 First sync: fetching all files")
    except Exception as e:
        logger.warning(f"Could not get last sync time: {e}. Doing full sync.")

    if folder_ids:
        logger.info(f"   Syncing specific folders: {folder_ids}")
    else:
        logger.info(f"   Syncing entire Drive")

    files_synced = 0
    files_skipped = 0
    errors = []

    try:
        # Get connection
        connection_id = await get_connection(company_id, provider_key)
        if not connection_id:
            error_msg = f"No Drive connection found for tenant {company_id}"
            logger.error(error_msg)
            return {
                "status": "error",
                "error": error_msg,
                "files_synced": 0
            }

        # Nango handles OAuth tokens internally - no need to get access_token

        # Paginate through all files
        cursor = None
        has_more = True

        while has_more:
            try:
                # Fetch page of files (with incremental sync support)
                result = await nango_list_drive_files(
                    http_client,
                    provider_key,
                    connection_id,
                    folder_ids=folder_ids,
                    page_token=cursor,
                    modified_after=last_sync_time,  # Incremental sync
                    limit=100
                )

                files = result.get("records", [])
                next_cursor = result.get("next_cursor")

                logger.info(f"📄 Fetched {len(files)} files (cursor: {cursor[:20] if cursor else 'none'}...)")

                # Process each file
                for raw_file in files:
                    try:
                        # Normalize metadata
                        normalized = normalize_drive_file(raw_file, company_id)

                        # Skip trashed files
                        if normalized["is_trashed"]:
                            logger.debug(f"   ⏭️  Skipping trashed file: {normalized['file_name']}")
                            files_skipped += 1
                            continue

                        # Check if file type is supported
                        if not is_supported_file_type(normalized["mime_type"]):
                            logger.debug(f"   ⏭️  Skipping unsupported type: {normalized['file_name']} ({normalized['mime_type']})")
                            files_skipped += 1
                            continue

                        # Download and ingest file
                        if download_files:
                            file_bytes = None
                            document_type = "file"  # Default
                            original_mime = normalized["mime_type"]
                            export_mime = None

                            # Check if Google Workspace file needs export
                            if normalized["mime_type"].startswith("application/vnd.google-apps"):
                                export_mime = get_export_mime_type(original_mime)
                                
                                if original_mime == "application/vnd.google-apps.document":
                                    document_type = "googledoc"
                                    normalized["mime_type"] = "text/plain"  # Exported as plain text
                                elif original_mime == "application/vnd.google-apps.spreadsheet":
                                    document_type = "googlesheet"
                                    normalized["mime_type"] = "text/csv"  # Exported as CSV
                                elif original_mime == "application/vnd.google-apps.presentation":
                                    document_type = "googleslide"
                                    normalized["mime_type"] = "text/plain"  # Exported as plain text
                                else:
                                    document_type = "file"

                            # Download/export file using Nango proxy
                            logger.info(f"   📥 {'Exporting' if export_mime else 'Downloading'}: {normalized['file_name']}")

                            file_bytes = await nango_fetch_file(
                                http_client,
                                provider_key,
                                connection_id,
                                normalized["file_id"],
                                mime_type=original_mime,
                                export_mime_type=export_mime
                            )

                            # Universal ingestion (Unstructured.io parses the file!)
                            result = await ingest_document_universal(
                                supabase=supabase,
                                cortex_pipeline=cortex_pipeline,
                                company_id=company_id,
                                source="googledrive",
                                source_id=normalized["file_id"],
                                document_type=document_type,  # googledoc, googlesheet, googleslide, or file
                                title=normalized["file_name"],
                                file_bytes=file_bytes,
                                filename=normalized["file_name"],
                                file_type=normalized["mime_type"],
                                raw_data=raw_file,  # Preserve full metadata
                                source_created_at=normalized["created_at"],
                                source_modified_at=normalized["modified_at"],
                                metadata={
                                    "owner_email": normalized["owner_email"],
                                    "owner_name": normalized["owner_name"],
                                    "web_view_link": normalized["web_view_link"],
                                    "parent_folders": normalized["parent_folders"],
                                    "original_mime_type": original_mime  # Preserve original type
                                }
                            )

                            if result["status"] == "success":
                                logger.info(f"   ✅ Ingested: {normalized['file_name']}")
                                files_synced += 1
                            else:
                                logger.error(f"   ❌ Ingestion failed: {result.get('error')}")
                                errors.append(f"{normalized['file_name']}: {result.get('error')}")
                        else:
                            # Metadata-only mode (no download)
                            files_synced += 1

                    except Exception as e:
                        error_msg = f"Error processing file: {e}"
                        logger.error(error_msg)
                        errors.append(error_msg)

                # Check pagination
                if next_cursor:
                    cursor = next_cursor
                else:
                    has_more = False

            except Exception as e:
                error_msg = f"Error fetching Drive page: {e}"
                logger.error(error_msg)
                errors.append(error_msg)
                has_more = False

        logger.info(f"✅ Drive sync complete: {files_synced} files synced, {files_skipped} skipped")

        return {
            "status": "success" if not errors else "partial_success",
            "company_id": company_id,
            "files_synced": files_synced,
            "files_skipped": files_skipped,
            "errors": errors
        }

    except Exception as e:
        error_msg = f"Fatal error during Drive sync: {e}"
        logger.error(error_msg)
        return {
            "status": "error",
            "error": error_msg,
            "files_synced": files_synced
        }
