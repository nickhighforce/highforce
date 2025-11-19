"""
Connector Schemas
Models for OAuth and webhook events from Nango
"""
from typing import Any, Dict, Optional
from pydantic import BaseModel


class NangoOAuthCallback(BaseModel):
    """
    Nango OAuth callback payload.
    Sent after user completes OAuth flow.
    """
    tenantId: str
    providerConfigKey: str
    connectionId: str


class NangoWebhook(BaseModel):
    """
    Nango webhook payload for connection events.

    Triggered by:
    - Auth events (OAuth success/failure)
    - Sync events (incremental/full sync completion)
    - Forward events (passthrough API calls)

    See: https://docs.nango.dev/integrate/guides/webhooks
    """
    type: str  # Event type: "auth", "sync", "forward"
    connectionId: str  # Tenant/user ID
    providerConfigKey: str  # Integration key (gmail-connector, outlook-connector)
    environment: str  # "dev" or "prod"
    success: Optional[bool] = None  # For auth events
    model: Optional[str] = None  # For sync events (e.g., "email_messages")
    responseResults: Optional[Dict[str, Any]] = None  # For sync events

    class Config:
        extra = "allow"  # Allow additional fields from Nango
