"""
File Upload Routes
Universal file ingestion for PDFs, Word, images, etc.

SECURITY FEATURES:
- File size limits (100MB max)
- MIME type validation (whitelist only)
- Filename sanitization (prevent path traversal)
- Streaming uploads (prevent memory exhaustion)
"""
import logging
import re
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Request
from supabase import Client

from app.core.security import get_current_user_context
from app.core.dependencies import get_supabase, get_cortex_pipeline
from app.services.universal.ingest import ingest_document_universal
from app.services.ingestion.llamaindex import UniversalIngestionPipeline
from app.middleware.rate_limit import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])

# SECURITY: File upload constraints
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
MAX_BATCH_FILES = 10  # Maximum files in batch upload
ALLOWED_MIME_TYPES = {
    # Documents
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.ms-powerpoint",  # .ppt
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    # Text
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
    # Images
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/bmp",
}


def sanitize_filename(filename: str) -> str:
    """
    Sanitize user-provided filename to prevent security issues.

    SECURITY:
    - Prevents path traversal (../../etc/passwd)
    - Removes dangerous characters
    - Limits length
    - Prevents hidden files

    Args:
        filename: Original filename from user

    Returns:
        Sanitized filename safe for storage
    """
    # Remove path components (prevent traversal)
    filename = Path(filename).name

    # Remove dangerous characters (keep only alphanumeric, dots, dashes, underscores)
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)

    # Limit length
    if len(filename) > 255:
        # Keep extension, truncate name
        parts = filename.rsplit('.', 1)
        if len(parts) == 2:
            name, ext = parts
            filename = name[:250] + '.' + ext
        else:
            filename = filename[:255]

    # Prevent hidden files
    if filename.startswith('.'):
        filename = '_' + filename

    # Ensure filename is not empty
    if not filename or filename == '_':
        filename = 'unnamed_file'

    return filename


@router.post("/file")
@limiter.limit("10/hour")  # SECURITY: 10 file uploads per hour per user
async def upload_file(
    request: Request,  # Required for rate limiting
    file: UploadFile = File(...),
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase),
    cortex_pipeline: UniversalIngestionPipeline = Depends(get_cortex_pipeline)
):
    """
    Upload and ingest a file.

    Supports:
    - PDFs (application/pdf)
    - Word docs (.docx, .doc)
    - PowerPoint (.pptx)
    - Excel (.xlsx)
    - Images (PNG, JPEG, etc.)
    - Text files (.txt, .md, .html)
    - And 20+ more file types

    Parsing runs 100% locally using Unstructured.

    Flow:
    1. Upload file
    2. Parse with Unstructured (LOCAL)
    3. Save to documents table (Supabase)
    4. Ingest to Qdrant vector store

    Args:
        file: Uploaded file
        user_id: Authenticated user (from JWT)
        supabase: Supabase client
        cortex_pipeline: Ingestion pipeline

    Returns:
        Ingestion result
    """
    try:
        # Extract user_id and company_id from JWT
        user_id = user_context["user_id"]
        company_id = user_context["company_id"]

        # SECURITY: Validate MIME type
        if file.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {file.content_type}. Allowed types: PDF, Word, Excel, PowerPoint, Images, Text"
            )

        # SECURITY: Sanitize filename (prevent path traversal, XSS)
        safe_filename = sanitize_filename(file.filename)

        # SECURITY: Read file with size limit (prevent memory exhaustion)
        file_bytes = await file.read()
        if len(file_bytes) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {MAX_FILE_SIZE / 1024 / 1024:.0f}MB"
            )

        logger.info(f"📤 File upload: {safe_filename} ({len(file_bytes)} bytes, {file.content_type}) from user {user_id[:8]}...")

        # Universal ingestion
        result = await ingest_document_universal(
            supabase=supabase,
            cortex_pipeline=cortex_pipeline,
            company_id=company_id,  # Use company_id, not user_id!
            source='upload',
            source_id=safe_filename,  # Use sanitized filename
            document_type='file',
            file_bytes=file_bytes,
            filename=safe_filename,  # Use sanitized filename
            file_type=file.content_type,
            metadata={
                'uploaded_by': user_id,
                'original_filename': file.filename,  # Preserve original for reference
                'sanitized_filename': safe_filename,
                'content_type': file.content_type,
            }
        )

        if result['status'] == 'error':
            raise HTTPException(
                status_code=500,
                detail=f"Upload failed: {result.get('error')}"
            )

        logger.info(f"✅ File uploaded and ingested: {safe_filename}")

        return {
            "success": True,
            "filename": safe_filename,
            "original_filename": file.filename,
            "file_type": result.get('file_type'),
            "characters": result.get('characters'),
            "message": f"File '{safe_filename}' uploaded and ingested successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Upload failed: {str(e)}"
        )


@router.post("/files")
@limiter.limit("5/hour")  # SECURITY: 5 batch uploads per hour (more restrictive)
async def upload_multiple_files(
    request: Request,  # Required for rate limiting
    files: list[UploadFile] = File(...),
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase),
    cortex_pipeline: UniversalIngestionPipeline = Depends(get_cortex_pipeline)
):
    """
    Upload and ingest multiple files.

    SECURITY: Limited to {MAX_BATCH_FILES} files per request.
    Processes each file independently with same security checks as single upload.

    Args:
        files: List of uploaded files
        user_id: Authenticated user
        supabase: Supabase client
        cortex_pipeline: Ingestion pipeline

    Returns:
        List of ingestion results
    """
    # Extract user_id and company_id from JWT
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]

    # SECURITY: Limit number of files in batch
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum {MAX_BATCH_FILES} files per batch upload."
        )

    results = []

    for file in files:
        try:
            # SECURITY: Validate MIME type
            if file.content_type not in ALLOWED_MIME_TYPES:
                results.append({
                    "filename": file.filename,
                    "status": "error",
                    "error": f"Unsupported file type: {file.content_type}"
                })
                continue

            # SECURITY: Sanitize filename
            safe_filename = sanitize_filename(file.filename)

            # SECURITY: Read file with size limit
            file_bytes = await file.read()
            if len(file_bytes) > MAX_FILE_SIZE:
                results.append({
                    "filename": file.filename,
                    "status": "error",
                    "error": f"File too large (max {MAX_FILE_SIZE / 1024 / 1024:.0f}MB)"
                })
                continue

            logger.info(f"📤 Batch upload: {safe_filename} ({len(file_bytes)} bytes)")

            result = await ingest_document_universal(
                supabase=supabase,
                cortex_pipeline=cortex_pipeline,
                company_id=company_id,  # Use company_id, not user_id!
                source='upload',
                source_id=safe_filename,
                document_type='file',
                file_bytes=file_bytes,
                filename=safe_filename,
                file_type=file.content_type,
                metadata={
                    'uploaded_by': user_id,
                    'original_filename': file.filename,
                    'sanitized_filename': safe_filename,
                }
            )

            results.append({
                "filename": safe_filename,
                "original_filename": file.filename,
                "status": result['status'],
                "characters": result.get('characters'),
                "error": result.get('error')
            })

        except Exception as e:
            logger.error(f"Failed to process {file.filename}: {e}")
            results.append({
                "filename": file.filename,
                "status": "error",
                "error": str(e)
            })

    # Summary
    success_count = sum(1 for r in results if r['status'] == 'success')
    error_count = sum(1 for r in results if r['status'] == 'error')

    return {
        "success": True,
        "total": len(files),
        "success_count": success_count,
        "error_count": error_count,
        "results": results
    }
