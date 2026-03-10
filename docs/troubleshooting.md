# Troubleshooting

## Python Installation

**Python 3.9 or higher is required.** If you don't have it, install it for your platform:

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

For older versions that need Python 3.9+:
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
# Amazon Linux 2023 includes Python 3.9+ by default
sudo yum install python3 python3-pip

# Verify installation
python3 --version
```

</details>

<details>
<summary><b>RHEL/Rocky Linux/CentOS</b></summary>

```bash
# Install Python 3.9+
sudo dnf install python3 python3-pip

# Verify installation
python3 --version
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

## Managed Database Environments

When running against managed PostgreSQL services (AWS RDS, Google Cloud SQL, etc.):

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
