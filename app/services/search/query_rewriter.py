"""
DEPRECATED: Use app.services.preprocessing.query_rewriter instead

This file is maintained for backward compatibility only.
All new code should import from app.services.preprocessing
"""

# Re-export everything from new location for backward compatibility
from app.services.preprocessing.query_rewriter import *  # noqa: F401, F403
