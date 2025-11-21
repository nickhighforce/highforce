"""
LlamaIndex Configuration

Architecture:
- IngestionPipeline → Qdrant (vector store only)
- SubQuestionQueryEngine for semantic search with recency boosting
"""

import os
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

# ============================================
# QDRANT CONFIGURATION
# ============================================

QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "HighForce")  # From .env or default to HighForce

# ============================================
# OPENAI CONFIGURATION
# ============================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# LLM for entity extraction
EXTRACTION_MODEL = "gpt-4o-mini"
EXTRACTION_TEMPERATURE = 0.0

# LLM for queries and synthesis
QUERY_MODEL = "gpt-4o-mini"
QUERY_TEMPERATURE = 0.0  # 0 for deterministic responses

# Embeddings
EMBEDDING_MODEL = "text-embedding-3-small"

# ============================================
# INGESTION PIPELINE CONFIGURATION
# ============================================

# Text chunking (per expert guidance)
# Increased to 1024 to handle long attachment metadata (filenames + CID + properties)
CHUNK_SIZE = 1024
CHUNK_OVERLAP = 50

# Vector search - Increased to 20 for better reranking performance
# Research: Retrieve more candidates (20) → rerank to final 10 for best accuracy
SIMILARITY_TOP_K = 20

# Progress display
SHOW_PROGRESS = True

# Parallel processing (production optimization)
NUM_WORKERS = 4  # For parallel node processing

# ============================================
# CACHING CONFIGURATION (Production)
# ============================================

# Redis cache for IngestionPipeline (optional but recommended for production)
# Set to None to disable caching, or provide Redis connection details
REDIS_HOST = os.getenv("REDIS_HOST", None)  # e.g., "127.0.0.1" or "redis.example.com"
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
CACHE_COLLECTION = "cortex_ingestion_cache"

# Enable caching if Redis is configured
ENABLE_CACHE = REDIS_HOST is not None
