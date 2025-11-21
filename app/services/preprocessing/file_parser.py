"""
Universal File Parser using Unstructured + LlamaIndex + GPT-4o Vision
Extracts plain text AND rich context from ANY file type (PDF, Word, Excel, Images, etc.)

Strategy:
1. PDFs: Fast mode first, then GPT-4o Vision for scanned PDFs (< 100 chars)
2. Images: GPT-4o Vision (rich context extraction, not just OCR)
3. Office files: Unstructured parsing (lightweight)
4. Lazy loading: Heavy ML models only loaded when needed
5. Vision: GPT-4o with detail=high - extracts text + context + entities
"""
import logging
import tempfile
import os
import base64
from typing import Tuple, Dict, Optional
from pathlib import Path

from llama_index.core import SimpleDirectoryReader, Document
from llama_index.readers.file import UnstructuredReader
import magic

logger = logging.getLogger(__name__)


def extract_with_vision(file_path: str, file_type: str, check_business_relevance: bool = False) -> Tuple[str, Dict]:
    """
    Extract text AND rich context from images/documents using GPT-4o Vision.

    Unlike traditional OCR, this extracts:
    - All text content (OCR)
    - Context and meaning (what the document is about)
    - Key entities (people, companies, materials, amounts, dates)
    - Document structure (invoices, receipts, forms, diagrams)

    Args:
        file_path: Path to the image/document file
        file_type: MIME type of the file
        check_business_relevance: If True, classify if image is business-relevant or just decorative (logos, signatures, etc.)

    Returns:
        Tuple of (extracted_text_with_context, metadata) - Returns ("", {"skip_attachment": True, ...}) if not business-relevant
    """
    from openai import OpenAI

    try:
        # Read and encode image as base64
        with open(file_path, 'rb') as image_file:
            image_bytes = image_file.read()

        base64_image = base64.b64encode(image_bytes).decode('utf-8')

        # Determine image format for data URL
        if file_type == 'image/png':
            data_url = f"data:image/png;base64,{base64_image}"
        elif file_type in ['image/jpeg', 'image/jpg']:
            data_url = f"data:image/jpeg;base64,{base64_image}"
        else:
            data_url = f"data:image/png;base64,{base64_image}"  # Default to PNG

        # Initialize OpenAI client
        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

        # Load prompts from Supabase (NO hardcoded fallback)
        from app.services.company_context import get_prompt_template

        if check_business_relevance:
            logger.info("🔄 Loading vision_ocr_business_check prompt from Supabase...")
            prompt = get_prompt_template("vision_ocr_business_check")
            if not prompt:
                error_msg = "❌ FATAL: vision_ocr_business_check prompt not found in Supabase! Run seed script: migrations/master/004_seed_unit_industries_prompts.sql"
                logger.error(error_msg)
                raise ValueError(error_msg)
            logger.info("✅ Loaded vision_ocr_business_check prompt from Supabase (version loaded dynamically)")
        else:
            logger.info("🔄 Loading vision_ocr_extract prompt from Supabase...")
            prompt = get_prompt_template("vision_ocr_extract")
            if not prompt:
                error_msg = "❌ FATAL: vision_ocr_extract prompt not found in Supabase! Run seed script: migrations/master/004_seed_unit_industries_prompts.sql"
                logger.error(error_msg)
                raise ValueError(error_msg)
            logger.info("✅ Loaded vision_ocr_extract prompt from Supabase (version loaded dynamically)")

        # Call GPT-4o Vision with detail=high for best OCR quality
        response = client.chat.completions.create(
            model="gpt-4o",  # GPT-4o has vision capabilities
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": data_url,
                                "detail": "high"  # High detail for better OCR
                            }
                        }
                    ]
                }
            ],
            max_tokens=4096,  # Allow long responses for detailed documents
            temperature=0  # Deterministic for OCR
        )

        # Extract the rich text response
        text = response.choices[0].message.content

        # Check if this was classified as non-business (if relevance check was requested)
        if check_business_relevance and text.strip().startswith("CLASSIFICATION: SKIP"):
            # Extract skip reason
            lines = text.split('\n')
            reason = lines[0].replace("CLASSIFICATION: SKIP", "").strip()
            if not reason and len(lines) > 1:
                reason = lines[1].strip()

            logger.info(f"   ⏭️  SKIPPING non-business attachment: {Path(file_path).name} - {reason}")

            metadata = {
                "parser": "gpt4o_vision",
                "file_type": file_type,
                "file_name": Path(file_path).name,
                "file_size": os.path.getsize(file_path),
                "skip_attachment": True,
                "skip_reason": reason or "Non-business content (logo, signature, or decorative image)",
                "model": "gpt-4o",
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens
            }

            return "", metadata  # Return empty text to signal skip

        # Remove classification line if present (for BUSINESS content)
        if check_business_relevance and text.strip().startswith("CLASSIFICATION: BUSINESS"):
            lines = text.split('\n')
            text = '\n'.join(lines[1:]).strip()  # Remove first line

        metadata = {
            "parser": "gpt4o_vision",
            "file_type": file_type,
            "file_name": Path(file_path).name,
            "file_size": os.path.getsize(file_path),
            "characters": len(text),
            "ocr_enabled": True,
            "ocr_method": "gpt4o_vision_context_aware",
            "model": "gpt-4o",
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens
        }

        logger.info(f"   ✅ GPT-4o Vision extracted {len(text)} characters with context")
        logger.info(f"   📊 Token usage: {response.usage.total_tokens} tokens")
        return text, metadata

    except Exception as e:
        logger.error(f"   ❌ GPT-4o Vision failed: {e}")

        # Fallback: Save file without OCR
        text = ""
        metadata = {
            "parser": "vision_failed",
            "file_type": file_type,
            "file_name": Path(file_path).name,
            "file_size": os.path.getsize(file_path),
            "characters": 0,
            "ocr_enabled": False,
            "ocr_error": str(e),
            "note": "GPT-4o Vision failed - original file stored"
        }

        return text, metadata


def detect_file_type(file_path: str) -> str:
    """
    Detect MIME type of a file.

    Args:
        file_path: Path to file

    Returns:
        MIME type string (e.g., 'application/pdf')
    """
    # Fallback: guess from extension
    ext_to_mime = {
        '.pdf': 'application/pdf',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.doc': 'application/msword',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.txt': 'text/plain',
        '.html': 'text/html',
        '.md': 'text/markdown',
    }

    # Try magic
    try:
        mime = magic.Magic(mime=True)
        return mime.from_file(file_path)
    except Exception as e:
        logger.warning(f"Failed to detect MIME type with magic: {e}, using extension fallback")

    # Use extension fallback
    ext = Path(file_path).suffix.lower()
    return ext_to_mime.get(ext, 'application/octet-stream')


def extract_text_from_file(
    file_path: str,
    file_type: Optional[str] = None,
    check_business_relevance: bool = False
) -> Tuple[str, Dict]:
    """
    Extract text from any file type using hybrid strategy with OCR.

    Strategy:
    1. PDFs → Try fast mode first (text only), if < 100 chars → OCR (scanned PDF)
    2. Images → GPT-4o Vision OCR for text extraction (with optional business relevance check)
    3. Other files → standard Unstructured parsing

    Args:
        file_path: Path to file
        file_type: Optional MIME type (auto-detected if not provided)
        check_business_relevance: If True, skip non-business images (logos, signatures)

    Returns:
        (extracted_text, metadata_dict) - Returns ("", {"skip_attachment": True}) if not business-relevant

    Raises:
        ValueError: If file parsing fails
    """
    try:
        # Detect file type if not provided
        if not file_type:
            file_type = detect_file_type(file_path)

        logger.info(f"📄 Parsing file: {Path(file_path).name} ({file_type})")

        # Special handling for PDFs (hybrid approach)
        if file_type == 'application/pdf':
            # Step 1: Try fast mode (text extraction only, no OCR)
            try:
                from unstructured.partition.pdf import partition_pdf
                elements = partition_pdf(
                    filename=file_path,
                    strategy="fast",
                    extract_images_in_pdf=False,
                    infer_table_structure=False
                )
                text = "\n\n".join([str(el) for el in elements])
                
                # Step 2: If we got barely any text, it's probably scanned - use GPT-4o Vision OCR!
                if len(text.strip()) < 100:
                    logger.warning(f"   ⚠️  Only {len(text)} chars extracted - PDF might be scanned, trying GPT-4o Vision OCR...")
                    try:
                        # Convert PDF to images and OCR each page
                        from pdf2image import convert_from_path

                        # Convert PDF pages to images
                        images = convert_from_path(file_path, dpi=200)
                        logger.info(f"   📄 Converted PDF to {len(images)} images for OCR")

                        # OCR each page with GPT-4o Vision
                        page_texts = []
                        for i, image in enumerate(images):
                            # Save image to temp file
                            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                                image.save(tmp.name, 'PNG')
                                tmp_path = tmp.name

                            # OCR the page image with GPT-4o Vision
                            try:
                                page_text, page_meta = extract_with_vision(tmp_path, 'image/png', check_business_relevance=check_business_relevance)
                                # Check if page was skipped
                                if page_meta.get('skip_attachment'):
                                    logger.info(f"   ⏭️  Page {i+1}: Skipped (non-business content)")
                                    continue
                                page_texts.append(page_text)
                                logger.info(f"   ✅ Page {i+1}: {len(page_text)} chars extracted with context")
                            finally:
                                os.unlink(tmp_path)

                        text = "\n\n".join(page_texts)
                        logger.info(f"   ✅ GPT-4o Vision OCR extracted {len(text)} chars from scanned PDF with rich context")
                    except Exception as ocr_err:
                        logger.warning(f"   ⚠️  PDF OCR failed: {ocr_err}, using fast extraction result")
                
                metadata = {
                    "parser": "unstructured_pdf",
                    "file_type": file_type,
                    "file_name": Path(file_path).name,
                    "file_size": os.path.getsize(file_path),
                    "characters": len(text),
                    "ocr_enabled": len(text.strip()) > 100  # OCR was used if we got more text
                }
                
            except Exception as pdf_error:
                logger.warning(f"PDF-specific parsing failed: {pdf_error}, falling back to generic parser")
                # Fall back to generic parser
                text, metadata = extract_with_generic_parser(file_path, file_type)
        
        # Special handling for images (OCR with GPT-4o Vision - context-aware)
        elif file_type in ['image/png', 'image/jpeg', 'image/jpg', 'image/tiff', 'image/bmp']:
            logger.info(f"   🔍 Running context-aware OCR on image (GPT-4o Vision)...")
            text, metadata = extract_with_vision(file_path, file_type, check_business_relevance=check_business_relevance)
            # Note: extract_with_vision already handles errors gracefully and returns skip signal if needed

        # Simple plain text files - just read them directly!
        elif file_type in ['text/plain', 'text/markdown', 'text/csv', 'text/html']:
            logger.info(f"   📝 Reading plain text file directly...")
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()

            metadata = {
                "parser": "plain_text_reader",
                "file_type": file_type,
                "file_name": Path(file_path).name,
                "file_size": os.path.getsize(file_path),
                "characters": len(text),
            }
            logger.info(f"   ✅ Read {len(text)} characters from plain text file")

        else:
            # Non-PDF, non-image files: use standard Unstructured
            text, metadata = extract_with_generic_parser(file_path, file_type)

        logger.info(f"✅ Extracted {len(text)} chars from {Path(file_path).name}")
        return text, metadata

    except Exception as e:
        # FALLBACK: Try GPT-4o Vision OCR as last resort for ANY file
        logger.warning(f"⚠️  Standard parsing failed: {e}")
        logger.info(f"   🔄 Attempting GPT-4o Vision OCR fallback...")

        try:
            text, metadata = extract_with_vision(file_path, file_type, check_business_relevance=check_business_relevance)
            logger.info(f"✅ Vision OCR fallback succeeded: {len(text)} chars extracted with context")
            metadata['fallback_method'] = 'gpt4o_vision_ocr'
            metadata['original_error'] = str(e)
            return text, metadata
        except Exception as vision_error:
            error_msg = f"Failed to parse file {Path(file_path).name} (even with Vision OCR fallback): {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)


