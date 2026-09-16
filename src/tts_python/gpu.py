"""Canonical shared GPU implementation; re-exported for API compatibility."""
import sys
from speech_common import gpu as _gpu
sys.modules[__name__] = _gpu
