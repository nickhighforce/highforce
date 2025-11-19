"""
Ingestion Schemas
Models for document ingestion into RAG pipeline
"""
from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field


class DocumentIngest(BaseModel):
    """
    Request model for document ingestion.
    Ingests documents into both vector store (Qdrant) and knowledge graph (Neo4j).
    """
    content: str = Field(..., description="Full document text content")
    document_name: str = Field(..., description="Name/title of the document")
    source: str = Field(..., description="Source system (gmail, outlook, slack, etc.)")
    document_type: str = Field(..., description="Document type (email, doc, deal, meeting, etc.)")
    reference_time: Optional[datetime] = Field(None, description="When the document was created")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")


class DocumentIngestResponse(BaseModel):
    """
    Response model for document ingestion.
    Returns episode_id that links vector and graph data.
    """
    success: bool
    episode_id: str  # Shared UUID linking vector chunks to graph episode
    document_name: str
    source: str
    document_type: str
    num_chunks: int
    message: str
