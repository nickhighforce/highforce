"""
Sync Schemas
Models for manual sync operations
"""
from typing import Optional, List
from pydantic import BaseModel


class SyncResponse(BaseModel):
    """
    Response for manual sync endpoint.
    Indicates sync completion status.
    """
    status: str  # "success", "partial", "failed"
    company_id: str
    users_synced: Optional[int] = None  # Only for Outlook (multi-user tenants)
    messages_synced: int
    errors: List[str] = []
