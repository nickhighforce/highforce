"""
Data Source Providers
Normalization layer for external APIs (Gmail, Outlook, Drive, QuickBooks, Slack)
"""
from app.services.sync.providers.gmail import normalize_gmail_message
from app.services.sync.providers.microsoft_graph import (
    list_all_users,
    sync_user_mailbox,
    normalize_message
)

__all__ = [
    "normalize_gmail_message",
    "list_all_users",
    "sync_user_mailbox",
    "normalize_message",
]
