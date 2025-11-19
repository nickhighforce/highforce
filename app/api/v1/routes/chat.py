"""
Chat Routes - Simple Query Interface for Hybrid Property Graph System
Uses HybridQueryEngine with SubQuestionQueryEngine (VectorStoreIndex (Qdrant))
"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from supabase import Client

from app.core.dependencies import get_supabase
from app.core.security import get_current_user_id, get_current_user_context
from app.middleware.rate_limit import limiter
from app.core.circuit_breakers import with_openai_retry
import app.core.dependencies as deps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatMessage(BaseModel):
    """Chat message"""
    question: str
    company_id: Optional[str] = None
    chat_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Chat response"""
    question: str
    answer: str
    source_count: int
    sources: List[Dict[str, Any]]
    chat_id: str


class CreateChatRequest(BaseModel):
    """Create new chat"""
    title: Optional[str] = None


class ChatHistoryItem(BaseModel):
    """Chat history item"""
    id: str
    title: Optional[str]
    created_at: str
    updated_at: str
    message_count: int


async def _get_query_engine():
    """Get the global query engine (initialized at startup)"""
    if not deps.query_engine:
        raise HTTPException(
            status_code=503,
            detail="Query engine not initialized. Please try again in a moment."
        )
    return deps.query_engine


@with_openai_retry
async def _execute_query_with_retry(engine, question: str):
    """
    Execute query with automatic retry on OpenAI failures.
    Prevents cascading failures when OpenAI has issues.
    """
    return await engine.query(question)