def extract_with_generic_parser(file_path: str, file_type: str) -> Tuple[str, Dict]:
    """
    Extract text using generic Unstructured parser (for non-PDFs).
    """
    reader = SimpleDirectoryReader(
        input_files=[file_path],
        file_extractor={
            ".docx": UnstructuredReader(),
            ".doc": UnstructuredReader(),
            ".pptx": UnstructuredReader(),
            ".ppt": UnstructuredReader(),
            ".xlsx": UnstructuredReader(),
            ".xls": UnstructuredReader(),
            ".txt": UnstructuredReader(),
            ".md": UnstructuredReader(),
            ".html": UnstructuredReader(),
            ".htm": UnstructuredReader(),
            ".csv": UnstructuredReader(),
            ".json": UnstructuredReader(),
            ".xml": UnstructuredReader(),
            ".eml": UnstructuredReader(),
            ".msg": UnstructuredReader(),
            ".rtf": UnstructuredReader(),
            ".odt": UnstructuredReader(),
        }
    )

    documents = reader.load_data()
    
    if not documents:
        raise ValueError("No content extracted from file")

    text = "\n\n".join([doc.text for doc in documents])
    
    metadata = {
        "parser": "unstructured",
        "file_type": file_type,
        "file_name": Path(file_path).name,
        "file_size": os.path.getsize(file_path),
        "num_documents": len(documents),
        "characters": len(text),
    }
    
    return text, metadata


