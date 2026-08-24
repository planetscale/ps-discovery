"""
Base analyzer class for consistent interface across all discovery modules.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from datetime import datetime, timezone


class BaseAnalyzer(ABC):
    """Base class for all discovery analyzers."""

    def __init__(self, config: Dict[str, Any], logger: logging.Logger = None):
        """Initialize the analyzer with configuration."""
        self.config = config
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self.errors = []
        self.warnings = []

    @abstractmethod
    def analyze(self) -> Dict[str, Any]:
        """Perform the analysis and return results."""

    def add_error(self, message: str, exception: Exception = None):
        """Add an error message, keeping the underlying exception detail.

        The exception text is what makes an error actionable ("Unknown column
        'generation_expression'"), so it is recorded on the entry and appended
        to the log line unless the caller already folded it into the message.
        """
        error_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "analyzer": self.__class__.__name__,
        }

        detail = message
        if exception is not None:
            error_entry["exception_type"] = type(exception).__name__
            error_entry["exception"] = str(exception)
            if str(exception) and str(exception) not in message:
                detail = f"{message} [{type(exception).__name__}: {exception}]"

        self.errors.append(error_entry)
        self.logger.error(f"{self.__class__.__name__}: {detail}")

    def add_warning(self, message: str):
        """Add a warning message."""
        warning_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "analyzer": self.__class__.__name__,
        }

        self.warnings.append(warning_entry)
        self.logger.warning(f"{self.__class__.__name__}: {message}")

    def get_analysis_metadata(self) -> Dict[str, Any]:
        """Get metadata about this analysis run."""
        return {
            "analyzer_name": self.__class__.__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": self.errors,
            "warnings": self.warnings,
        }


class CloudAnalyzer(BaseAnalyzer):
    """Base class for cloud provider analyzers."""

    def __init__(
        self, config: Dict[str, Any], provider: str, logger: logging.Logger = None
    ):
        """Initialize the cloud analyzer with provider information."""
        super().__init__(config, logger)
        self.provider = provider
        self.client = None

    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the cloud provider."""

    @abstractmethod
    def discover_resources(self) -> List[str]:
        """Discover available resources to analyze."""

    def get_analysis_metadata(self) -> Dict[str, Any]:
        """Get metadata about this cloud analysis run."""
        metadata = super().get_analysis_metadata()
        metadata["provider"] = self.provider
        return metadata


class DatabaseAnalyzer(BaseAnalyzer):
    """Base class for database analyzers."""

    def __init__(
        self,
        connection,
        config: Dict[str, Any] = None,
        logger: logging.Logger = None,
    ):
        """Initialize the database analyzer with connection."""
        super().__init__(config or {}, logger)
        self.connection = connection

    def execute_query(
        self, query: str, params: tuple = None, raise_on_error: bool = False
    ) -> List[Dict[str, Any]]:
        """Execute a query and return results as list of dictionaries.

        Pass raise_on_error when the caller needs to tell "the server rejected
        this query" apart from "the query returned no rows" -- probing for a
        column or table that only exists on newer server versions, for example.
        """
        try:
            cursor = self.connection.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)

            columns = (
                [desc[0] for desc in cursor.description] if cursor.description else []
            )
            rows = cursor.fetchall()
            cursor.close()

            return [dict(zip(columns, row)) for row in rows]

        except Exception as e:
            if raise_on_error:
                raise
            # Collapse whitespace so the truncated snippet in the error message
            # is readable, and keep the full statement at debug level.
            self.logger.debug(f"Failed query: {query}")
            condensed = " ".join(query.split())
            self.add_error(f"Query execution failed: {condensed[:300]}", e)
            return []

    def execute_query_single(self, query: str, params: tuple = None) -> Dict[str, Any]:
        """Execute a query and return single result."""
        results = self.execute_query(query, params)
        return results[0] if results else {}
