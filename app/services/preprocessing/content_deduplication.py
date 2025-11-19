"""
Deduplication Service
Content-based deduplication using SHA256 hashing to prevent near-duplicate ingestion
"""
import hashlib
import logging
import re
from typing import Optional
from supabase import Client

logger = logging.getLogger(__name__)


class DedupeService:
    """
    Simple, clean deduplication service.
    
    Uses content hashing (SHA256) to detect duplicate documents
    before RAG ingestion. Works across all sources.
    """
    
    @staticmethod
    def normalize_content(content: str) -> str:
        """
        Normalize content for consistent hashing.
        
        Removes whitespace variations, case differences, etc.
        to catch near-duplicates.
        
        Args:
            content: Raw document content
            
        Returns:
            Normalized content string
        """
        # Convert to lowercase
        normalized = content.lower()
        
        # Remove extra whitespace (multiple spaces, tabs, newlines)
        normalized = re.sub(r'\s+', ' ', normalized)
        
        # Strip leading/trailing whitespace
        normalized = normalized.strip()
        
        return normalized
    
    @staticmethod
    def compute_content_hash(content: str) -> str:
        """
        Compute SHA256 hash of normalized content.
        
        Args:
            content: Document content
            
        Returns:
            Hex string of SHA256 hash
        """
        normalized = DedupeService.normalize_content(content)
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    @staticmethod
    async def check_duplicate(
        supabase: Client,
        company_id: str,
        content_hash: str,
        source: Optional[str] = None
    ) -> Optional[dict]:
        """
        Check if a document with this content hash already exists.
        
        Args:
            supabase: Supabase client
            company_id: Tenant/user ID
            content_hash: SHA256 hash of content
            source: Optional source filter (e.g., 'gmail', 'gdrive')
            
        Returns:
            Existing document dict if found, None otherwise
        """
        try:
            query = supabase.table("documents").select("*").eq(
                "company_id", company_id
            ).eq(
                "content_hash", content_hash
            )
            
            # Optionally filter by source
            if source:
                query = query.eq("source", source)
            
            result = query.limit(1).execute()
            
            if result.data:
                logger.info(f"Duplicate content found: hash={content_hash[:16]}...")
                return result.data[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Error checking duplicate: {e}")
            # On error, assume not duplicate (safer to ingest than skip)
            return None
    
    @staticmethod
    async def mark_as_duplicate(
        supabase: Client,
        document_id: int,
        duplicate_of_id: int
    ):
        """
        Mark a document as a duplicate of another.
        
        Updates metadata to track the relationship.
        
        Args:
            supabase: Supabase client
            document_id: ID of the duplicate document
            duplicate_of_id: ID of the original document
        """
        try:
            supabase.table("documents").update({
                "metadata": {
                    "is_duplicate": True,
                    "duplicate_of_id": duplicate_of_id
                }
            }).eq("id", document_id).execute()
            
            logger.info(f"Marked document {document_id} as duplicate of {duplicate_of_id}")
            
        except Exception as e:
            logger.error(f"Error marking duplicate: {e}")


async def should_ingest_document(
    supabase: Client,
    company_id: str,
    content: str,
    source: Optional[str] = None,
    skip_dedupe: bool = False
) -> tuple[bool, Optional[str]]:
    """
    Convenience function: Check if document should be ingested.
    
    Args:
        supabase: Supabase client
        company_id: Tenant/user ID
        content: Document content
        source: Optional source filter
        skip_dedupe: If True, always return True (for testing/override)
        
    Returns:
        Tuple of (should_ingest: bool, content_hash: str)
    """
    if skip_dedupe:
        content_hash = DedupeService.compute_content_hash(content)
        return True, content_hash
    
    content_hash = DedupeService.compute_content_hash(content)
    
    duplicate = await DedupeService.check_duplicate(
        supabase,
        company_id,
        content_hash,
        source
    )
    
    if duplicate:
        logger.info(f"Skipping duplicate document: {duplicate.get('title', 'Unknown')[:50]}...")
        return False, content_hash
    
    return True, content_hash

