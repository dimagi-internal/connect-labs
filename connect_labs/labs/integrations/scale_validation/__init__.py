"""
Scale Image Validation Integration.

Provides ML-based validation of weight readings against scale images.
Used for KMC (Kangaroo Mother Care) weight verification.
"""

from connect_labs.labs.integrations.scale_validation.api_client import ScaleValidationClient, ScaleValidationError

__all__ = ["ScaleValidationClient", "ScaleValidationError"]
