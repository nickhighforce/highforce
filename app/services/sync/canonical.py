"""
Canonical ID Generation for Universal Deduplication

Generates canonical IDs that group related documents together:
- Email threads: One ID per conversation (not per message)
- Files: One ID per file (not per version)
- Records: One ID per business entity

This enables automatic deduplication via Supabase UPSERT.
"""
from typing import Optional


def get_canonical_id(source: str, thread_id: Optional[str], fallback_id: str) -> str:
    """
    Generate canonical ID for emails.

    For Gmail/Outlook emails:
        Uses thread_id to group all emails in a conversation
        Format: "{source}:thread:{thread_id}"
        Example: "outlook:thread:AAQkAGM3..." or "gmail:thread:18c3f8a9"

    For other sources or missing thread_id:
        Falls back to message_id (preserves current behavior)

    Args:
        source: Source type ('gmail', 'outlook', 'gdrive', etc.)
        thread_id: Thread/conversation ID from email provider
        fallback_id: Message ID to use if thread_id missing

    Returns:
        Canonical ID string

    Examples:
        >>> get_canonical_id('outlook', 'AAQk123', 'msg_456')
        'outlook:thread:AAQk123'

        >>> get_canonical_id('outlook', '', 'msg_456')
        'msg_456'  # Fallback to message_id

        >>> get_canonical_id('gmail', '18c3f8a9', 'msg_789')
        'gmail:thread:18c3f8a9'
    """
    # Email sources: Use thread_id for deduplication
    if source in ['gmail', 'outlook']:
        if thread_id and thread_id.strip():
            return f"{source}:thread:{thread_id}"

    # Fallback: Use original ID (message_id for emails, file_id for files, etc.)
    # This preserves current behavior for edge cases
    return fallback_id
