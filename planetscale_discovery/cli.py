"""
PlanetScale Discovery Tools - Unified CLI

Main command-line interface for database and cloud infrastructure discovery.
"""

import argparse
import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, List

from .config.config_manager import ConfigManager
from .cloud.discovery import CloudDiscoveryTool
from .common.utils import setup_logging, generate_timestamp
from . import __version__


def create_main_parser() -> argparse.ArgumentParser:
    """Create the main argument parser."""
    parser = argparse.ArgumentParser(
        description=(
            "PlanetScale Discovery Tools - Comprehensive database "
            "and cloud infrastructure discovery"
        ),
        epilog="""
Examples:
  # Database discovery only
  ps-discovery database --host localhost -d mydb -u postgres

  # Cloud discovery only
  ps-discovery cloud --config cloud-config.yaml

  # Both database and cloud discovery
  ps-discovery both --config full-config.yaml

  # Generate configuration template
  ps-discovery config-template --output discovery-config.yaml
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Create subparsers
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Database discovery subcommand
    db_parser = subparsers.add_parser("database", help="PostgreSQL database discovery")
    add_common_args(db_parser)
    add_database_args(db_parser)

    # Cloud discovery subcommand
    cloud_parser = subparsers.add_parser("cloud", help="Cloud infrastructure discovery")
    add_common_args(cloud_parser)
    add_cloud_args(cloud_parser)

    # Combined discovery subcommand
    both_parser = subparsers.add_parser(
        "both", help="Combined database and cloud discovery"
    )
    add_common_args(both_parser)
    add_database_args(both_parser)
    add_cloud_args(both_parser)

    # Configuration template subcommand
    config_parser = subparsers.add_parser(
        "config-template", help="Generate configuration template"
    )
    config_parser.add_argument(
        "--output", required=True, help="Output file for configuration template"
    )
    config_parser.add_argument(
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="Configuration format",
    )
    config_parser.add_argument(
        "--providers",
        help="Comma-separated list of cloud providers to include (aws,gcp,supabase,heroku). If not specified, only database and output sections are generated.",
        default=None,
    )

    return parser


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add common arguments to a subcommand parser."""
    common_group = parser.add_argument_group("Common Options")

    common_group.add_argument(
        "--config", "-c", help="Configuration file (YAML or JSON)", type=str
    )

    common_group.add_argument(
        "--output-dir",
        "-o",
        help="Output directory for reports",
        default="./discovery_output",
        type=str,
    )

    common_group.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level",
    )

    common_group.add_argument("--log-file", help="Log file path", type=str)

    common_group.add_argument(
        "--local-summary",
        action="store_true",
        default=False,
        help="Generate a local markdown summary for debugging. This file stays on your machine and is not sent anywhere.",
    )


def add_database_args(parser: argparse.ArgumentParser) -> None:
    """Add database-specific arguments."""
    db_group = parser.add_argument_group("Database Discovery Options")

    db_group.add_argument("--host", help="PostgreSQL server host")

    db_group.add_argument("--port", "-p", help="PostgreSQL server port", type=int)

    db_group.add_argument(
        "--database", "-d", help="PostgreSQL database name", required=False
    )

    db_group.add_argument("--username", "-u", help="PostgreSQL username")

    db_group.add_argument(
        "--password", "-W", action="store_true", help="Prompt for password"
    )

    db_group.add_argument(
        "--analyzers",
        help=(
            "Comma-separated list of database analyzers to run "
            "(config,schema,performance,security,features)"
        ),
        default="config,schema,performance,security,features",
    )


def add_cloud_args(parser: argparse.ArgumentParser) -> None:
    """Add cloud-specific arguments."""
    cloud_group = parser.add_argument_group("Cloud Discovery Options")

    cloud_group.add_argument(
        "--providers",
        help=(
            "Comma-separated list of cloud providers (aws,gcp,supabase,heroku). "
            "If not specified, uses config file settings."
        ),
        default=None,
    )

    cloud_group.add_argument(
        "--regions", help="Comma-separated list of regions to analyze"
    )

    cloud_group.add_argument(
        "--aws-profile", help="AWS profile to use for authentication"
    )

    cloud_group.add_argument("--gcp-project", help="GCP project ID")

    cloud_group.add_argument("--gcp-key", help="Path to GCP service account key file")

    cloud_group.add_argument(
        "--target-database",
        help="Target specific database identifier for focused analysis",
    )

    cloud_group.add_argument(
        "--heroku-api-key",
        help="Heroku API key for authentication",
    )

    cloud_group.add_argument(
        "--heroku-target-app",
        help="Target specific Heroku app for focused analysis",
    )


