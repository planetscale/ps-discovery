"""
Cloud Discovery Report Generator

Retained for JSON report structuring. Markdown generation is handled by cli.py.
"""

import logging
from typing import Dict, Any


class CloudReportGenerator:
    """Report helper for cloud discovery results."""

    def __init__(self, results: Dict[str, Any]):
        """Initialize report generator with discovery results."""
        self.results = results
        self.logger = logging.getLogger(__name__)
