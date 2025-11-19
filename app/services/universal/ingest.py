"""
DEPRECATED: Use app.services.preprocessing.normalizer instead

This file is maintained for backward compatibility only.
All new code should import from app.services.preprocessing
"""

# Re-export everything from new location for backward compatibility
from app.services.preprocessing.normalizer import *  # noqa: F401, F403
