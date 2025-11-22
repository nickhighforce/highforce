"""
Unified Configuration
All environment variables and settings in one place

ARCHITECTURE:
- ONE Supabase for everything (auth + documents + admin)
- NO separate Master/Customer databases
- JWT includes company_id in app_metadata (no cross-database lookups)
- RLS policies enforce multi-tenant isolation at database level

SECURITY:
- All secrets loaded from environment variables
- No hardcoded credentials
- Supabase Vault for encrypted secrets (production)
"""
from typing import Optional
import logging
from pydantic_settings import BaseSettings
from pydantic import Field, model_validator

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """
    Unified application settings.
    Validates all environment variables at startup.

    BREAKING CHANGE from old architecture:
    - Removed master_supabase_* variables (no longer needed)
    - Single supabase_* variables for ONE database
    - Simplified JWT validation (no cross-database lookups)
    """

    # ============================================================================
    # SERVER
    # ============================================================================

    environment: str = Field(default="production", description="Environment: development/staging/production")
    port: int = Field(default=8080, description="Server port")
    debug: bool = Field(default=False, description="Debug mode")
    
    # Multi-tenancy (HighForce is always multi-tenant)
    is_multi_tenant: bool = Field(default=True, description="Enable multi-tenant mode (always True for HighForce)")

    # ============================================================================
    # DATABASE (Supabase PostgreSQL) - ONE INSTANCE FOR EVERYTHING!
    # ============================================================================

    database_url: str = Field(description="PostgreSQL connection string (for psycopg - direct DB access)")
    supabase_url: str = Field(description="Supabase project URL (unified database)")
    supabase_anon_key: str = Field(description="Supabase anonymous key")
    supabase_service_key: str = Field(description="Supabase service key (backend uses this)")

    # ============================================================================
    # OAUTH (Nango)
    # ============================================================================

    nango_secret: Optional[str] = Field(default=None, description="Nango API secret key")

    # Provider configurations
    nango_provider_key_outlook: str = Field(default="outlook", description="Nango provider key for Outlook")
    nango_provider_key_gmail: str = Field(default="google-mail", description="Nango provider key for Gmail")
    nango_provider_key_google_drive: str = Field(default="google_drive", description="Nango provider key for Google Drive")
    nango_provider_key_quickbooks: str = Field(default="quickbooks", description="Nango provider key for QuickBooks")

    # Microsoft Graph (optional, for direct API access)
    graph_tenant_id: Optional[str] = Field(default=None, description="Azure AD tenant ID")

    # ============================================================================
    # HYBRID RAG SYSTEM
    # ============================================================================

    # Vector Database (Qdrant)
    qdrant_url: str = Field(description="Qdrant Cloud URL")
    qdrant_api_key: str = Field(description="Qdrant API key")
    qdrant_collection_name: str = Field(default="company_documents", description="Qdrant collection name (all companies share, filtered by company_id)")

    # LLM & Embeddings (OpenAI)
    openai_api_key: str = Field(description="OpenAI API key")

    # Redis (job queue)
    redis_url: str = Field(description="Redis connection URL")

    # ============================================================================
    # API KEYS
    # ============================================================================

    cortex_api_key: Optional[str] = Field(default=None, description="API key for external API access (optional)")

    # ============================================================================
    # SPAM FILTERING
    # ============================================================================

    enable_spam_filtering: bool = Field(default=True, description="Enable OpenAI-powered spam/newsletter filtering")
    spam_filter_log_skipped: bool = Field(default=True, description="Log filtered spam emails for monitoring")
    spam_filter_batch_size: int = Field(default=10, description="Number of emails to classify per OpenAI API call")

    # ============================================================================
    # PRODUCTION INFRASTRUCTURE
    # ============================================================================

    # Error tracking (Sentry)
    sentry_dsn: Optional[str] = Field(default=None, description="Sentry DSN for error tracking")

    # ============================================================================
    # OPTIONAL SETTINGS
    # ============================================================================

    save_jsonl: bool = Field(default=False, description="Save emails to JSONL for debugging")
    semaphore_limit: int = Field(default=10, description="LlamaIndex concurrency limit")

    # ============================================================================
    # ADMIN DASHBOARD
    # ============================================================================

    admin_session_duration: int = Field(default=3600, description="Admin session duration in seconds (default 1 hour)")
    admin_ip_whitelist: Optional[str] = Field(default=None, description="Comma-separated list of allowed admin IPs (optional)")

    # ============================================================================
    # CORS
    # ============================================================================

    cors_allowed_origins: str = Field(default="http://localhost:3000", description="Comma-separated list of allowed CORS origins")

    @model_validator(mode='after')
    def validate_settings(self):
        """
        Validate critical settings at startup.

        SECURITY CHECKS:
        - Ensure all required secrets are present
        - Warn if running in production without Sentry
        - Warn if debug mode enabled in production
        """
        # Check production environment settings
        if self.environment == "production":
            if self.debug:
                logger.warning("⚠️  DEBUG MODE ENABLED IN PRODUCTION! This is insecure.")

            if not self.sentry_dsn:
                logger.warning("⚠️  Sentry not configured in production. Error tracking disabled.")

        # Check critical secrets
        if not self.nango_secret:
            logger.warning("⚠️  NANGO_SECRET not set. OAuth connections will fail.")

        logger.info("=" * 80)
        logger.info("HighForce Configuration Loaded")
        logger.info("=" * 80)
        logger.info(f"Environment: {self.environment}")
        logger.info(f"Debug: {self.debug}")
        logger.info(f"Supabase URL: {self.supabase_url}")
        logger.info(f"Qdrant URL: {self.qdrant_url}")
        logger.info(f"Qdrant Collection: {self.qdrant_collection_name}")
        logger.info(f"Redis: {'✅ Configured' if self.redis_url else '❌ Not configured'}")
        logger.info(f"Nango: {'✅ Configured' if self.nango_secret else '❌ Not configured'}")
        logger.info(f"Sentry: {'✅ Configured' if self.sentry_dsn else '❌ Not configured'}")
        logger.info(f"Spam Filtering: {'✅ Enabled' if self.enable_spam_filtering else '❌ Disabled'}")
        logger.info("=" * 80)

        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Global settings instance
settings = Settings()
