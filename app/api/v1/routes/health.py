"""
Health Check Routes
System status and diagnostics
"""
import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@router.get("/")
async def root():
    """Root endpoint with API info."""
    return {
        "name": "Email Connector & RAG Search API",
        "version": "1.0.0",
        "description": "Unified backend for email sync (Gmail/Outlook) and hybrid RAG search",
        "endpoints": {
            "health": "/health",
            "oauth": "/connect/start",
            "status": "/status",
            "sync": {
                "outlook": "/sync/once",
                "gmail": "/sync/once/gmail"
            },
            "search": "/api/v1/search"
        }
    }
