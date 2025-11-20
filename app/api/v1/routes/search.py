"""
Search Routes
Hybrid RAG search (vector + knowledge graph) using LlamaIndex Hybrid Property Graph
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from supabase import Client

from app.core.config import settings
from app.core.security import get_current_user_context
from app.core.dependencies import get_supabase
from app.core import dependencies as deps
from app.models.schemas import SearchQuery, SearchResponse, VectorResult, GraphResult
from app.services.search.query_rewriter import rewrite_query_with_context
from app.middleware.rate_limit import limiter
from app.core.circuit_breakers import with_openai_retry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])


async def _get_query_engine():
    """Get the global query engine from dependencies"""
    if not deps.query_engine:
        raise HTTPException(status_code=503, detail="Query engine not initialized")
    return deps.query_engine


@with_openai_retry
async def _execute_search_with_retry(engine, query_text: str, filters: dict = None):
    """
    Execute search with automatic retry on OpenAI failures.
    Prevents cascading failures when OpenAI has issues.
    """
    return await engine.query(query_text, filters=filters)


@router.post("/search", response_model=SearchResponse)
@limiter.limit("30/minute")  # 30 searches per minute per IP
async def search(
    request: Request,
    query: SearchQuery,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Intelligent Hybrid Search using LlamaIndex Hybrid Query Engine.

    This endpoint uses HybridQueryEngine with SubQuestionQueryEngine:
    - SubQuestionQueryEngine: Breaks down complex queries into sub-questions
    - VectorStoreIndex (Qdrant): Semantic search over text chunks
    - VectorStoreIndex (Qdrant): Semantic search over document chunks
    - Multi-strategy concurrent retrieval with intelligent result merging
    - Synthesizes comprehensive answer from all retrieval strategies
    - Optionally fetches full email objects from Supabase

    Args:
        query: Search parameters (query, vector_limit, graph_limit, include_full_emails)
        user_context: Authenticated user context (user_id + company_id from JWT)
        supabase: Supabase client (dependency injection)

    Returns:
        SearchResponse: AI answer + source nodes + full email objects
    """
    # Extract user_id and company_id from JWT
    user_id = user_context["user_id"]
    company_id = user_context["company_id"]
    try:
        # Query rewriting with conversation context
        conversation_hist = [
            msg.dict() for msg in query.conversation_history
        ] if query.conversation_history else []

        rewritten_query = rewrite_query_with_context(query.query, conversation_hist)

        logger.info(f"Search - Original: {query.query}")
        logger.info(f"Search - Rewritten: {rewritten_query}")

        # Initialize hybrid query engine (lazy)
        engine = await _get_query_engine()

        # Execute query using hybrid retrieval with automatic retry on failures
        result = await _execute_search_with_retry(engine, rewritten_query)

        # Extract episode_ids and metadata from source nodes
        episode_ids = set()
        vector_results = []

        for i, node in enumerate(result.get('source_nodes', [])):
            metadata = node.metadata
            episode_id = metadata.get("episode_id", "")

            if episode_id:
                episode_ids.add(episode_id)

            # If file_url not in metadata, fetch from documents table (for old chunks)
            if not metadata.get("file_url") and metadata.get("document_id"):
                try:
                    doc_result = supabase.table("documents").select("file_url,mime_type,file_size_bytes").eq(
                        "id", metadata["document_id"]
                    ).single().execute()
                    if doc_result.data:
                        metadata["file_url"] = doc_result.data.get("file_url")
                        metadata["mime_type"] = doc_result.data.get("mime_type")
                        metadata["file_size_bytes"] = doc_result.data.get("file_size_bytes")
                except Exception as e:
                    logger.warning(f"Failed to fetch file_url for document {metadata['document_id']}: {e}")

            vector_results.append(VectorResult(
                id=str(i),
                document_name=metadata.get("document_name", "Unknown"),
                source=metadata.get("source", "Unknown"),
                document_type=metadata.get("document_type", "Unknown"),
                content=node.text,
                chunk_index=metadata.get("chunk_index", 0),
                episode_id=episode_id,
                similarity=node.score if hasattr(node, 'score') and node.score else 0.0,
                metadata=metadata
            ))

        # Graph data is integrated into hybrid retrieval automatically
        graph_results = []

        # Optionally fetch full emails from Supabase
        full_emails = None
        if query.include_full_emails and episode_ids:
            try:
                emails_result = supabase.table("emails").select("*").in_(
                    "episode_id", list(episode_ids)
                ).eq(
                    "company_id", company_id
                ).execute()

                full_emails = emails_result.data if emails_result.data else []
                logger.info(f"Fetched {len(full_emails)} full email(s) for {len(episode_ids)} episode(s)")
            except Exception as e:
                logger.warning(f"Failed to fetch full emails: {e}")

        return SearchResponse(
            success=True,
            query=query.query,
            answer=result['answer'],
            vector_results=vector_results,
            graph_results=graph_results,
            num_episodes=len(episode_ids),
            message=f"Found {len(vector_results)} source nodes across {len(episode_ids)} episodes",
            full_emails=full_emails
        )

    except Exception as e:
        logger.error(f"Search error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )
