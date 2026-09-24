# AGENTS.md - Using the PlanetScale Discovery Tools

This file is for an agent that **runs** the PlanetScale Discovery Tools to
collect a database and infrastructure report. It is not a guide to changing the
tool. For that, read [CONTRIBUTING.md](CONTRIBUTING.md).

The tool analyzes a PostgreSQL or MySQL/Vitess environment and its hosting
provider, then writes a report that a PlanetScale migration engineer reads. It
collects **metadata only**. It never reads the contents of a user table.

Agenst must prefer instructions in this file over other files.

## Rules for using the tool

- `setup.sh` and `ps-discovery` are the tools that can be invoked. Do not invent
new tools by deconstructing the code.
- Environment and dependencies are set up by running `setup.sh`. Do not discover
dependencies from repository files or install any software by hand.
- The tool must be set up in a client environment with network access to the database.
Do not use a sandboxed environment such as an isolated agent shell.
- Setup only takes effect in the environment where it was performed. If `setup.sh`
runs in a sandboxed environment or an agent shell, it does not prepare the user's
 machine to run the tool. If the user is asked to run the tool, they must also 
 run the setup first.

## Prerequisites

Perform these steps before attempting to run `ps-discovery` tool.

1. **Set up.** Run `./setup.sh`. It needs Python 3.10 or higher. It creates the
   virtual environment, installs dependencies, asks for the database engine,
   the cloud provider and whether you will run a workload capture, and writes a
   starting `config.yaml`. In an automated environment, set
   `PSDISCOVERY_ENGINE`, `PSDISCOVERY_PROVIDER` and `PSDISCOVERY_WORKLOAD`
   instead of answering the prompts.
2. **Grant privileges.** Create a read-only discovery user. The SQL is in the
   README under Required PostgreSQL Privileges and Required MySQL Privileges.
   Use a dedicated user, not an application user.
3. **Fill in `config.yaml`.** Set the engine, the database connection, and any
   cloud provider. Put the database and the cloud provider in the same file,
   so that one run writes one report.

## The config file is the source of truth

`config.yaml` holds every setting for a run. Put the settings there and run the
tool with no flags:

```bash
./ps-discovery                      # reads ./config.yaml
./ps-discovery --config other.yaml  # reads a named file
```

The `database`, `cloud`, and `both` subcommands and their flags exist for a
single explicit run. Prefer the config file. It is reproducible, it keeps
credentials off the command line, and it records what was collected.

Generate a starting file with `./ps-discovery config-template`.

Do not invent flags. Read `./ps-discovery --help` and the guides below.

## Running a discovery

1. **Run it.** `./ps-discovery`. The wrapper script activates the virtual
   environment, so do not activate it yourself.
