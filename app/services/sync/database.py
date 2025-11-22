"""
Database helper functions for email sync connector
Handles connections, cursors, and email persistence
"""
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg
from app.core.config import settings

logger = logging.getLogger(__name__)


# ============================================================================
# DATABASE CONNECTION
# ============================================================================

def get_db_connection():
    """Get synchronous database connection.

    Note: Creates a new connection each time. For high-frequency calls,
    consider refactoring to use Supabase client instead.
    """
    return psycopg.connect(settings.database_url, autocommit=False)


# ============================================================================
# CONNECTION MANAGEMENT
# ============================================================================

async def save_connection(
    company_id: str,
    provider_key: str,
    connection_id: str,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None
):
    """
    Save or update connection in database.

    Args:
        company_id: Company ID (for multi-tenant isolation)
        provider_key: Provider name (gmail, outlook, etc.)
        connection_id: Nango connection ID
        user_id: User ID from Master Supabase (who created this connection)
        user_email: User email (for display/logging)

    SECURITY: user_id enables per-user connections and proper attribution.
    Multiple users in same company can each have their own OAuth connections.
    """
    logger.info(f"[SAVE_CONNECTION] Starting - company_id={company_id}, provider={provider_key}, connection_id={connection_id}, user_id={user_id}, user_email={user_email}")

    # PRODUCTION FIX: Use Supabase client instead of psycopg (DATABASE_URL not set in Render)
    try:
        from app.core.dependencies import get_supabase

        logger.debug(f"[SAVE_CONNECTION] Using Supabase client for upsert...")
        supabase = get_supabase()

        # Build upsert payload - user_id and user_email are REQUIRED by schema!
        if not user_id or not user_email:
            raise ValueError(f"user_id and user_email are required! Got user_id={user_id}, user_email={user_email}")

        payload = {
            "company_id": company_id,
            "provider_key": provider_key,
            "connection_id": connection_id,
            "user_id": user_id,
            "user_email": user_email
        }

        logger.debug(f"[SAVE_CONNECTION] Payload: {payload}")

        # Upsert to Supabase (ON CONFLICT handled by Supabase RPC or manual check)
        result = supabase.table("connections").upsert(
            payload,
            on_conflict="company_id,provider_key"
        ).execute()

        logger.debug(f"[SAVE_CONNECTION] Supabase response: {result}")
        logger.info(f"✅ [SAVE_CONNECTION] SUCCESS - Saved connection for tenant {company_id} (company), user {user_id}, provider {provider_key}")

    except Exception as e:
        logger.error(f"❌ [SAVE_CONNECTION] ERROR - Failed to save connection")
        logger.error(f"   company_id: {company_id}")
        logger.error(f"   provider_key: {provider_key}")
        logger.error(f"   connection_id: {connection_id}")
        logger.error(f"   user_id: {user_id}")
        logger.error(f"   user_email: {user_email}")
        logger.error(f"   Error type: {type(e).__name__}")
        logger.error(f"   Error message: {str(e)}")
        logger.exception(f"   Full traceback:")
        raise


async def get_connection(
    company_id: str,
    provider_key: str,
    user_id: Optional[str] = None
) -> Optional[str]:
    """
    Get connection_id for a tenant/user using Supabase client.

    Args:
        company_id: Company ID
        provider_key: Provider name (gmail, outlook, etc.)
        user_id: Optional user ID for per-user connections. If not provided, returns first connection found.

    Returns:
        Nango connection_id if found, None otherwise

    Note: After migration to user-tracked connections, user_id should be required.
    Currently optional for backward compatibility during transition.
    """
    from app.core.dependencies import supabase_client

    if not supabase_client:
        logger.warning("Supabase client not initialized, falling back to direct psycopg connection")
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute(
                        "SELECT connection_id FROM connections WHERE company_id = %s AND provider_key = %s AND user_id = %s",
                        (company_id, provider_key, user_id)
                    )
                else:
                    cur.execute(
                        "SELECT connection_id FROM connections WHERE company_id = %s AND provider_key = %s",
                        (company_id, provider_key)
                    )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()

    # Build query
    query = supabase_client.table("connections").select("connection_id").eq("company_id", company_id).eq("provider_key", provider_key)

    if user_id:
        query = query.eq("user_id", user_id)

    result = query.limit(1).execute()

    if result.data and len(result.data) > 0:
        return result.data[0]["connection_id"]
    return None


# ============================================================================
# OUTLOOK CURSOR MANAGEMENT
# ============================================================================

async def get_user_cursor(company_id: str, provider_key: str, user_id: str) -> Optional[str]:
    """Get delta link for a user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT delta_link FROM user_cursors WHERE company_id = %s AND provider_key = %s AND user_id = %s",
                (company_id, provider_key, user_id)
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


async def save_user_cursor(
    company_id: str,
    provider_key: str,
    user_id: str,
    user_principal_name: str,
    delta_link: str
):
    """Save or update delta link for a user."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO user_cursors (company_id, provider_key, user_id, user_principal_name, delta_link, last_synced_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (company_id, provider_key, user_id)
                DO UPDATE SET
                    delta_link = EXCLUDED.delta_link,
                    last_synced_at = EXCLUDED.last_synced_at,
                    user_principal_name = EXCLUDED.user_principal_name
                """,
                (company_id, provider_key, user_id, user_principal_name, delta_link, datetime.now(timezone.utc))
            )
        conn.commit()
        logger.info(f"Saved cursor for user {user_id}")
    finally:
        conn.close()


# ============================================================================
# GMAIL CURSOR MANAGEMENT
# ============================================================================

async def get_gmail_cursor(company_id: str, provider_key: str) -> Optional[str]:
    """Get Nango cursor for Gmail sync."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cursor FROM gmail_cursors WHERE company_id = %s AND provider_key = %s",
                (company_id, provider_key)
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


async def set_gmail_cursor(company_id: str, provider_key: str, cursor: str):
    """Save or update Nango cursor for Gmail sync."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO gmail_cursors (company_id, provider_key, cursor, last_synced_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (company_id, provider_key)
                DO UPDATE SET
                    cursor = EXCLUDED.cursor,
                    last_synced_at = EXCLUDED.last_synced_at
                """,
                (company_id, provider_key, cursor, datetime.now(timezone.utc))
            )
        conn.commit()
        logger.info(f"Saved Gmail cursor for tenant {company_id}")
    finally:
        conn.close()
