"""
Health Check Schemas
Models for system health and debug endpoints
"""
from typing import List, Dict, Any
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    version: str
    vector_db: str
    knowledge_graph: str


class EpisodeContextResponse(BaseModel):
    """
    Response model for episode context endpoint.
    Returns all chunks for a given episode_id.
    """
    success: bool
    episode_id: str
    chunks: List[Dict[str, Any]]
    total_chunks: int
