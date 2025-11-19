"""
DEPRECATED: Use app.services.jobs.broker instead

This file is maintained for backward compatibility only.
All new code should import from app.services.jobs
"""

# Re-export everything from new location for backward compatibility
from app.services.jobs.broker import *  # noqa: F401, F403
