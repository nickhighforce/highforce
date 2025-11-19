"""
DEPRECATED: Use app.services.tenant.context instead

This file is maintained for backward compatibility only.
All new code should import from app.services.tenant.context
"""

# Re-export everything from new location for backward compatibility
from app.services.tenant.context import *  # noqa: F401, F403
