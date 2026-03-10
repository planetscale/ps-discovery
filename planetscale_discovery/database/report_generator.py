"""
PostgreSQL Discovery Report Generator
Generates JSON reports from discovery analysis results.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any


class ReportGenerator:
    """Generates reports from PostgreSQL discovery analysis results."""

    def __init__(self, analysis_results: Dict[str, Any]):
        self.results = analysis_results
        self.logger = logging.getLogger(__name__)

    def generate_json_report(self, output_file: Path):
        """Generate complete JSON report."""
        try:
            with open(output_file, "w") as f:
                json.dump(self.results, f, indent=2, default=str)
            self.logger.info(f"JSON report written to {output_file}")
        except Exception as e:
            self.logger.error(f"Failed to write JSON report: {e}")
            raise
