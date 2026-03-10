"""
Common utility functions for discovery tools.
"""

import logging
import json
import yaml
from pathlib import Path
from typing import Dict, Any, Union
from datetime import datetime, timezone


def setup_logging(level: str = "INFO", log_file: str = None) -> logging.Logger:
    """Set up logging configuration."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Create logger
    logger = logging.getLogger("planetscale_discovery")
    logger.setLevel(log_level)

    # Prevent propagation to root logger to avoid duplicates
    logger.propagate = False

    # Clear existing handlers to prevent duplicates
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def load_config_file(config_path: Union[str, Path]) -> Dict[str, Any]:
    """Load configuration from YAML or JSON file."""
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r") as f:
        if config_path.suffix.lower() in [".yaml", ".yml"]:
            return yaml.safe_load(f)
        elif config_path.suffix.lower() == ".json":
            return json.load(f)
        else:
            raise ValueError(
                f"Unsupported configuration file format: {config_path.suffix}"
            )


def save_results(
    results: Dict[str, Any], output_path: Union[str, Path], format: str = "json"
) -> None:
    """Save analysis results to file."""
    output_path = Path(output_path)

    # Ensure directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        if format.lower() == "json":
            json.dump(results, f, indent=2, default=str)
        elif format.lower() in ["yaml", "yml"]:
            yaml.safe_dump(results, f, default_flow_style=False)
        else:
            raise ValueError(f"Unsupported output format: {format}")


def generate_timestamp() -> str:
    """Generate ISO timestamp for consistency."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
