"""
DEPRECATED: Use app.services.jobs instead

This module has been renamed for clarity.
All new code should import from app.services.jobs
"""

# Re-export everything from new location for backward compatibility
from app.services.jobs.broker import broker
from app.services.jobs.tasks import sync_gmail_task, sync_drive_task, sync_outlook_task, sync_quickbooks_task

__all__ = ["broker", "sync_gmail_task", "sync_drive_task", "sync_outlook_task", "sync_quickbooks_task"]

