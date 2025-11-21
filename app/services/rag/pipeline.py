"""
Universal Ingestion Pipeline

Architecture:
1. Supabase document row → Document with metadata
2. Text chunking (SentenceSplitter) → Multiple chunks per document
3. Embedding (OpenAI) → Vectors
4. Storage: Qdrant (chunks with embeddings + metadata)

Handles ALL document types:
- Emails (Gmail, Outlook)
- Documents (PDFs, Word, Google Docs)
- Spreadsheets (Excel, Google Sheets)
- Structured data (QuickBooks, HubSpot, etc.)
"""

import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, date

from llama_index.core import Document
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, AsyncQdrantClient

from .config import (
    QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION_NAME,
    OPENAI_API_KEY, EMBEDDING_MODEL, CHUNK_SIZE, CHUNK_OVERLAP,
    SHOW_PROGRESS, NUM_WORKERS, ENABLE_CACHE, REDIS_HOST, REDIS_PORT, CACHE_COLLECTION
)

logger = logging.getLogger(__name__)


class UniversalIngestionPipeline:
    """
    Universal ingestion pipeline for ANY document type.

    Vector-only architecture:
    - Qdrant: Text chunks + embeddings (semantic search)

    Handles: Emails, PDFs, Sheets, Structured data, etc.
    """

    def __init__(self):
        logger.info("🚀 Initializing Universal Ingestion Pipeline (Expert Pattern)")

        # Qdrant vector store (with async support)
        qdrant_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        qdrant_aclient = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

        self.vector_store = QdrantVectorStore(
            client=qdrant_client,
            aclient=qdrant_aclient,
            collection_name=QDRANT_COLLECTION_NAME,
            text_key="_node_content"  # Map Qdrant's "_node_content" field to LlamaIndex text field
        )
        logger.info(f"✅ Qdrant Vector Store: {QDRANT_COLLECTION_NAME}")

        # Embedding model
        self.embed_model = OpenAIEmbedding(
            model_name=EMBEDDING_MODEL,
            api_key=OPENAI_API_KEY
        )

        # Neo4j entity extraction removed - no longer extracting entities

        # Production caching setup (optional but recommended)
        cache = None
        if ENABLE_CACHE:
            try:
                from llama_index.core.ingestion import IngestionCache
                from llama_index.storage.kvstore.redis import RedisKVStore as RedisCache

                cache = IngestionCache(
                    cache=RedisCache.from_host_and_port(host=REDIS_HOST, port=REDIS_PORT),
                    collection=CACHE_COLLECTION,
                )
                logger.info(f"✅ Redis Cache enabled: {REDIS_HOST}:{REDIS_PORT}/{CACHE_COLLECTION}")
            except Exception as e:
                logger.warning(f"⚠️  Redis cache not available: {e}")
                cache = None

        # Document store for deduplication (production best practice)
        # CRITICAL: Use RedisDocumentStore for persistent cross-session deduplication
        docstore = None
        docstore_strategy = None
        if ENABLE_CACHE:
            try:
                from llama_index.storage.docstore.redis import RedisDocumentStore
                from llama_index.core.ingestion import DocstoreStrategy

                docstore = RedisDocumentStore.from_host_and_port(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    namespace="cortex_docstore"
                )
                docstore_strategy = DocstoreStrategy.UPSERTS
                logger.info(f"✅ Redis Docstore enabled: {REDIS_HOST}:{REDIS_PORT}/cortex_docstore")
            except Exception as e:
                logger.warning(f"⚠️  Redis docstore not available: {e}")
                logger.info("   Falling back to SimpleDocumentStore (in-memory)")
                from llama_index.core.storage.docstore import SimpleDocumentStore
                docstore = SimpleDocumentStore()
        else:
            from llama_index.core.storage.docstore import SimpleDocumentStore
            docstore = SimpleDocumentStore()
            logger.info("   Using SimpleDocumentStore (in-memory, no Redis)")

        # Ingestion pipeline for Qdrant (chunking + embedding)
        # Production optimizations: caching, docstore, parallel processing
        pipeline_kwargs = {
            "transformations": [
                SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP),
                self.embed_model
            ],
            "vector_store": self.vector_store,
            "cache": cache,  # Production: Redis caching for transformations
            "docstore": docstore,  # Production: Document deduplication
        }

        # Add docstore_strategy only if we have Redis (requires vector_store)
        if docstore_strategy and ENABLE_CACHE:
            pipeline_kwargs["docstore_strategy"] = docstore_strategy

        self.qdrant_pipeline = IngestionPipeline(**pipeline_kwargs)

        logger.info(f"✅ Ingestion Pipeline: chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}")
        if cache:
            logger.info(f"   📦 Caching: Enabled (Redis)")
        if docstore_strategy:
            logger.info(f"   📚 Docstore: Redis with UPSERTS strategy (cross-session dedup)")
        else:
            logger.info(f"   📚 Docstore: SimpleDocumentStore (session-only dedup)")
        logger.info(f"   ⚡ Parallel workers: {NUM_WORKERS}")

        logger.info("✅ Universal Ingestion Pipeline ready")
        logger.info("   Architecture: IngestionPipeline → Qdrant")
        logger.info("   Storage: Qdrant vector store (chunks with embeddings + metadata)")
        logger.info("   Supports: Emails, PDFs, Sheets, Structured data")

    async def ingest_document(
        self,
        document_row: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Ingest ANY document from Supabase row (universal format).

        Process:
        1. Create Document from Supabase row
        2. Chunk text and embed → Store in Qdrant

        Args:
            document_row: Supabase document row (from 'documents' table)
                Required fields: id, content, title
                Optional: source, document_type, metadata, etc.

        Returns:
            Dict with ingestion results
        """
        # Universal field extraction (works for both emails and documents tables)
        doc_id = document_row.get("id")
        source = document_row.get("source", "unknown")
        document_type = document_row.get("document_type", "document")

        # Title: try 'title' first (documents), fallback to 'subject' (emails)
        title = document_row.get("title") or document_row.get("subject", "Untitled")

        # Content: try 'content' first (documents), fallback to 'full_body' (emails)
        content = document_row.get("content") or document_row.get("full_body", "")

        # Metadata
        company_id = document_row.get("company_id", "")  # CRITICAL: Multi-tenant isolation
        tenant_id = document_row.get("tenant_id", "")
        source_id = document_row.get("source_id") or document_row.get("message_id", str(doc_id))
        created_at = document_row.get("source_created_at") or document_row.get("received_datetime", "")

        # FIX: Attachments inherit timestamp from parent email
        # CRITICAL for time-filtered queries (e.g., "show me emails from last week")
        # Without this, 70%+ of Qdrant data has created_at_timestamp = None
        if not created_at and document_row.get("parent_document_id"):
            parent_id = document_row.get("parent_document_id")
            logger.info(f"   📎 Attachment detected with no timestamp, fetching from parent document {parent_id}...")

            try:
                # Query parent document's timestamp from Supabase
                from supabase import create_client
                import os
                supabase = create_client(
                    os.getenv('SUPABASE_URL'),
                    os.getenv('SUPABASE_ANON_KEY')
                )

                parent_result = supabase.table('documents') \
                    .select('source_created_at') \
                    .eq('id', parent_id) \
                    .single() \
                    .execute()

                if parent_result.data and parent_result.data.get('source_created_at'):
                    created_at = parent_result.data['source_created_at']
                    logger.info(f"   ✅ Inherited timestamp from parent: {created_at}")
            except Exception as e:
                logger.warning(f"   ⚠️  Could not fetch parent timestamp: {e}")

        # Convert created_at to Unix timestamp for Qdrant filtering
        created_at_timestamp = None
        if created_at:
            try:
                from dateutil import parser
                if isinstance(created_at, str):
                    dt = parser.parse(created_at)
                else:
                    dt = created_at
                created_at_timestamp = int(dt.timestamp())
            except Exception as e:
                logger.warning(f"   ⚠️  Could not parse created_at timestamp: {e}")

        logger.info(f"\n{'='*80}")
        logger.info(f"📄 INGESTING DOCUMENT: {title}")
        logger.info(f"{'='*80}")
        logger.info(f"   ID: {doc_id}")
        logger.info(f"   Source: {source}")
        logger.info(f"   Type: {document_type}")
        logger.info(f"   Length: {len(content)} characters")

        try:
            # Step 1: Create Document for Qdrant ingestion
            # Build metadata from document_row (preserve all fields)
            doc_metadata = {
                "document_id": str(doc_id),
                "source_id": source_id,
                "canonical_id": source_id,  # CANONICAL ID: Explicit field for deduplication
                "title": title,
                "source": source,
                "document_type": document_type,
                "company_id": company_id,  # CRITICAL: Multi-tenant isolation for search filtering
                "tenant_id": tenant_id,
                "created_at": str(created_at),
                "created_at_timestamp": created_at_timestamp,  # Unix timestamp for filtering
                "source_created_at": str(created_at) if created_at else None,  # ISO datetime string for Qdrant datetime field
                # THREAD DEDUPLICATION: Add thread metadata to Qdrant payload
                "thread_id": document_row.get("metadata", {}).get("thread_id", "") or
                            document_row.get("raw_data", {}).get("thread_id", ""),
                "message_id": document_row.get("metadata", {}).get("message_id", "") or
                             document_row.get("raw_data", {}).get("message_id", "")
            }

            # Add file metadata if available (for attachments/files)
            if document_row.get("file_url"):
                doc_metadata["file_url"] = document_row["file_url"]
            if document_row.get("file_size_bytes"):
                doc_metadata["file_size_bytes"] = document_row["file_size_bytes"]
            if document_row.get("mime_type"):
                doc_metadata["mime_type"] = document_row["mime_type"]

            # CRITICAL: Add parent_document_id for attachment grouping
            # This allows chat.py to group attachments with parent email
            if document_row.get("parent_document_id"):
                doc_metadata["parent_document_id"] = str(document_row["parent_document_id"])

            # Merge in any additional metadata from the row (TRUNCATE to prevent metadata > chunk_size error)
            if "metadata" in document_row and document_row["metadata"]:
                additional_meta = {}
                if isinstance(document_row["metadata"], dict):
                    additional_meta = document_row["metadata"]
                elif isinstance(document_row["metadata"], str):
                    try:
                        additional_meta = json.loads(document_row["metadata"])
                    except:
                        pass

                # Truncate metadata values to prevent total metadata length > chunk size
                MAX_META_VALUE_LEN = 200  # Max chars per metadata value
                for key, value in additional_meta.items():
                    # Skip overwriting thread_id/message_id if they're already set (from raw_data fallback)
                    if key in ['thread_id', 'message_id'] and doc_metadata.get(key):
                        continue

                    if isinstance(value, list):
                        # Convert lists to JSON strings for consistent storage
                        doc_metadata[key] = json.dumps(value)
                    elif isinstance(value, str) and len(value) > MAX_META_VALUE_LEN:
                        doc_metadata[key] = value[:MAX_META_VALUE_LEN] + "..."
                    else:
                        doc_metadata[key] = value

            # For emails: preserve email-specific fields
            if document_type == "email":
                # Convert to_addresses to JSON string for consistent storage
                to_addrs = document_row.get("to_addresses", "[]")
                if isinstance(to_addrs, list):
                    to_addrs = json.dumps(to_addrs)

                doc_metadata.update({
                    "sender_name": document_row.get("sender_name", ""),
                    "sender_address": document_row.get("sender_address", ""),
                    "to_addresses": to_addrs,
                })

            # CRITICAL: Set doc_id to ensure chunks preserve original document_id
            # Without this, LlamaIndex overwrites document_id with chunk node_id
            document = Document(
                text=content,
                metadata=doc_metadata,
                doc_id=str(doc_id)  # Force chunks to inherit this as ref_doc_id
            )

            # Step 2: Chunk, embed, and store in Qdrant
            # Production: Use parallel processing with num_workers
            logger.info("   → Chunking text and embedding...")
            self.qdrant_pipeline.run(
                documents=[document],
                show_progress=SHOW_PROGRESS,
                num_workers=NUM_WORKERS  # Production: Parallel processing
            )
            logger.info("   ✅ Stored chunks in Qdrant")

            # Neo4j entity extraction removed - vector-only system
            # All document content is searchable via Qdrant semantic search

            logger.info(f"✅ DOCUMENT INGESTION COMPLETE: {title}")
            logger.info(f"{'='*80}\n")

            return {
                "status": "success",
                "document_id": str(doc_id),
                "source_id": source_id,
                "title": title,
                "source": source,
                "document_type": document_type,
                "characters": len(content)
            }

        except Exception as e:
            error_msg = f"Document ingestion failed: {str(e)}"
            logger.error(f"❌ {error_msg}", exc_info=True)
            return {
                "status": "error",
                "error": error_msg,
                "document_id": str(doc_id),
                "title": title
            }

    async def ingest_documents_batch(
        self,
        document_rows: List[Dict[str, Any]],
        num_workers: int = 4
    ) -> List[Dict[str, Any]]:
        """
        Batch ingestion with parallel Qdrant processing.

        Process:
        1. Create Document objects from Supabase rows
        2. Parallel chunking + embedding → Qdrant (async with num_workers)

        Args:
            document_rows: List of Supabase document rows
            num_workers: Parallel workers for Qdrant (default: 4)

        Returns:
            List of ingestion results (one per document)

        Performance:
        - Sequential: ~2-3 documents/second
        - Batch (num_workers=4): ~8-12 documents/second
        - Recommended batch size: 50-100 documents per call
        """
        import asyncio
        import time

        if not document_rows:
            return []

        start_time = time.time()
        logger.info(f"{'='*80}")
        logger.info(f"🚀 BATCH INGESTION: {len(document_rows)} documents")
        logger.info(f"   Qdrant workers: {num_workers}")
        logger.info(f"{'='*80}")

        results = []

        try:
            # Step 1: Create Document objects for Qdrant pipeline
            documents = []

            prep_start = time.time()
            for doc_row in document_rows:
                doc_id = doc_row.get("id")
                source = doc_row.get("source", "unknown")
                document_type = doc_row.get("document_type", "document")
                title = doc_row.get("title") or doc_row.get("subject", "Untitled")
                content = doc_row.get("content") or doc_row.get("full_body", "")

                if not content or not content.strip():
                    logger.warning(f"⚠️  Skipping document {doc_id}: empty content")
                    results.append({
                        "status": "skipped",
                        "document_id": str(doc_id),
                        "title": title,
                        "reason": "empty_content"
                    })
                    continue

                # Get timestamp
                created_at = doc_row.get("source_created_at") or doc_row.get("received_datetime", "")
                created_at_timestamp = None
                if created_at:
                    try:
                        from dateutil import parser
                        if isinstance(created_at, str):
                            dt = parser.parse(created_at)
                        else:
                            dt = created_at
                        created_at_timestamp = int(dt.timestamp())
                    except Exception as e:
                        logger.warning(f"   ⚠️  Could not parse timestamp for doc {doc_id}: {e}")

                # Build metadata
                doc_metadata = {
                    "document_id": str(doc_id),
                    "title": title,
                    "source": source,
                    "document_type": document_type,
                    "tenant_id": doc_row.get("tenant_id", ""),
                    "source_id": doc_row.get("source_id") or doc_row.get("message_id", str(doc_id)),
                    "created_at": str(created_at),
                    "created_at_timestamp": created_at_timestamp,
                }

                document = Document(
                    text=content,
                    metadata=doc_metadata,
                    doc_id=str(doc_id)
                )

                documents.append(document)

            prep_time = time.time() - prep_start
            logger.info(f"   Prepared {len(documents)} documents in {prep_time:.2f}s")

            # Step 2: Parallel chunking + embedding → Qdrant
            qdrant_start = time.time()
            logger.info(f"📦 Processing {len(documents)} documents with {num_workers} Qdrant workers...")
            nodes = await self.qdrant_pipeline.arun(
                documents=documents,
                num_workers=num_workers
            )
            qdrant_time = time.time() - qdrant_start
            logger.info(f"✅ Created {len(nodes)} chunks in Qdrant ({qdrant_time:.2f}s, {len(nodes)/qdrant_time:.1f} chunks/sec)")

            # Build success results
            for doc_row in document_rows:
                results.append({
                    "status": "success",
                    "document_id": str(doc_row.get("id")),
                    "title": doc_row.get("title") or doc_row.get("subject", "Untitled")
                })

            # Summary
            total_time = time.time() - start_time
            success_count = len([r for r in results if r.get("status") == "success"])

            logger.info(f"{'='*80}")
            logger.info(f"✅ BATCH COMPLETE: {success_count}/{len(document_rows)} successful")
            logger.info(f"   Total time: {total_time:.2f}s ({len(document_rows)/total_time:.1f} docs/sec)")
            logger.info(f"   Breakdown: prep={prep_time:.1f}s, qdrant={qdrant_time:.1f}s")
            logger.info(f"{'='*80}")

            return results

        except Exception as e:
            error_msg = f"Batch ingestion failed: {str(e)}"
            logger.error(f"❌ {error_msg}", exc_info=True)
            return [{
                "status": "error",
                "error": error_msg,
                "document_id": "batch",
                "total_documents": len(document_rows)
            }]

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics from Qdrant vector store."""
        stats = {}

        try:
            client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
            collection = client.get_collection(QDRANT_COLLECTION_NAME)
            stats["qdrant_points"] = collection.points_count
            stats["qdrant_vectors_count"] = collection.vectors_count
            client.close()
        except Exception as e:
            logger.error(f"Failed to get Qdrant stats: {e}")
            stats["qdrant_error"] = str(e)

        return stats

