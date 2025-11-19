"""
DEPRECATED: Use app.services.rag instead

This module has been renamed to remove implementation details from the name.
All new code should import from app.services.rag
"""

# Re-export everything from new location for backward compatibility
from app.services.rag.pipeline import UniversalIngestionPipeline
from app.services.rag.query import HybridQueryEngine

__all__ = [
    "UniversalIngestionPipeline",
    "HybridQueryEngine",
]
