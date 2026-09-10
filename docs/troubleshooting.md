# Troubleshooting

## Python Installation

**Python 3.10 or higher is required.** If you don't have it, install it for your platform:

<details>
<summary><b>macOS</b></summary>

```bash
# Using Homebrew (recommended)
brew install python@3.12

# Verify installation
python3 --version
```

If you don't have Homebrew:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

</details>

<details>
<summary><b>Ubuntu/Debian</b></summary>

```bash
# Ubuntu 22.04+ and Debian 12+ have Python 3.10+ by default
sudo apt update
sudo apt install python3 python3-pip python3-venv

# Verify installation
python3 --version
```

Ubuntu 20.04 and Debian 11 ship Python 3.9 or older, which is no longer
supported. Install a newer interpreter:
```bash
sudo apt install software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.12 python3.12-venv python3-pip
```

</details>

<details>
<summary><b>Amazon Linux 2023</b></summary>

```bash
# Amazon Linux 2023 ships Python 3.9 as the default python3, which is no
# longer supported. Install Python 3.11 (or newer) explicitly:
sudo dnf install python3.11 python3.11-pip

# Run setup.sh with the newer interpreter, e.g.:
python3.11 -m venv venv  # or ensure python3.11 is first on PATH

# Verify installation
python3.11 --version
```

</details>

<details>
<summary><b>RHEL/Rocky Linux/CentOS</b></summary>

```bash
# RHEL 9 / Rocky Linux 9 ship Python 3.9 as the default python3, which is no
# longer supported. Install Python 3.11 (or newer) explicitly:
sudo dnf install python3.11 python3.11-pip

# Verify installation
python3.11 --version
```

</details>

## Common Issues

### Connection Problems

```bash
# Error: could not connect to server
# Solution: Check host, port, and network connectivity
ping your-postgres-host
telnet your-postgres-host 5432
```

### Permission Errors

```bash
# Error: permission denied for relation pg_stat_statements
# Solution: Install pg_stat_statements extension or run with higher privileges
# As superuser in PostgreSQL:
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

### Missing Dependencies

```bash
# Error: No module named 'psycopg2'
# Solution: Install required packages
pip install psycopg2-binary
```

### Workload Capture

`ps-discovery workload init` reports the state of the server and prints the
remediation for what it finds. Run it first; it is read-only. See
[Workload Capture](workload_capture.md#before-you-start) for the full setup.

The cases that are easy to get wrong:

- **`pg_stat_statements` exists but stays empty.** The library is not in
  `shared_preload_libraries`. `CREATE EXTENSION` alone is not enough, and the
  setting needs a server restart. This one wastes a whole capture window, so
  `init` reports it as its own case.
- **Statement text reads `<insufficient privilege>`.** The role is missing
  `pg_monitor`, so PostgreSQL hides other roles' statements. The result would
  describe only the capture role's own statements, so capture refuses.
- **`CREATE EXTENSION pg_stat_statements` is denied.** The extension is not
  *trusted*, so it needs a superuser. On a managed service, use the provider's
  control plane, or capture the relation tier without it.
- **`collect` exits 2.** There is no session at that path. `init` creates the
  session; `collect` never does, because a session with no baseline silently
  captures nothing.
- **Snapshot count stops rising.** The cron entry is the usual cause. cron
  starts in the home directory, so a relative `--session` path or a relative
  config path will not resolve. The line `init` prints includes the required
  `cd`. Run the command by hand to see the error.
- **Exit 5.** The server cannot support collection at all. Read the printed
  remediation, which names the exact state found.

## Managed Database Environments

When running against managed PostgreSQL services (AWS RDS/Aurora, GCP Cloud SQL/AlloyDB, Supabase, Heroku Postgres, Neon, etc.):

- **Expected Warnings**: You may see warnings about missing schema privileges or transaction errors for certain advanced features
- **Graceful Degradation**: The tool is designed to continue analysis even when some features are restricted
- **Core Analysis**: Essential information (configuration, schema structure, performance metrics) will still be captured
- **Error Handling**: Errors are logged but don't prevent the tool from completing its analysis

## Performance Considerations

- **Resource Usage**: The tool only reads metadata from system catalogs and statistics views — it does not access table data and should have minimal impact on database performance
- **Network Latency**: High network latency between the tool and database server may increase analysis time

## Limitations

- **Read-Only Analysis**: The tool only reads database metadata and statistics; it does not access actual table data
- **Point-in-Time**: Analysis reflects the database state at the time of execution
- **Extension Dependencies**: Some analysis features require specific PostgreSQL extensions to be installed
- **Version Compatibility**: Designed for PostgreSQL 9.6 through 16+