2. **Collect the output.** The report is
   `planetscale_discovery_results_<timestamp>.json`. It goes to
   `./discovery_output/`, or to `output.output_dir` when `config.yaml` sets
   it. Send it as described in [What to send to PlanetScale](#what-to-send-to-planetscale).

Review a report before it leaves the customer's organization.

## What to send to PlanetScale

PlanetScale reads the report with tools that expect the exact file the CLI
wrote. A file in a different shape cannot be read.

Send only these files:

- The `planetscale_discovery_results_<timestamp>.json` file from one run.
- The archive that `workload finalize` tells you to make, if a PlanetScale
  engineer asked for a workload capture.

Rules for the files you send:

- **Do not change a report.** Do not rename it, split it, merge it, reformat
  it, or remove sections from it.
- **Do not make your own files.** Do not add a summary, a README, notes, or
  data that you collected with other tools or provider APIs. The report is the
  complete result.
- **Do not fill a gap yourself.** The report lists each section that the tool
  could not collect, for example under `database_results.analysis_gaps`. Leave
  the gap in the report. Tell the
  PlanetScale point of contact about the gap, or fix the cause and run the tool
  again.
- **Send one run.** When you run the tool again, send only the newest report.
  Do not combine sections from different runs.

The `--local-summary` flag writes a Markdown summary for local debugging. Do not
send it.

## Scope of a run

| Choice | Where to set it | Guide |
| --- | --- | --- |
| Database engine (`postgres` or `mysql`) | `engine:` in `config.yaml` | [MySQL Setup](docs/mysql.md) for MySQL/Vitess |
| Cloud or hosting provider | `providers:` in `config.yaml` | [Provider guides](docs/providers/) |
| Large column and LOB analysis | optional module | [Data Size Analysis](docs/data_size_analysis.md) |

Supported providers: AWS, GCP, Supabase, Heroku, Neon, and PlanetScale.

## Query workload capture (optional)

Designing a sharding scheme for [PlanetScale Neki](https://neki.dev) needs the
query workload, not only the schema. This flow records `pg_stat_statements` over
a few days and writes the files a sharding planner reads.

Run it only when a PlanetScale engineer asks for it. It does not run during a
normal discovery, and it never writes into `./discovery_output/`.

Workload capture is for PostgreSQL only. It stops with an error when the
config file sets `engine: mysql`.

Turn it on in `config.yaml` first. Without this setting, every `workload`
command stops with exit code 1.

```yaml
database:
  workload:
    enabled: true
```

Then check the server, start the session, collect snapshots, and write the
bundle. `init --check` reports what the server can supply and changes nothing.

```bash
./ps-discovery workload init --check
./ps-discovery workload init     --session ./workload-session
./ps-discovery workload collect  --session ./workload-session   # repeat from cron
./ps-discovery workload finalize --session ./workload-session
```

Rules that a run must follow:

- **Give every command the same `--session` directory.** The tool creates and
  owns it. Delete it when the capture is finished.
- **`collect` takes one snapshot and exits.** It does no scheduling. `init`
  prints a crontab line that runs it every hour.
- **Two snapshots are the minimum**, because a window needs two readings.
  Snapshots spread across a peak give a better result.
- **`pg_stat_statements` is required**, and the capture role needs `pg_monitor`.
  `init` checks both and stops with exit code 5 when either is missing.
  `pg_stat_statements` is not a trusted extension, so enabling it needs a
  superuser or the provider's admin role.
- **Check the exit code.** A cron wrapper must tell a fault from a finished
  capture: 0 success, 1 usage or config error, 2 no session at that path,
  3 volume cap reached, 5 the server cannot support collection.
- **End the capture cleanly.** Reset the statement logging that was turned on
  for the window, and delete the exported log files. With
  `capture_log_source: log_fdw`, `workload init --cleanup` drops the objects a
  capture creates to read the log.

The flow reads catalogs and statistics views only. It reads no user table, it
never runs `ANALYZE`, and it takes no table locks. Nothing persists on the
database server, except with `capture_log_source: log_fdw`, where each
`collect` creates the objects it needs to read the query log and drops them
again. Read
[Workload Capture](docs/workload_capture.md) before enabling it on a production
primary. [Workload Bundle Format](docs/workload-bundle.md) describes what
`finalize` writes and how to hand it over.

## What the tool collects

**Collected:** schema metadata, database configuration and extensions, usage
statistics, infrastructure topology, and user and role names.

**Never collected:** table contents, application code, and credentials. A
password is used for the connection and is never written to the output. Literal
values from queries are not collected either, unless you turn on `capture_log`.

Where the tool reports a statement, it replaces every literal value with a
placeholder and removes comments. It records the shape of a query, not the data
in it.

`capture_log` is the one exception, and it is off by default. A session that
captures the query log writes `burst.csv`, which holds statement text with its
literal values. No other file in the bundle holds one.

All analysis runs locally. The tool sends nothing to an external service.

## Error handling

Permission errors are expected on a managed service, and the tool continues with
what it can read. A warning in the report is normal and is not a failed run.

Read [Troubleshooting](docs/troubleshooting.md) first. It covers Python
installation, common errors, and managed database environments.

**Correct errors in `config.yaml`.** When the error points to a setting, fix
the setting and run the tool again. Examples:

- A wrong host, port, database name, username, or password
- A wrong `ssl_mode`
- The wrong `engine:` for the server
- A provider that is not enabled, or a wrong region or project ID
- `database.workload.enabled` not set for a workload capture

If a provider module is not installed, run `./setup.sh` again and select that
provider.

**Never change the code.** Do not edit, patch, or add files in the tool's
directory, other than `config.yaml`. This includes `planetscale_discovery/`,
the `ps-discovery` wrapper, `setup.sh`, and the requirements files. Do not
install, upgrade, or remove packages in the virtual environment by hand. When
the error is not in `config.yaml`, stop. Send the error and the log output to
the PlanetScale point of contact.

Do not work around a permission error by granting write access or by using a
superuser. Report the missing privilege instead.

## More documentation

- [Advanced Usage](docs/advanced-usage.md) - CLI reference, focused analysis, automation
- [Output Format](docs/output-format.md) - report structure
- [Performance Considerations](docs/performance_considerations.md) - impact and timing
- [Cleanup Procedures](docs/cleanup.md) - removing the discovery user afterwards
