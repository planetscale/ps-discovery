# Changelog

All notable changes to PlanetScale Discovery Tools will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-03-09

Initial public release of PlanetScale Discovery Tools — a comprehensive suite for analyzing PostgreSQL databases and cloud infrastructure environments to support migration planning.

### Database Discovery

- **Configuration Analysis**: PostgreSQL server version, runtime settings, and tuning parameters
- **Schema Analysis**: Tables, columns, indexes, constraints, views, functions, triggers, sequences, and partitions
- **Performance Analysis**: Connection stats, query performance (via pg_stat_statements), cache hit ratios, index usage, lock analysis, replication lag, and wait events
- **Security Analysis**: Users, roles, permissions, row-level security policies, SSL configuration, and privilege escalation risks
- **Advanced Features Analysis**: Extensions, custom data types, foreign data wrappers, PostGIS, and other PostgreSQL-specific features
- **Data Size Analysis** (opt-in): Large column detection and LOB identification for migration sizing

### Cloud Infrastructure Discovery

- **AWS**: RDS instances, Aurora clusters, VPC topology (subnets, security groups, NACLs, route tables, NAT gateways), and focused single-database analysis mode
- **GCP**: Cloud SQL instances, AlloyDB clusters with storage usage via Cloud Monitoring API, VPC networks, and firewall rules
- **Supabase**: Managed PostgreSQL project inventory, connection pooling configuration, and database details
- **Heroku**: Postgres add-ons, PgBouncer pooling detection, follower/replica databases, cross-app attachments, and plan-based resource specifications

### CLI and Reporting

- Unified `ps-discovery` CLI with `database`, `cloud`, and `both` subcommands
- YAML configuration-driven execution for repeatable discovery runs
- JSON output containing complete structured analysis results
- Optional local markdown summary for quick review (`--local-summary`)
- Focused analysis mode (`--target-database`) for single-database cloud infrastructure scoping
- Configurable analyzer selection (`--analyzers`) to run only the modules you need

### Reliability and Safety

- Metadata-only collection — no actual table data is ever accessed
- Graceful degradation on permission errors — one module failure does not stop the entire discovery
- 5-minute query timeout to prevent runaway queries
- Read-only operation with minimal database performance impact
- Output files restricted to owner-only permissions (0600)
- 372 unit tests covering all analyzers