VALID_DATABASE_ANALYZERS = [
    "config",
    "schema",
    "performance",
    "security",
    "features",
    "data_size",
]


def run_database_discovery(config: Any, args: argparse.Namespace) -> Dict[str, Any]:
    """Run database discovery."""
    from .database.discovery import DatabaseDiscoveryTool

    # Override analyzer modules from CLI --analyzers flag
    if args.analyzers:
        requested = args.analyzers.split(",")
        invalid = [a for a in requested if a not in VALID_DATABASE_ANALYZERS]
        if invalid:
            valid_list = ", ".join(VALID_DATABASE_ANALYZERS)
            print(
                f"Error: Invalid analyzer(s): {', '.join(invalid)}\n"
                f"Valid analyzers are: {valid_list}",
                file=sys.stderr,
            )
            sys.exit(1)
        config.database_analyzers = requested

    # Validate required database parameters
    if not config.database.database:
        raise ValueError("Database name is required for database discovery")

    if not config.database.username:
        raise ValueError("Username is required for database discovery")

    # Run database discovery
    db_tool = DatabaseDiscoveryTool(config)
    return db_tool.discover()


def run_cloud_discovery(config: Any, args: argparse.Namespace) -> Dict[str, Any]:
    """Run cloud discovery."""
    # Override config with command line arguments
    if args.providers:
        providers = args.providers.split(",")
        config.aws.enabled = "aws" in providers
        config.gcp.enabled = "gcp" in providers
        config.supabase.enabled = "supabase" in providers
        config.heroku.enabled = "heroku" in providers

    if args.regions:
        regions = args.regions.split(",")
        if config.aws.enabled:
            config.aws.regions = regions
        if config.gcp.enabled:
            config.gcp.regions = regions

    if args.aws_profile:
        config.aws.profile = args.aws_profile

    if args.gcp_project:
        config.gcp.project_id = args.gcp_project

    if args.gcp_key:
        config.gcp.service_account_key = args.gcp_key

    # Set target database for focused analysis
    if args.target_database:
        config.target_database = args.target_database

    # Heroku-specific overrides
    if hasattr(args, "heroku_api_key") and args.heroku_api_key:
        config.heroku.api_key = args.heroku_api_key

    if hasattr(args, "heroku_target_app") and args.heroku_target_app:
        config.heroku.target_app = args.heroku_target_app

    # Run cloud discovery
    cloud_tool = CloudDiscoveryTool(config)
    return cloud_tool.discover()


