"""
Search Schemas
Models for hybrid RAG search (vector + knowledge graph)
"""
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class Message(BaseModel):
    """Chat message for conversation history."""
    role: str = Field(..., description="Role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class SearchQuery(BaseModel):
    """
    Request model for hybrid search.
    Combines vector similarity search with knowledge graph traversal.
    """
    query: str = Field(..., description="Search query text")
    vector_limit: int = Field(5, description="Max vector search results", ge=1, le=20)
    graph_limit: int = Field(5, description="Max knowledge graph results", ge=1, le=20)
    source_filter: Optional[str] = Field(None, description="Filter by source (gmail, slack, etc.)")
    conversation_history: Optional[List[Message]] = Field(default=[], description="Previous messages for context")
    include_full_emails: bool = Field(True, description="Auto-fetch full emails from Supabase using episode_ids")


class VectorResult(BaseModel):
    """Vector search result from Qdrant."""
    id: str
    document_name: str
    source: str
    document_type: str
    content: str
    chunk_index: int
    episode_id: str
    similarity: float
    metadata: Optional[Dict[str, Any]]


class GraphResult(BaseModel):
    """Knowledge graph result from Neo4j via LlamaIndex."""
    type: str
    relation_name: str
    fact: str
    source_node_id: str
    target_node_id: str
    valid_at: Optional[str]
    episodes: List[str]


class SearchResponse(BaseModel):
    """
    Response model for hybrid search.
    Includes AI-generated answer plus raw results.
    """
    success: bool
    query: str
    answer: str  # AI-generated conversational answer
    vector_results: List[VectorResult]
    graph_results: List[GraphResult]
    num_episodes: int
    message: str
    full_emails: Optional[List[Dict[str, Any]]] = Field(None, description="Full email objects from Supabase (if include_full_emails=true)")
