"""
Slack Message Connector
Normalizes Slack messages from Nango unified API

NOTE: Slack integration ready but not yet active in production.
TODO: Activate when NANGO_PROVIDER_KEY_SLACK is configured and integrated into sync_engine.py
"""
import logging
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


def normalize_slack_message(raw_message: Dict[str, Any], company_id: str) -> Dict[str, Any]:
    """
    Normalize Slack message from Nango unified API.

    Args:
        raw_message: Raw message from Nango
        company_id: Tenant/user ID

    Returns:
        Normalized message dict
    """
    # Nango unified structure for messages:
    # {
    #   "id": "1234567890.123456",
    #   "type": "message",
    #   "text": "Hey team, check out this document...",
    #   "user": "U01234567",
    #   "user_name": "Sarah Chen",
    #   "channel": "C01234567",
    #   "channel_name": "general",
    #   "timestamp": "2024-01-15T10:30:00Z",
    #   "thread_ts": "1234567890.123456",  // If it's a thread reply
    #   "attachments": [],  // File attachments
    #   ...
    # }

    return {
        "company_id": company_id,
        "source": "slack",
        "message_id": raw_message.get("id") or raw_message.get("ts"),
        "subject": f"Slack message in #{raw_message.get('channel_name', 'unknown')}",
        "full_body": raw_message.get("text", ""),
        "sender_name": raw_message.get("user_name", "Unknown User"),
        "sender_address": raw_message.get("user"),  # Slack user ID
        "to_addresses": [raw_message.get("channel_name", "")],
        "received_datetime": raw_message.get("timestamp"),
        "user_id": raw_message.get("user"),
        "channel": raw_message.get("channel_name"),
        "thread_ts": raw_message.get("thread_ts"),
        "web_link": raw_message.get("permalink", ""),
    }