def generate_summary_markdown(
    combined_results: Dict[str, Any], output_path: Path
) -> None:
    """Generate a single neutral summary markdown from combined results."""
    lines: List[str] = []

    # Header
    lines.extend(
        [
            "# Discovery Summary",
            "",
            f"**Generated:** {combined_results.get('timestamp', 'Unknown')}",
            f"**Tool Version:** {combined_results.get('discovery_version', 'Unknown')}",
            "",
            "---",
            "",
        ]
    )

    # Overview table - which modules ran
    lines.extend(["## Overview", ""])
    lines.extend(
        [
            "| Module | Status |",
            "|--------|--------|",
        ]
    )
    if combined_results.get("database_results"):
        lines.append("| Database | Completed |")
    if combined_results.get("cloud_results"):
        lines.append("| Cloud | Completed |")
    lines.append("")

    # Database section
    db_results = combined_results.get("database_results")
    if db_results:
        lines.extend(["## Database", ""])
        analysis = db_results.get("analysis_results", {})
        conn_info = db_results.get("connection_info", {})

        # PG version
        config_results = analysis.get("config", {})
        if "version_info" in config_results:
            version_info = config_results["version_info"]
            major = version_info.get("major_version", "Unknown")
            lines.append(f"- **PostgreSQL Version:** {major}")

        # Database name
        if conn_info.get("current_database"):
            lines.append(f"- **Database:** {conn_info['current_database']}")

        # Database count
        schema_results = analysis.get("schema", {})
        if "database_catalog" in schema_results:
            db_count = len(schema_results["database_catalog"])
            lines.append(f"- **Databases:** {db_count}")

        # Table count
        if "table_analysis" in schema_results:
            table_count = len(schema_results["table_analysis"])
            total_size = sum(
                t.get("total_size_bytes", 0) for t in schema_results["table_analysis"]
            )
            lines.append(f"- **Tables:** {table_count}")
            if total_size > 0:
                lines.append(f"- **Total Size:** {_format_bytes(total_size)}")

        # Extensions
        features_results = analysis.get("features", {})
        if "extensions_analysis" in features_results:
            extensions = features_results["extensions_analysis"].get(
                "installed_extensions", []
            )
            if extensions:
                ext_names = [e.get("extname", "") for e in extensions]
                lines.append(
                    f"- **Extensions ({len(extensions)}):** {', '.join(ext_names)}"
                )

        # Users/roles
        security_results = analysis.get("security", {})
        if "user_role_analysis" in security_results:
            user_analysis = security_results["user_role_analysis"]
            if "summary" in user_analysis:
                summary = user_analysis["summary"]
                user_count = summary.get("user_count", 0)
                role_count = summary.get("role_count", 0)
                lines.append(f"- **Users:** {user_count}, **Roles:** {role_count}")

        # Connection stats
        perf_results = analysis.get("performance", {})
        if "connection_analysis" in perf_results:
            conn = perf_results["connection_analysis"]
            if "connection_summary" in conn:
                conn_summary = conn["connection_summary"]
                lines.append(
                    f"- **Connections:** {conn_summary.get('total_connections', 0)} total, "
                    f"{conn_summary.get('active_connections', 0)} active"
                )

        # Replication status
        if (
            "replication_lag" in perf_results
            and "error" not in perf_results["replication_lag"]
        ):
            repl_info = perf_results["replication_lag"]
            is_standby = repl_info.get("is_standby", False)
            if is_standby:
                lines.append("- **Replication:** Standby (replica)")
            else:
                replica_count = repl_info.get("replication_summary", {}).get(
                    "total_replicas", 0
                )
                if replica_count > 0:
                    lines.append(
                        f"- **Replication:** Primary with {replica_count} replica(s)"
                    )
                else:
                    lines.append("- **Replication:** Primary (no active replicas)")

        lines.append("")

    # Cloud section
    cloud_results = combined_results.get("cloud_results")
    if cloud_results:
        lines.extend(["## Cloud Infrastructure", ""])
        providers = cloud_results.get("providers", {})
        cloud_summary = cloud_results.get("summary", {})

        if cloud_summary.get("providers_discovered"):
            lines.append(
                f"- **Providers:** {', '.join(p.upper() for p in cloud_summary['providers_discovered'])}"
            )
        if cloud_summary.get("total_regions", 0) > 0:
            lines.append(f"- **Regions:** {cloud_summary['total_regions']}")
        if cloud_summary.get("total_databases", 0) > 0:
            lines.append(
                f"- **Database Instances:** {cloud_summary['total_databases']}"
            )
        if cloud_summary.get("total_clusters", 0) > 0:
            lines.append(f"- **Database Clusters:** {cloud_summary['total_clusters']}")
        lines.append("")

        # Per-provider detail
        for provider_name, provider_data in providers.items():
            provider_summary = provider_data.get("summary", {})
            resources = provider_data.get("resources", {})

            lines.append(f"### {provider_name.upper()}")
            lines.append("")

            if provider_name == "aws":
                # Collect all instances and clusters across regions
                all_rds = []
                all_aurora = []
                for region, rdata in resources.items():
                    for inst in rdata.get("rds_instances", []):
                        all_rds.append(inst)
                    for cl in rdata.get("aurora_clusters", []):
                        all_aurora.append(cl)

                if all_rds:
                    lines.extend(
                        [
                            "#### RDS Instances",
                            "",
                            "| Instance | Engine | Version | Class | Storage | Multi-AZ | Status |",
                            "|----------|--------|---------|-------|---------|----------|--------|",
                        ]
                    )
                    for inst in all_rds:
                        lines.append(
                            f"| {inst.get('db_instance_identifier', '')} "
                            f"| {inst.get('engine', '')} "
                            f"| {inst.get('engine_version', '')} "
                            f"| {inst.get('db_instance_class', '')} "
                            f"| {inst.get('allocated_storage', '')}GB {inst.get('storage_type', '')} "
                            f"| {'Yes' if inst.get('multi_az') else 'No'} "
                            f"| {inst.get('status', '')} |"
                        )
                    lines.append("")

                if all_aurora:
                    lines.extend(
                        [
                            "#### Aurora Clusters",
                            "",
                            "| Cluster | Engine | Version | Members | Multi-AZ | Storage | Status |",
                            "|---------|--------|---------|---------|----------|---------|--------|",
                        ]
                    )
                    for cl in all_aurora:
                        member_count = len(cl.get("cluster_members", []))
                        storage = cl.get("allocated_storage", "")
                        storage_str = f"{storage}GB" if storage else "Aurora managed"
                        lines.append(
                            f"| {cl.get('identifier', '')} "
                            f"| {cl.get('engine', '')} "
                            f"| {cl.get('engine_version', '')} "
                            f"| {member_count} "
                            f"| {'Yes' if cl.get('multi_az') else 'No'} "
                            f"| {storage_str} "
                            f"| {cl.get('status', '')} |"
                        )
                    lines.append("")

            elif provider_name == "gcp":
                all_sql = []
                all_alloydb = []
                for region, rdata in resources.items():
                    for inst in rdata.get("cloud_sql_instances", []):
                        all_sql.append(inst)
                    for cl in rdata.get("alloydb_clusters", []):
                        all_alloydb.append(cl)

                if all_sql:
                    lines.extend(
                        [
                            "#### Cloud SQL Instances",
                            "",
                            "| Instance | Database Version | Tier | Region | HA | Status |",
                            "|----------|-----------------|------|--------|----|--------|",
                        ]
                    )
                    for inst in all_sql:
                        settings = inst.get("settings", {})
                        lines.append(
                            f"| {inst.get('name', '')} "
                            f"| {inst.get('database_version', '')} "
                            f"| {settings.get('tier', '')} "
                            f"| {inst.get('region', '')} "
                            f"| {'Yes' if settings.get('availability_type') == 'REGIONAL' else 'No'} "
                            f"| {inst.get('state', '')} |"
                        )
                    lines.append("")

                if all_alloydb:
                    lines.extend(
                        [
                            "#### AlloyDB Clusters",
                            "",
                            "| Cluster | State | Region |",
                            "|---------|-------|--------|",
                        ]
                    )
                    for cl in all_alloydb:
                        lines.append(
                            f"| {cl.get('name', '')} "
                            f"| {cl.get('state', '')} "
                            f"| {cl.get('region', '')} |"
                        )
                    lines.append("")

            elif provider_name == "supabase":
                lines.append(
                    f"- **Projects:** {provider_summary.get('total_projects', 0)}"
                )
                lines.append("")

            elif provider_name == "heroku":
                lines.append(f"- **Apps:** {provider_summary.get('total_apps', 0)}")
                lines.append(
                    f"- **Databases:** {provider_summary.get('total_databases', 0)}"
                )
                plans = provider_summary.get("plans", {})
                if plans:
                    lines.append(
                        f"- **Plans:** {', '.join(f'{k} ({v})' for k, v in plans.items())}"
                    )
                lines.append("")

    # Gaps section
    gaps = []
    if db_results:
        gaps = db_results.get("analysis_gaps", [])

    if gaps:
        lines.extend(["## Information Not Collected", ""])
        for gap in gaps:
            reason = gap.get("error_message", "Unknown reason")
            module = gap.get("module", "unknown")
            description = gap.get("description", "")
            lines.append(f"- **{description}** ({module}): {reason}")
        lines.append("")

    # Errors section
    errors = []
    if cloud_results:
        errors.extend(cloud_results.get("errors", []))

    if errors:
        lines.extend(["## Errors", ""])
        for error in errors:
            lines.append(f"- {error.get('message', 'Unknown error')}")
        lines.append("")

    # Write the file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))


