"""
Rate Limiting Middleware
Prevents abuse and DoS attacks using slowapi

RATE LIMITS:
- Global: 100 requests/minute per IP (default)
- Per-user (authenticated): 200 requests/minute
- File uploads: 10 uploads/hour per user
- OAuth starts: 20/hour per IP
- Search queries: 100/hour per user

SECURITY: Uses user_id for authenticated requests (can't bypass via IP switching)
"""
import logging
from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request

from app.core.config import settings

logger = logging.getLogger(__name__)


def rate_limit_key_func(request: Request) -> str:
    """
    Determine rate limit key based on authentication status.

    STRATEGY:
    - Authenticated requests: use user_id (more restrictive, can't bypass via IP)
    - Unauthenticated requests: use IP address

    This prevents:
    - Users bypassing limits by switching IPs (if authenticated)
    - Credential stuffing attacks (IP-based limiting on auth endpoints)
    """
    # Check if user is authenticated (user_id set by auth middleware)
    if hasattr(request.state, "user_id"):
        user_id = request.state.user_id
        # SECURITY: Don't log full user_id (PII)
        logger.debug(f"Rate limit key: user_id={user_id[:8]}...")
        return f"user:{user_id}"

    # Fall back to IP address for unauthenticated requests
    ip = get_remote_address(request)
    logger.debug(f"Rate limit key: ip={ip}")
    return f"ip:{ip}"


# Initialize rate limiter with smart key function
limiter = Limiter(
    key_func=rate_limit_key_func,
    default_limits=["100/minute"],  # Global default for all endpoints
    storage_uri="memory://",  # In-memory storage (single instance)
    # For multi-instance deployment with Redis:
    # storage_uri=f"redis://{settings.redis_host}:{settings.redis_port}/0"
)