@with_openai_retry
async def _execute_chat_with_retry(engine, message: str, chat_history: Optional[List[Dict]] = None, filters: Optional[Dict] = None):
    """
    Execute conversational chat with automatic retry on OpenAI failures.
    Uses CondensePlusContextChatEngine for natural conversations with memory.
    """
    return await engine.chat(message, chat_history=chat_history, filters=filters)


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")  # 20 chat requests per minute per IP
async def chat(
    request: Request,
    message: ChatMessage,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Simple chat interface for testing the hybrid property graph retrieval.
    Now saves chat history to Supabase!

    Uses:
    - VectorStoreIndex (Qdrant vector search only)
    - VectorContextRetriever for graph-aware vector search
    - LLMSynonymRetriever for query expansion with entity synonyms
    - Multi-strategy concurrent retrieval with intelligent result merging

    Args:
        message: User question
        user_context: User context (user_id, company_id, company_id)
        supabase: Supabase client

    Returns:
        ChatResponse: Answer + sources
    """
    try:
        # Get global query engine (initialized at startup)
        engine = await _get_query_engine()

        # Extract user context
        user_id = user_context["user_id"]  # Actual user ID for private chats
        company_id = user_context["company_id"]  # Company ID for shared data queries

        logger.info(f"💬 Chat query: {message.question}")
        logger.info(f"🔑 AUTH - user_id: {user_id[:8]}..., company_id: {company_id[:8]}...")

        # Create or get chat
        chat_id = message.chat_id
        if not chat_id:
            # Generate iPhone Notes-style title (first 3-5 words)
            words = message.question.strip().split()
            title_words = words[:5] if len(words) > 5 else words
            title = ' '.join(title_words)
            if len(words) > 5:
                title += '...'

            # Create new chat (private to this user)
            logger.info(f"📝 Creating chat for user_id: {user_id[:8]}...")
            chat_result = supabase.table('chats').insert({
                'company_id': company_id,  # For company association
                'user_email': user_id,     # Private to this user
                'title': title
            }).execute()
            chat_id = chat_result.data[0]['id']
            logger.info(f"✅ Created new chat: {chat_id} - '{title}'")

        # Get existing chat history from database (for conversation context)
        chat_history = []
        if chat_id:
            # Fetch previous messages for this chat to restore context
            history_result = supabase.table('chat_messages')\
                .select('role, content')\
                .eq('chat_id', chat_id)\
                .order('created_at', desc=False)\
                .execute()

            if history_result.data:
                chat_history = [
                    {"role": msg['role'], "content": msg['content']}
                    for msg in history_result.data
                ]
                logger.info(f"📚 Loaded {len(chat_history)} previous messages for context")

        # Save user message
        supabase.table('chat_messages').insert({
            'chat_id': chat_id,
            'role': 'user',
            'content': message.question
        }).execute()

        # Execute conversational chat with full history context
        # Uses CondensePlusContextChatEngine for:
        # - Natural greeting handling ("hey" → friendly response, no retrieval)
        # - Context-aware follow-ups ("tell me more" → knows what "more" refers to)
        # - Full SubQuestionQueryEngine pipeline (vector + graph + reranking)
        # CRITICAL: Pass company_id for data isolation
        filters = {'company_id': company_id}
        result = await _execute_chat_with_retry(engine, message.question, chat_history=chat_history, filters=filters)

        logger.info(f"🔍 Query result keys: {result.keys()}")
        logger.info(f"🔍 Source nodes count: {len(result.get('source_nodes', []))}")

        # Format source nodes - Filter out entity nodes and deduplicate documents
        sources = []
        seen_documents = set()  # Track unique documents by ID or name
        source_index = 1
        
        for node in result.get('source_nodes', []):
            metadata = node.metadata if hasattr(node, 'metadata') else {}

            # Extract document_id for clickable sources - try multiple field names
            document_id = (
                metadata.get('document_id') or
                metadata.get('doc_id') or
                metadata.get('id') or
                None
            )

            # FILTER OUT non-document sources:
            # 1. Entity nodes (PERSON, COMPANY, etc.) - they don't have 'source' field
            # 2. Chunk nodes without proper document metadata
            # 3. Any node without a valid source system
            source_system = metadata.get('source', None)
            
            # Skip if no source system (likely an entity node)
            if not source_system or source_system == 'Unknown':
                logger.debug(f"   ⏭️  Skipping entity/chunk node. Available keys: {list(metadata.keys())}")
                continue
                
            # Skip if no document metadata at all
            has_doc_metadata = any([
                metadata.get('title'),
                metadata.get('document_name'), 
                metadata.get('document_type'),
                metadata.get('created_at'),
                document_id
            ])
            
            if not has_doc_metadata:
                logger.debug(f"   ⏭️  Skipping node without document metadata")
                continue

            # DEDUPLICATE: Group by parent email (for attachments)
            # If this is an attachment (has parent_document_id), use parent ID as unique key
            # This ensures email + all attachments show as ONE source bubble
            parent_doc_id = metadata.get('parent_document_id')
            doc_name = metadata.get('title', metadata.get('document_name', 'Untitled'))

            if parent_doc_id:
                # This is an attachment - group by parent email
                unique_key = f"parent:{parent_doc_id}"
                # Use parent document as the source (not the attachment)
                document_id = parent_doc_id
                logger.debug(f"   📎 Attachment detected, grouping under parent {parent_doc_id}")
            else:
                # This is a standalone document
                unique_key = str(document_id) if document_id else f"{source_system}:{doc_name}"

            # Skip if we've already seen this document
            if unique_key in seen_documents:
                logger.debug(f"   🔄 Skipping duplicate document: {doc_name}")
                continue

            seen_documents.add(unique_key)

            # This is a valid, unique document source
            # Clean document name: remove "[Outlook Embedded]" prefix
            clean_doc_name = doc_name.replace('[Outlook Embedded] ', '') if doc_name else doc_name

            # Get parent_document_id - if missing, try to lookup via email_id
            parent_doc_id = metadata.get('parent_document_id', None)
            if not parent_doc_id and metadata.get('email_id'):
                # This is an attachment without parent_document_id set
                # Lookup parent email by source_id (message_id)
                try:
                    email_id = metadata.get('email_id')
                    parent_lookup = supabase.table('documents')\
                        .select('id')\
                        .eq('company_id', user_id)\
                        .eq('source_id', email_id)\
                        .eq('document_type', 'email')\
                        .limit(1)\
                        .execute()

                    if parent_lookup.data and len(parent_lookup.data) > 0:
                        parent_doc_id = parent_lookup.data[0]['id']
                        logger.info(f"   🔗 Found parent email for attachment via email_id lookup: {parent_doc_id}")
                except Exception as e:
                    logger.warning(f"   ⚠️  Failed to lookup parent email: {e}")

            source_info = {
                'index': source_index,
                'document_id': str(document_id) if document_id is not None else None,
                'document_name': clean_doc_name,
                'source': source_system,
                'document_type': metadata.get('document_type', 'document'),
                'timestamp': metadata.get('created_at', metadata.get('timestamp', 'Unknown')),
                'text_preview': node.text[:200] if hasattr(node, 'text') else '',
                'score': node.score if hasattr(node, 'score') else None,
                'file_url': metadata.get('file_url', None),
                'parent_document_id': str(parent_doc_id) if parent_doc_id is not None else None  # For "Explore Chain" feature
            }
            sources.append(source_info)
            logger.info(f"   📄 Source {source_index}: {source_info['source']} - {source_info['document_name']}")
            source_index += 1

        # Save assistant message
        supabase.table('chat_messages').insert({
            'chat_id': chat_id,
            'role': 'assistant',
            'content': result['answer'],
            'sources': sources
        }).execute()

        # Update chat timestamp
        supabase.table('chats').update({
            'updated_at': datetime.utcnow().isoformat()
        }).eq('id', chat_id).execute()

        logger.info(f"✅ Retrieved {len(sources)} sources, saved to chat {chat_id}")

        return ChatResponse(
            question=message.question,
            answer=result['answer'],
            source_count=len(sources),
            sources=sources,
            chat_id=chat_id
        )

    except Exception as e:
        logger.error(f"Chat error: {str(e)}", exc_info=True)

        # Provide more helpful error messages based on error type
        error_message = "I'm experiencing technical difficulties. Please try again in a moment."

        # Check for specific error types
        error_str = str(e).lower()
        if "timeout" in error_str or "connection" in error_str:
            error_message = "The knowledge base is taking longer than expected to respond. Please try again."
        elif "qdrant" in error_str:
            error_message = "Unable to connect to the knowledge base. Please try again."
        elif "qdrant" in error_str:
            error_message = "Unable to connect to the graph database. Please try again."
        elif "openai" in error_str or "api" in error_str:
            error_message = "The AI service is temporarily unavailable. Please try again."

        raise HTTPException(
            status_code=500,
            detail=error_message
        )


@router.get("/chats")
async def list_chats(
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase),
    limit: int = 50
):
    """
    Get user's private chat history.

    Returns list of chats ordered by most recent, filtered by user_id.
    """
    try:
        # Get chats for this specific user within their company
        user_id = user_context["user_id"]
        company_id = user_context["company_id"]
        logger.info(f"📋 Listing chats for user_id: {user_id[:8]}..., company_id: {company_id[:8]}...")

        result = supabase.table('chats')\
            .select('id, title, created_at, updated_at')\
            .eq('user_email', user_id)\
            .eq('company_id', company_id)\
            .order('updated_at', desc=True)\
            .limit(limit)\
            .execute()

        chats = []
        for chat in result.data:
            # Get message count
            msg_result = supabase.table('chat_messages')\
                .select('id', count='exact')\
                .eq('chat_id', chat['id'])\
                .execute()
            
            chats.append({
                'id': chat['id'],
                'title': chat['title'],
                'created_at': chat['created_at'],
                'updated_at': chat['updated_at'],
                'message_count': msg_result.count or 0
            })

        return {'chats': chats}

    except Exception as e:
        logger.error(f"Failed to list chats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chats/{chat_id}/messages")
async def get_chat_messages(
    chat_id: str,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Get all messages in a chat.

    Returns messages in chronological order.
    """
    try:
        user_id = user_context["user_id"]
        company_id = user_context["company_id"]

        # Verify chat belongs to this user AND company
        chat_result = supabase.table('chats')\
            .select('id')\
            .eq('id', chat_id)\
            .eq('user_email', user_id)\
            .eq('company_id', company_id)\
            .execute()

        if not chat_result.data:
            raise HTTPException(status_code=404, detail="Chat not found")

        # Get messages
        result = supabase.table('chat_messages')\
            .select('*')\
            .eq('chat_id', chat_id)\
            .order('created_at', desc=False)\
            .execute()

        return {'messages': result.data}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get messages: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chats")
async def create_chat(
    request: CreateChatRequest,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Create a new empty chat.

    Returns the new chat ID.
    """
    try:
        user_id = user_context["user_id"]
        company_id = user_context["company_id"]

        result = supabase.table('chats').insert({
            'company_id': company_id,  # For company association
            'user_email': user_id,     # Private to this user
            'title': request.title or 'New Chat'
        }).execute()

        return {'chat_id': result.data[0]['id']}

    except Exception as e:
        logger.error(f"Failed to create chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: str,
    user_context: dict = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
):
    """
    Delete a chat and all its messages.

    Messages are automatically deleted via CASCADE.
    """
    try:
        user_id = user_context["user_id"]
        company_id = user_context["company_id"]

        # Verify ownership (user_id AND company_id) and delete
        result = supabase.table('chats')\
            .delete()\
            .eq('id', chat_id)\
            .eq('user_email', user_id)\
            .eq('company_id', company_id)\
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Chat not found")

        return {'success': True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete chat: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sources/{document_id}")
async def get_source_document(
    document_id: str,
    user_id: str = Depends(get_current_user_id),
    supabase: Client = Depends(get_supabase)
):
    """
    Get full document details for a source.
    Used when user clicks on a source bubble to see the original content.

    SMART GROUPING:
    - If document is an attachment, returns parent email + all attachments
    - If document is an email with attachments, returns email + all attachments
    - If document is standalone (PDF, doc, etc.), returns just that document

    Returns:
        Full document with content, metadata, file_url, and attachments array
    """
    try:
        # Fetch the requested document
        result = supabase.table('documents')\
            .select('*')\
            .eq('id', document_id)\
            .eq('company_id', user_id)\
            .execute()

        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail="Source document not found")

        document = result.data[0]

        # Determine if we need to fetch parent email + attachments
        parent_id = document.get('parent_document_id')
        is_attachment = parent_id is not None

        # CASE 1: Document is an attachment → fetch parent email instead
        if is_attachment:
            logger.info(f"   📎 Document {document_id} is an attachment, fetching parent email {parent_id}")
            parent_result = supabase.table('documents')\
                .select('*')\
                .eq('id', parent_id)\
                .eq('company_id', user_id)\
                .execute()

            if parent_result.data and len(parent_result.data) > 0:
                # Use parent email as primary document
                document = parent_result.data[0]
                document_id = parent_id  # Switch to parent ID for attachment fetching below
            else:
                logger.warning(f"   ⚠️  Parent document {parent_id} not found, showing attachment standalone")

        # CASE 2 & 3: Fetch all attachments for this document (if any)
        attachments_result = supabase.table('documents')\
            .select('*')\
            .eq('parent_document_id', document_id)\
            .eq('company_id', user_id)\
            .execute()

        attachments = []
        if attachments_result.data:
            for att in attachments_result.data:
                attachments.append({
                    'id': att['id'],
                    'title': att['title'],
                    'file_url': att.get('file_url'),
                    'mime_type': att.get('mime_type'),
                    'file_size_bytes': att.get('file_size_bytes'),
                    'document_type': att.get('document_type', 'attachment'),
                    'content': att.get('content', ''),  # Extracted text (for fallback)
                })

        logger.info(f"   📧 Returning document {document_id} with {len(attachments)} attachments")

        return {
            'id': document['id'],
            'title': document['title'],
            'content': document['content'],
            'source': document['source'],
            'document_type': document['document_type'],
            'source_id': document['source_id'],
            'created_at': document.get('source_created_at', document.get('ingested_at')),
            'metadata': document.get('metadata', {}),
            'raw_data': document.get('raw_data', {}),
            # File storage fields (for PDFs, images, etc.)
            'file_url': document.get('file_url'),
            'mime_type': document.get('mime_type'),
            'file_size_bytes': document.get('file_size_bytes'),
            # Email fields (for Outlook/Gmail)
            'sender_name': document.get('sender_name'),
            'sender_address': document.get('sender_address'),
            'to_addresses': document.get('to_addresses'),
            # Attachments array (empty if none)
            'attachments': attachments
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get source document: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chat/health")
async def chat_health():
    """Check if hybrid query engine is initialized"""
    return {
        "initialized": deps.query_engine is not None,
        "engine": "HybridQueryEngine" if deps.query_engine else None
    }