def _format_bytes(bytes_value: int) -> str:
    """Format bytes value to human readable string."""
    if bytes_value == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    size = float(bytes_value)
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.1f} {units[unit_index]}"


def _handle_database_error(e: Exception, logger: Any) -> None:
    """Print user-friendly database connection error messages."""
    error_str = str(e)

    # Check for common psycopg2 connection errors
    if "could not connect to server" in error_str or "Connection refused" in error_str:
        logger.error(
            f"Could not connect to the database server.\n"
            f"  Check that the host and port are correct and the server is running.\n"
            f"  Details: {e}"
        )
    elif "password authentication failed" in error_str:
        logger.error(
            f"Authentication failed.\n"
            f"  Check your username and password.\n"
            f"  Details: {e}"
        )
    elif "does not exist" in error_str and "database" in error_str:
        logger.error(
            f"Database not found.\n"
            f"  Verify the database name is correct.\n"
            f"  Details: {e}"
        )
    elif "timeout" in error_str.lower():
        logger.error(
            f"Connection timed out.\n"
            f"  Check network connectivity and firewall rules.\n"
            f"  Details: {e}"
        )
    else:
        logger.error(f"Database discovery failed: {e}")


def main() -> None:
    """Main entry point for the unified CLI."""
    parser = create_main_parser()
    args = parser.parse_args()

    if not args.command:
        print(
            "Usage: ps-discovery <command> [options]\n"
            "\n"
            "Commands:\n"
            "  database         PostgreSQL database discovery\n"
            "  cloud            Cloud infrastructure discovery\n"
            "  both             Combined database and cloud discovery\n"
            "  config-template  Generate configuration template\n"
            "\n"
            "Run 'ps-discovery <command> --help' for details on a specific command."
        )
        sys.exit(1)

    try:
        # Handle config template generation
        if args.command == "config-template":
            config_manager = ConfigManager()
            providers = args.providers.split(",") if args.providers else []
            config_manager.save_config_template(args.output, providers=providers)
            print(f"Configuration template saved to {args.output}")
            return

        # Load configuration without validation
        config_manager = ConfigManager(args.config)
        config = config_manager.load_config(validate=False)

        # Override global config with CLI args
        if args.output_dir:
            config.output.output_dir = args.output_dir
        config.log_level = args.log_level
        if args.log_file:
            config.log_file = args.log_file

        # Apply database CLI args early for validation
        if hasattr(args, "host") and args.host:
            config.database.host = args.host
        if hasattr(args, "port") and args.port:
            config.database.port = args.port
        if hasattr(args, "database") and args.database:
            config.database.database = args.database
        if hasattr(args, "username") and args.username:
            config.database.username = args.username
        if hasattr(args, "password") and args.password:
            import getpass

            config.database.password = getpass.getpass("PostgreSQL Password: ")

        # Set up logging
        logger = setup_logging(config.log_level, config.log_file)

        # Combined results
        combined_results = {
            "discovery_version": __version__,
            "timestamp": generate_timestamp(),
            "database_results": None,
            "cloud_results": None,
            "summary": {},
        }

        # Set modules based on command
        if args.command == "database":
            config.modules = ["database"]
        elif args.command == "cloud":
            config.modules = ["cloud"]
        elif args.command == "both":
            config.modules = ["database", "cloud"]

        # Apply cloud provider flags before validation so --providers works
        if hasattr(args, "providers") and args.providers:
            providers = args.providers.split(",")
            config.aws.enabled = "aws" in providers
            config.gcp.enabled = "gcp" in providers
            config.supabase.enabled = "supabase" in providers
            config.heroku.enabled = "heroku" in providers

        # Validate configuration after setting modules
        config_manager.validate_config()

        # Run discovery based on command
        if args.command in ["database", "both"]:
            logger.info("Starting database discovery...")
            try:
                db_results = run_database_discovery(config, args)
                combined_results["database_results"] = db_results
                logger.info("Database discovery completed successfully")
            except Exception as e:
                _handle_database_error(e, logger)
                if args.command == "database":
                    sys.exit(1)

        if args.command in ["cloud", "both"]:
            logger.info("Starting cloud discovery...")
            try:
                cloud_results = run_cloud_discovery(config, args)
                combined_results["cloud_results"] = cloud_results
                logger.info("Cloud discovery completed successfully")
            except Exception as e:
                logger.error(f"Cloud discovery failed: {e}")
                if args.command == "cloud":
                    sys.exit(1)

        # Generate combined summary
        summary = generate_combined_summary(combined_results)
        combined_results["summary"] = summary

        # Generate reports
        output_dir = Path(config.output.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save JSON results (always generated)
        json_path = output_dir / "planetscale_discovery_results.json"
        with open(json_path, "w") as f:
            json.dump(combined_results, f, indent=2, default=str)
        os.chmod(json_path, 0o600)
        logger.info(f"JSON report saved to {json_path}")

        # Generate local summary markdown (debugging only)
        if args.local_summary:
            md_path = output_dir / "discovery_summary.md"
            generate_summary_markdown(combined_results, md_path)
            os.chmod(md_path, 0o600)
            logger.info(f"Summary report saved to {md_path}")

        # Print summary
        print_discovery_summary(summary, args.command)

    except KeyboardInterrupt:
        print("\nDiscovery interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def generate_combined_summary(results: Dict[str, Any]) -> Dict[str, Any]:
    """Generate combined discovery summary."""
    summary = {
        "discovery_type": "combined",
        "database_summary": {},
        "cloud_summary": {},
    }

    # Extract database summary
    if results.get("database_results"):
        db_results = results["database_results"]
        if "analysis_results" in db_results:
            # Count analysis gaps
            analysis_gaps = db_results.get("analysis_gaps", [])

            summary["database_summary"] = {
                "tables_analyzed": len(
                    db_results["analysis_results"]
                    .get("schema", {})
                    .get("table_analysis", [])
                ),
                "extensions_found": len(
                    db_results["analysis_results"]
                    .get("features", {})
                    .get("extensions_analysis", {})
                    .get("installed_extensions", [])
                ),
                "users_analyzed": len(
                    db_results["analysis_results"]
                    .get("security", {})
                    .get("user_role_analysis", {})
                    .get("users", [])
                ),
                "analysis_gaps": len(analysis_gaps),
            }

    # Extract cloud summary
    if results.get("cloud_results"):
        cloud_results = results["cloud_results"]
        cloud_summary = cloud_results.get("summary", {})
        summary["cloud_summary"] = {
            "providers_analyzed": len(cloud_summary.get("providers_discovered", [])),
            "total_databases": cloud_summary.get("total_databases", 0),
            "total_clusters": cloud_summary.get("total_clusters", 0),
            "regions_analyzed": cloud_summary.get("total_regions", 0),
        }

    return summary


def print_discovery_summary(summary: Dict[str, Any], command: str) -> None:
    """Print discovery summary to console."""
    print("\n" + "=" * 60)
    print("DISCOVERY SUMMARY")
    print("=" * 60)

    if command in ["database", "both"] and summary.get("database_summary"):
        db_summary = summary["database_summary"]
        print("\nDATABASE ANALYSIS:")
        print(f"   Tables Analyzed: {db_summary.get('tables_analyzed', 0)}")
        print(f"   Extensions Found: {db_summary.get('extensions_found', 0)}")
        print(f"   Users Analyzed: {db_summary.get('users_analyzed', 0)}")

        # Show analysis gaps if any exist
        gap_count = db_summary.get("analysis_gaps", 0)
        if gap_count > 0:
            print(f"\n   Information Gaps: {gap_count}")
            print("   See reports for details")

    if command in ["cloud", "both"] and summary.get("cloud_summary"):
        cloud_summary = summary["cloud_summary"]
        print("\nCLOUD ANALYSIS:")
        print(f"   Providers Analyzed: {cloud_summary.get('providers_analyzed', 0)}")
        print(f"   Database Instances: {cloud_summary.get('total_databases', 0)}")
        print(f"   Database Clusters: {cloud_summary.get('total_clusters', 0)}")
        print(f"   Regions Analyzed: {cloud_summary.get('regions_analyzed', 0)}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