def extract_text_from_bytes(
    file_bytes: bytes,
    filename: str,
    file_type: Optional[str] = None,
    check_business_relevance: bool = False
) -> Tuple[str, Dict]:
    """
    Extract text from file bytes (for uploads or API responses).

    Args:
        file_bytes: File content as bytes
        filename: Original filename (used for extension detection)
        file_type: Optional MIME type
        check_business_relevance: If True, skip non-business images (logos, signatures)

    Returns:
        (extracted_text, metadata_dict) - Returns ("", {"skip_attachment": True}) if not business-relevant

    Raises:
        ValueError: If file parsing fails
    """
    # Save bytes to temporary file
    ext = Path(filename).suffix or '.bin'
    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # Extract text from temp file
        text, metadata = extract_text_from_file(tmp_path, file_type, check_business_relevance=check_business_relevance)

        # Add original filename to metadata
        metadata['original_filename'] = filename
        metadata['file_size'] = len(file_bytes)

        return text, metadata

    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"Failed to delete temp file: {e}")


def is_parseable_file(file_type: str) -> bool:
    """
    Check if a file type is parseable by Unstructured.

    Args:
        file_type: MIME type string

    Returns:
        True if file can be parsed, False otherwise
    """
    parseable_types = [
        # Documents
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/rtf",
        "application/vnd.oasis.opendocument.text",

        # Text
        "text/plain",
        "text/html",
        "text/markdown",
        "text/csv",
        "text/xml",
        "application/json",

        # Email
        "message/rfc822",
        "application/vnd.ms-outlook",

        # Images (with OCR)
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
    ]

    return file_type in parseable_types


def get_extension_from_mime(mime_type: str) -> str:
    """
    Get file extension from MIME type.

    Args:
        mime_type: MIME type string

    Returns:
        File extension (e.g., '.pdf')
    """
    mime_to_ext = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.ms-powerpoint": ".ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.ms-excel": ".xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/plain": ".txt",
        "text/html": ".html",
        "text/markdown": ".md",
        "text/csv": ".csv",
        "application/json": ".json",
        "text/xml": ".xml",
        "message/rfc822": ".eml",
        "application/vnd.ms-outlook": ".msg",
        "application/rtf": ".rtf",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/tiff": ".tiff",
    }
    return mime_to_ext.get(mime_type, ".bin")
