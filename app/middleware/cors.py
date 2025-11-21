"""
CORS Configuration
Cross-Origin Resource Sharing settings for frontend access

SECURITY:
- Multi-tenant: Dynamically loads frontend URL from master Supabase
- Production: Only HTTPS origins
- Development: Localhost + HTTPS
- NO "null" origin (prevents file:// attacks)
"""
import logging
from fastapi.middleware.cors import CORSMiddleware as FastAPICORSMiddleware
from app.core.config import settings
from app.core.config import settings as master_config

logger = logging.getLogger(__name__)


def get_cors_middleware():
    """
    Returns configured CORS middleware with environment-based settings.

    SECURITY:
    - Multi-tenant: Loads frontend_url from company record in master Supabase
    - Production: Strict HTTPS-only origins
    - Dev/Staging: Include localhost for development
    - Never allows "null" origin (file:// protocol attacks)
    """
    # TEMPORARY DEV MODE: Allow all origins for internet testing
    # TODO: Re-enable strict CORS when ready for production
    if settings.environment == "development":
        logger.warning("⚠️  DEV MODE: CORS allowing ALL origins (*) for testing")
        return FastAPICORSMiddleware, {
            "allow_origins": ["*"],  # Allow all origins temporarily
            "allow_credentials": False,  # Must be False when using "*"
            "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["*"],  # Allow all headers
            "expose_headers": ["X-Request-ID"],
            "max_age": 600,
        }

    # PRODUCTION: Strict origin whitelist
    allowed_origins = []

    # Load frontend URL from master Supabase if multi-tenant mode
    if master_config.is_multi_tenant:
        try:
            from app.core.dependencies import master_supabase_client

            if master_supabase_client:
                company_result = master_supabase_client.table("companies")\
                    .select("frontend_url")\
                    .eq("id", master_config.company_id)\
                    .single()\
                    .execute()

                if company_result.data and company_result.data.get("frontend_url"):
                    frontend_url = company_result.data["frontend_url"]
                    allowed_origins.append(frontend_url)
                    logger.info(f"✅ CORS: Loaded frontend URL from master Supabase: {frontend_url}")
                else:
                    logger.warning("⚠️  CORS: No frontend_url found in company record")
        except Exception as e:
            logger.error(f"❌ CORS: Failed to load frontend URL from master Supabase: {e}")

    # ALWAYS add the Vercel frontend (fallback for both prod and dev)
    if "https://connectorfrontend.vercel.app" not in allowed_origins:
        allowed_origins.append("https://connectorfrontend.vercel.app")
        logger.info("✅ CORS: Added Vercel frontend as fallback")

    logger.info(f"🌐 CORS allowed origins: {allowed_origins}")

    return FastAPICORSMiddleware, {
        "allow_origins": allowed_origins,
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Explicit methods
        "allow_headers": [
            "Content-Type",
            "Authorization",
            "X-API-Key",
            "X-Admin-Session",  # Admin dashboard authentication
            "X-Request-ID",
        ],  # Explicit headers (more secure than "*")
        "expose_headers": ["X-Request-ID"],  # Headers frontend can read
        "max_age": 600,  # Cache preflight requests for 10 minutes
    }
