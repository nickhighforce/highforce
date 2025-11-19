"""
Pydantic Schemas
All request/response models for API endpoints
"""

# Connector schemas (OAuth, webhooks)
from .connector import NangoOAuthCallback, NangoWebhook

# Health check schemas
from .health import HealthResponse, EpisodeContextResponse

# Ingestion schemas
from .ingestion import DocumentIngest, DocumentIngestResponse

# Search schemas
from .search import Message, SearchQuery, VectorResult, GraphResult, SearchResponse

# Sync schemas
from .sync import SyncResponse

__all__ = [
    # Connector
    "NangoOAuthCallback",
    "NangoWebhook",
    # Health
    "HealthResponse",
    "EpisodeContextResponse",
    # Ingestion
    "DocumentIngest",
    "DocumentIngestResponse",
    # Search
    "Message",
    "SearchQuery",
    "VectorResult",
    "GraphResult",
    "SearchResponse",
    # Sync
    "SyncResponse",
]
