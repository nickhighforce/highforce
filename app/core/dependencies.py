"""
Dependency Injection
Provides reusable dependencies for FastAPI routes

DEPENDENCIES:
- Supabase client (database + auth)
- Qdrant client (vector database)
- Redis client (job queue)
- HTTP client (for external APIs)
"""
import logging
from typing import Generator
import httpx
from supabase import create_client, Client
from qdrant_client import QdrantClient
import redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# GLOBAL CLIENTS (initialized once, reused across requests)
# ============================================================================

# Supabase client (singleton)
_supabase_client: Client = None

# Qdrant client (singleton)
_qdrant_client: QdrantClient = None

# Redis client (singleton)
_redis_client: redis.Redis = None

# Query engine (singleton) - lazy loaded when data exists
query_engine = None


# ============================================================================
# INITIALIZATION (called on app startup)
# ============================================================================

async def initialize_clients():
    """
    Initialize all global clients on app startup.

    Called from main.py lifespan event.
    """
    global _supabase_client, _qdrant_client, _redis_client

    logger.info("Initializing global clients...")

    # Supabase
    try:
        _supabase_client = create_client(
            settings.supabase_url,
            settings.supabase_service_key  # Backend uses service role
        )
        logger.info("✅ Supabase client initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Supabase: {e}")
        raise

    # Qdrant
    try:
        _qdrant_client = QdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=30.0
        )
        # Test connection
        _qdrant_client.get_collections()
        logger.info("✅ Qdrant client initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Qdrant: {e}")
        raise

    # Redis (optional for local dev)
    try:
        _redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5
        )
        # Test connection
        _redis_client.ping()
        logger.info("✅ Redis client initialized")
    except Exception as e:
        logger.warning(f"⚠️  Redis not available: {e}")
        logger.warning("⚠️  Background jobs will not work (OK for local dev)")
        _redis_client = None

    logger.info("✅ All clients initialized successfully")


async def shutdown_clients():
    """
    Shutdown all global clients on app shutdown.

    Called from main.py lifespan event.
    """
    global _supabase_client, _qdrant_client, _redis_client

    logger.info("Shutting down global clients...")

    # Qdrant
    if _qdrant_client:
        try:
            _qdrant_client.close()
            logger.info("✅ Qdrant client closed")
        except Exception as e:
            logger.error(f"Error closing Qdrant: {e}")

    # Redis
    if _redis_client:
        try:
            _redis_client.close()
            logger.info("✅ Redis client closed")
        except Exception as e:
            logger.error(f"Error closing Redis: {e}")

    # Supabase doesn't need explicit cleanup
    _supabase_client = None

    logger.info("✅ All clients shutdown complete")


# ============================================================================
# DEPENDENCY FUNCTIONS (injected into routes)
# ============================================================================

def get_supabase() -> Client:
    """
    Get Supabase client for dependency injection.

    Usage:
        @router.get("/example")
        async def example(supabase: Client = Depends(get_supabase)):
            result = supabase.table("documents").select("*").execute()
            return result.data

    Returns:
        Supabase client (service role, full access with RLS)
    """
    if _supabase_client is None:
        logger.error("Supabase client not initialized")
        raise RuntimeError("Supabase client not initialized. Call initialize_clients() first.")

    return _supabase_client


def get_qdrant() -> QdrantClient:
    """
    Get Qdrant client for dependency injection.

    Usage:
        @router.get("/search")
        async def search(qdrant: QdrantClient = Depends(get_qdrant)):
            results = qdrant.search(...)
            return results

    Returns:
        Qdrant client
    """
    if _qdrant_client is None:
        logger.error("Qdrant client not initialized")
        raise RuntimeError("Qdrant client not initialized. Call initialize_clients() first.")

    return _qdrant_client


def get_redis() -> redis.Redis:
    """
    Get Redis client for dependency injection.

    Usage:
        @router.get("/jobs")
        async def jobs(redis_client: redis.Redis = Depends(get_redis)):
            jobs = redis_client.keys("job:*")
            return jobs

    Returns:
        Redis client
    """
    if _redis_client is None:
        logger.error("Redis client not initialized")
        raise RuntimeError("Redis client not initialized. Call initialize_clients() first.")

    return _redis_client


def get_http_client() -> Generator[httpx.AsyncClient, None, None]:
    """
    Get HTTP client for external API calls.

    Usage:
        @router.get("/external")
        async def external(http: httpx.AsyncClient = Depends(get_http_client)):
            response = await http.get("https://api.example.com")
            return response.json()

    Yields:
        httpx.AsyncClient (auto-closed after request)
    """
    client = httpx.AsyncClient(timeout=30.0)
    try:
        yield client
    finally:
        client.aclose()


def get_rag_pipeline():
    """
    Get RAG ingestion pipeline for dependency injection.

    Usage:
        @router.post("/ingest")
        async def ingest(pipeline = Depends(get_rag_pipeline)):
            result = pipeline.ingest(...)
            return result

    Returns:
        UniversalIngestionPipeline instance (lazy loaded)
    """
    from app.services.rag import UniversalIngestionPipeline

    # Pipeline reads config from app.services.rag.config automatically
    return UniversalIngestionPipeline()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_qdrant_collection_name(company_id: str) -> str:
    """
    Get Qdrant collection name for a company.

    ARCHITECTURE DECISION:
    - All companies share ONE collection: "company_documents"
    - Documents filtered by company_id in query.filter
    - Simpler than per-company collections (easier to manage)

    Args:
        company_id: Company UUID (not used in current implementation)

    Returns:
        Collection name (currently always "company_documents")
    """
    # All companies share one collection, filtered by company_id
    return settings.qdrant_collection_name


def get_redis_key_prefix(company_id: str) -> str:
    """
    Get Redis key prefix for a company.

    Ensures Redis keys are namespaced by company_id.

    Args:
        company_id: Company UUID

    Returns:
        Key prefix: "company:{company_id}:"

    Example:
        company_id = "abc123"
        prefix = get_redis_key_prefix(company_id)  # "company:abc123:"
        redis.set(f"{prefix}sync_job:1", "data")  # Key: "company:abc123:sync_job:1"
    """
    return f"company:{company_id}:"


# Alias for backward compatibility
get_cortex_pipeline = get_rag_pipeline
