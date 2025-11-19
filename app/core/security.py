"""
Security and Authentication
Handles JWT validation and API key authentication

BREAKING CHANGES from old CORTEX:
- NO cross-database lookups (no Master Supabase query)
- company_id extracted directly from JWT app_metadata
- Simplified validation (single database)
- RLS policies enforce isolation at database level

SECURITY FEATURES:
- JWT validation via Supabase
- API key authentication with timing-safe comparison
- company_id in JWT custom claim (no query needed)
- RLS ensures database-level isolation
"""
import logging
import hmac
from typing import Dict, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from supabase import Client

from app.core.dependencies import get_supabase
from app.core.config import settings

logger = logging.getLogger(__name__)

# Security schemes
bearer_scheme = HTTPBearer()
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


# ============================================================================
# JWT AUTHENTICATION (Supabase) - SIMPLIFIED!
# ============================================================================

async def get_current_user_context(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    supabase: Client = Depends(get_supabase)
) -> Dict[str, str]:
    """
    Get full user context (user_id + company_id) for multi-tenant auth.

    BREAKING CHANGE: company_id is now in JWT app_metadata (no database query!)

    Flow:
    1. Validate JWT with Supabase Auth
    2. Extract user_id from JWT sub claim
    3. Extract company_id from JWT app_metadata.company_id
    4. Return context (no database lookup needed!)

    Returns:
        dict with:
        - user_id: User ID from JWT (for private data like chats)
        - company_id: Company ID from JWT metadata (for shared company data)
        - email: User email
        - role: User role in company (optional, from metadata)

    Security:
    - RLS policies enforce company_id isolation at database level
    - Even if attacker modifies company_id in JWT, signature validation fails
    - Database queries auto-filtered by RLS (defense in depth)

    Example JWT:
    {
        "sub": "user-uuid",
        "email": "user@example.com",
        "app_metadata": {
            "company_id": "company-uuid",
            "role": "owner"
        }
    }
    """
    if not credentials:
        logger.warning("No authorization credentials provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required"
        )

    token = credentials.credentials

    try:
        # Validate JWT with Supabase Auth
        response = supabase.auth.get_user(token)

        if not response or not response.user:
            logger.warning("JWT validation failed: no user returned")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication token"
            )

        user = response.user
        user_id = user.id
        email = user.email

        # Extract company_id from JWT app_metadata
        app_metadata = user.app_metadata or {}
        company_id = app_metadata.get("company_id")

        if not company_id:
            logger.error(f"User {user_id} has no company_id in JWT metadata")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User not assigned to any company. Contact support."
            )

        # Optional: Extract role from metadata
        role = app_metadata.get("role", "member")

        logger.info(f"✅ User authenticated: {email} (company_id: {company_id[:8]}...)")

        return {
            "user_id": user_id,
            "company_id": company_id,
            "email": email,
            "role": role
        }

    except HTTPException:
        # Re-raise HTTP exceptions (already formatted)
        raise
    except Exception as e:
        logger.error(f"JWT validation error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed"
        )


async def get_current_user_id(
    user_context: Dict[str, str] = Depends(get_current_user_context)
) -> str:
    """
    Extract just the user_id from the user context.

    This is a convenience function for endpoints that only need user_id.

    Returns:
        User ID string
    """
    return user_context["user_id"]


async def get_current_user_context_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> Optional[Dict[str, str]]:
    """
    Optional JWT authentication (for public endpoints that optionally show user data).

    Returns:
        User context if JWT present and valid, None otherwise
    """
    if not credentials:
        return None

    try:
        return await get_current_user_context(credentials)
    except HTTPException:
        return None


# ============================================================================
# API KEY AUTHENTICATION (for external integrations)
# ============================================================================

async def verify_api_key(api_key: Optional[str] = Depends(api_key_scheme)) -> bool:
    """
    Verify API key for external integrations.

    Uses timing-safe comparison to prevent timing attacks.

    Returns:
        True if API key is valid

    Raises:
        HTTPException if API key is invalid or missing
    """
    if not settings.cortex_api_key:
        logger.error("API key authentication attempted but CORTEX_API_KEY not configured")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key authentication not configured"
        )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required (X-API-Key header)"
        )

    # Timing-safe comparison (prevents timing attacks)
    if not hmac.compare_digest(api_key, settings.cortex_api_key):
        logger.warning(f"Invalid API key attempt: {api_key[:8]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )

    logger.info("✅ API key authenticated")
    return True


# ============================================================================
# ADMIN AUTHENTICATION
# ============================================================================

async def get_current_admin(
    user_context: Dict[str, str] = Depends(get_current_user_context),
    supabase: Client = Depends(get_supabase)
) -> Dict[str, str]:
    """
    Verify user is an admin.

    Checks if user's email exists in admins table with is_active=true.

    Returns:
        Admin context (user_id, email, role)

    Raises:
        HTTPException if user is not an admin
    """
    user_id = user_context["user_id"]
    email = user_context["email"]

    try:
        # Check if user is admin (query admins table)
        result = supabase.table("admins").select("role, is_active").eq("email", email).single().execute()

        if not result.data or not result.data.get("is_active"):
            logger.warning(f"Non-admin user attempted admin access: {email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )

        admin_role = result.data.get("role", "admin")
        logger.info(f"✅ Admin authenticated: {email} (role: {admin_role})")

        return {
            "user_id": user_id,
            "email": email,
            "role": admin_role
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin verification error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin verification failed"
        )


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def is_super_admin(admin_context: Dict[str, str]) -> bool:
    """
    Check if admin has super_admin role.

    Super admins can:
    - Delete companies
    - View all data across companies
    - Manage other admins
    """
    return admin_context.get("role") == "super_admin"


def sanitize_for_logging(text: str, max_length: int = 50) -> str:
    """
    Sanitize sensitive data for logging (prevent PII leakage).

    Truncates long strings and masks email addresses.

    Example:
        "user@example.com" -> "u***@example.com"
        "very long text..." -> "very long te..."
    """
    if not text:
        return ""

    # Truncate long strings
    if len(text) > max_length:
        text = text[:max_length] + "..."

    # Mask emails (keep first char and domain)
    if "@" in text:
        parts = text.split("@")
        if len(parts) == 2:
            local = parts[0]
            domain = parts[1]
            masked_local = local[0] + "***" if len(local) > 1 else local
            text = f"{masked_local}@{domain}"

    return text
