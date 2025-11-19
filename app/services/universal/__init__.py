"""
DEPRECATED: Use app.services.preprocessing.normalizer instead

This module has been moved to preprocessing.
All new code should import from app.services.preprocessing
"""

# Re-export from new location for backward compatibility
from app.services.preprocessing.normalizer import *  # noqa: F401, F403
