# Query Workload Capture

## Overview

Workload capture records a PostgreSQL query workload over a few days and writes
it as a bundle of files. The PlanetScale migration team uses that bundle to plan
a sharding scheme for a migration to [PlanetScale Neki](https://neki.dev).
Sharding decisions need the query workload, not only the schema.

Capture is opt-in. Nothing here runs during a normal discovery run, and nothing
here writes into the discovery output.

It runs from cron over hours or days rather than once, because a sharding scheme
needs to know how often each query runs, not only that it exists. See
[How a capture works over time](#how-a-capture-works-over-time) for the shape of
a capture and how long to run one.

The tool measures your workload and hands the measurements to the migration
team, whose planning tools work out the sharding scheme from them.

## Before you start

Work through this list once. `workload init` checks every item and tells you
what is missing, so run it early: it is a read-only check and it is safe to run
before you commit to a capture.

### 1. Create a role for the capture

Workload capture needs less access than a discovery run. It reads no user table,
so it needs no `SELECT` on your data:

```sql
CREATE USER planetscale_workload WITH PASSWORD 'secure_password_here';
GRANT CONNECT ON DATABASE your_database TO planetscale_workload;
GRANT pg_monitor TO planetscale_workload;
```

`pg_monitor` is the whole permission requirement. Without it, PostgreSQL hides
the statement text of every other role behind `<insufficient privilege>`, and
the capture would describe only the statements your capture role ran itself.
That result looks complete and is badly wrong, so the tool refuses it rather
than reporting it.

You can reuse the discovery role from the
[Required PostgreSQL Privileges](../README.md#required-postgresql-privileges)
section instead. Add `pg_monitor` to it if it does not have it.

### 2. Enable pg_stat_statements

**This step is required.** `pg_stat_statements` is the only place PostgreSQL
records the query workload, and a sharding scheme is planned from that workload.
Without it there is nothing to capture, so `init` stops rather than producing a
bundle that cannot be planned. Table and index counters say a table is read
often; they never say which column a query filtered on.

The extension needs two things, and the order matters:

1. The library must be listed in the `shared_preload_libraries` server setting.
   Changing that setting needs a **restart**, because PostgreSQL loads the
   library at startup.
2. The extension must then be created in the database:
   `CREATE EXTENSION pg_stat_statements;`

Step 2 alone does nothing. The extension is created, the view appears, and it
stays empty forever. `workload init` reports that state specifically, because it
is the failure that wastes a capture window.

Most managed providers preload the library for you, so step 1 is usually already
done and only `CREATE EXTENSION` is left. Find your provider below.

`pg_stat_statements` is not a *trusted* extension, so `CREATE EXTENSION`
normally requires a superuser or the provider's equivalent admin role. On a
managed service you may not have one. If that blocks you, raise it with your
PlanetScale migration engineer early: capture cannot proceed without it, and the
right people can usually get the extension enabled once they know it is a
prerequisite rather than a preference.

#### Summary

| Where the database runs | Library preloaded? | What you do |
| --- | --- | --- |
| Self-managed | No | Edit `postgresql.conf`, restart, then `CREATE EXTENSION` |
| Amazon RDS and Aurora | Yes, by the default parameter group | `CREATE EXTENSION` as `rds_superuser` |
| Cloud SQL | Normally yes | `CREATE EXTENSION`. Add the database flag only if `init` reports it missing |
| AlloyDB | No | Set the `shared_preload_libraries` flag, restart, then `CREATE EXTENSION` |
| Supabase | Yes, enabled on every project | Nothing. It is already on |
| Heroku Postgres | Yes | `CREATE EXTENSION` |
| Neon | Yes | `CREATE EXTENSION`, and read the warning below |

#### Self-managed PostgreSQL

Add the library to `postgresql.conf`, keeping any entries already there:

    shared_preload_libraries = 'pg_stat_statements'

Restart PostgreSQL, then, as a superuser:

```sql
CREATE EXTENSION pg_stat_statements;
```

#### Amazon RDS and Aurora PostgreSQL

The default parameter group already loads `pg_stat_statements`, so usually you
only need to create the extension. Connect as a member of `rds_superuser`:

```sql
CREATE EXTENSION pg_stat_statements;
```

If `init` reports the library is not loaded, you are on a custom parameter group
that dropped it. Add `pg_stat_statements` to `shared_preload_libraries` in that
group, then **reboot the instance**, because it is a static parameter. For
Aurora, set it on the cluster parameter group.

Check `rds.allowed_extensions` if `CREATE EXTENSION` is refused. That parameter
can restrict which extensions may be installed at all.

#### Cloud SQL for PostgreSQL

`pg_stat_statements` is normally preloaded, so create the extension:

```sql
CREATE EXTENSION pg_stat_statements;
```

If `init` reports the library is not loaded, add it to the
`shared_preload_libraries` database flag and restart the instance. Set the flag
as a comma-separated list, keeping anything already there:

    gcloud sql instances patch INSTANCE_NAME \
      --database-flags shared_preload_libraries=pg_stat_statements

Setting `--database-flags` **replaces the whole list**, so include every flag
the instance already has, or you will silently drop one.

#### AlloyDB for PostgreSQL

AlloyDB does not preload the library by default. Set the
`shared_preload_libraries` flag on the primary instance to include
`pg_stat_statements`, restart the instance, then create the extension:

```sql
CREATE EXTENSION pg_stat_statements;
```

#### Supabase

Nothing to do. Every Supabase project has `pg_stat_statements` enabled by
default; the query performance page in the dashboard is built on it. If it has
been turned off, re-enable it under **Database > Extensions**.

#### Heroku Postgres

The library is preloaded and you cannot change `shared_preload_libraries`.
Create the extension:

```sql
CREATE EXTENSION pg_stat_statements;
```

`heroku pg:outliers` reads the same view, so if that command returns rows, the
extension is working.

#### Neon

Create the extension:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

**Neon needs one extra decision.** When a Neon compute suspends or scales to
zero, the collected statistics are lost. A capture spanning several days on a
compute that idles will keep restarting from nothing, and the result will
describe only the periods between suspensions.

Before capturing on Neon, disable scale-to-zero on the branch you are measuring,
or accept that the window covers only active periods. Capture handles the resets
correctly rather than reporting negative numbers, and `finalize` counts them in
the caveats, so check that count before you quote any figure.

### 3. Check the extension's own settings

These affect what the capture can see. All are optional. `init` reports each one
that will limit the result, so you do not have to check them by hand.

| Setting | Why it matters |
| --- | --- |
| `pg_stat_statements.max` | The number of statements tracked, 5000 by default. When the table is full, PostgreSQL evicts the least-used statements. An evicted statement is one the capture never sees, and nothing records that it existed. Raise it on a busy database |
| `pg_stat_statements.track` | `top`, the default, records the statement the application sent. If your application does its work inside functions or procedures, `top` records only the call, so the statements that touch tables stay invisible. `all` records those too |
| `track_io_timing` | Off by default. While it is off, every block read and write time reads `0`, which means "not measured" rather than "fast" |

Raising `pg_stat_statements.max` needs a restart. The other two are runtime
settings, and both add measurement overhead, so change them with the same care
as any other production setting.

### 4. Decide where the capture runs

Run it against the **primary**. A standby counts only the statements that
executed on it, and the write workload is what decides a shard key. `init`
refuses a standby unless you pass `--allow-replica`.

The host that runs the capture needs cron, the tool installed, and a writable
directory for the session. It does not need to be the database host.

## How a capture works over time

A capture is not one reading. It is a series of readings, and what you hand over
is the difference between them.

`pg_stat_statements` counts forward from the moment it started collecting. A
single reading tells you a statement has run 4 million times, but not whether
that took a week or a year. Two readings, an hour apart, tell you it ran 12,000
times in that hour. The second answer is the one a sharding scheme needs, so the
tool takes many readings and differences each consecutive pair.

    hour 0        hour 1        hour 2        hour 3
    ────┬─────────────┬─────────────┬─────────────┬────────►  time
        │             │             │             │
      init         collect       collect       collect
        │             │             │             │
      snap #1       snap #2       snap #3       snap #4
        │             │             │             │
        └── delta ────┴── delta ────┴── delta ────┘
             1h            1h            1h
                           │
                           ▼
                    finalize sums every interval
                    into one measured window

Three properties follow from this shape, and they are what make the capture safe
to leave running:

- **Every snapshot is a separate file, written once and never modified.** A tick
  that fails, or two ticks that overlap, cost you nothing but that interval.
- **The capture holds no state on the server.** Nothing is reset, nothing is
  written, and stopping is deleting a cron line.
- **You can stop whenever you like.** `finalize` differences whatever snapshots
  exist. More snapshots mean a longer window, not a different format.

### Choosing the capture period

The window you measure is the whole workload the sharding scheme is designed
against. Anything that did not
run during the capture does not exist as far as the sharding scheme is
concerned, so the goal is to cover a representative slice of real life rather
than to run for as long as possible.

Start from what you know about your own traffic:

| If your workload has | Capture for | Because |
| --- | --- | --- |
| A daily peak, and nothing unusual otherwise | 24 hours, hourly | One full daily cycle, peak and trough |
| Nightly batch jobs, reports, or ETL | 2 to 3 days, hourly | The batch window plus the days on either side |
| Weekly jobs: billing runs, weekend reports, Monday backfills | A full 7 days, hourly | A weekly job that runs once is invisible in a 3-day capture |
| Month-end or quarter-end processing | Cover the boundary itself | Those jobs often touch tables nothing else touches |

Two or three days is the usual answer. A week is better when you know something
important happens weekly, and the cost of the longer capture is small: hourly
snapshots are roughly 80 KiB each, so a week is around 13 MiB on disk.

**Name the jobs you know about before you start.** A nightly reconciliation that
rewrites a large table, a weekly export that reads across every tenant, a
month-end billing run — each of these can be the query that decides a shard key,
and each is easy to miss. Write the list down, check the capture covered them,
and tell your migration engineer which ones fell inside the window and which did
not. A capture that missed the weekly job is still useful, as long as everyone
knows it was missed.

      Mon      Tue      Wed      Thu      Fri      Sat      Sun
    ──────────────────────────────────────────────────────────────
      ███      ███      ███      ███      ███      ██       ██       daily peak
         ▒        ▒        ▒        ▒        ▒        ▒        ▒     nightly batch
                                                    ████            weekly billing
    ──────────────────────────────────────────────────────────────
    [───── 3-day capture ─────]
     sees the daily peak and the nightly batch, never sees the billing run

    [────────────────── 7-day capture ───────────────────────────]
     sees every recurring job above

### If you cannot capture for that long

A short capture is still worth having. Two snapshots an hour apart give a real
window, just a narrow one. Say so when you hand the bundle over, because a
one-hour window measured at 14:00 on a Tuesday describes exactly that and
nothing else.

What you should not do is finalize a session with a single snapshot and treat
the result as traffic. `finalize` refuses that by default, and `--allow-partial`
exists for the case where lifetime totals are genuinely all you can get. The
bundle then says so in its caveats, and no rate can be derived from it.

## Usage

    # Check the server, store the schema, take the baseline snapshot
    ps-discovery workload init --session ./workload-session

    # Append a snapshot. Safe to run from cron
    ps-discovery workload collect --session ./workload-session

    # Difference the snapshots and write the bundle
    ps-discovery workload finalize --session ./workload-session

    # Check on a session, with no database connection
    ps-discovery workload status --session ./workload-session

The tool finds `./config.yaml` on its own, as it does for a discovery run. Use
`--config` only to point at a different file.

### The session directory

`--session` names a directory that this tool creates and owns. Give all four
commands the same directory. It holds:

    workload-session/
      schema.json                      the schema, captured once by init
      snapshots/
        snapshot-20260826T211338.json.gz    one file per collect
        snapshot-20260826T211522.json.gz
      workload-<capture id>/           the bundle, written by finalize

You can name the directory anything and put it anywhere the cron user can write.
The directory is mode `0700`. Delete it when you have the bundle. Nothing
persists on the database server. To stop collection, remove the cron entry and
delete the directory.

### Scheduling

Each `collect` takes one snapshot and exits. It does no scheduling of its own,
so something must call it repeatedly. Cron is the intended driver. `init` prints
a crontab line you can paste:

    17 * * * * cd /path/to/ps-discovery && ./ps-discovery workload collect --session ./workload-session

The line uses minute 17 rather than the top of the hour, where scheduled jobs
tend to collect. Keep the `cd`. Cron starts in the home directory, so without it
the relative `--session` path misses and `./config.yaml` is not found.

Two snapshots are the minimum, because a window needs two readings to
difference. Hourly snapshots for two or three days is the usual shape, which is
48 to 72 snapshots at approximately 80 KiB each. See
[Choosing the capture period](#choosing-the-capture-period) for how long to run
in your case, and note that a weekly job needs a capture that spans a week.

Hourly suits most databases. Change the cadence only for a reason:

| Cadence | Crontab | When |
| --- | --- | --- |
| Hourly | `17 * * * *` | The default. Good resolution, small files |
| Every 15 minutes | `*/15 * * * *` | A short capture, or a sharp peak you want resolved |
| Every 6 hours | `17 */6 * * *` | A capture running for weeks, where volume matters |

A finer cadence does not measure more traffic, only the same traffic in smaller
pieces. `finalize` sums the intervals either way.

Schedule the capture across a peak. A workload measured only at 03:00 is not the
workload you must shard.

`init` takes the first snapshot, because deltas need a baseline. `collect`
refuses to run against a session directory that does not exist. Such a session
would have no baseline and no schema, and you would not find out for days.

Each `collect` writes its own timestamped file and modifies no existing file.
Overlapping cron ticks are therefore harmless, and there is nothing to lock.

### Check that the capture is running

`status` needs no database connection, so it is safe to run at any time and it
is the right thing to paste into a support thread:

    ps-discovery workload status --session ./workload-session

Check two things. The snapshot count must rise by one per scheduled tick, and
the newest snapshot must be recent. If the count stops rising, the cron entry is
the first place to look: run the `collect` command by hand and read the error.

A tick that fails leaves the earlier snapshots untouched, and capture resumes on
the next tick. A gap costs you that interval and nothing else.

### Finish the capture

Run `finalize` when you have enough snapshots. It writes the bundle and prints a
summary, the caveats, and where to send the result.

`finalize` reads only the session directory, so you can run it as often as you
like, and you can run it again later after more snapshots arrive.

## Deliver the bundle

Compress the whole bundle directory into one archive and send that archive to
your PlanetScale migration engineer:

    tar -czf workload-bundle.tar.gz -C ./workload-session bundle

Or, if you prefer a zip:

    cd ./workload-session && zip -r ../workload-bundle.zip bundle

Send one archive, not individual files. The files reference each other, and a
partial set cannot be used.

A bundle is small: it holds statement text and counters, not data, so the
archive is normally well under the attachment limit of any mail or ticket
system. Every file in it is mode `0600`, and the directory carries a
`.gitignore` so it cannot be committed by accident.

Treat it as you would any schema export. It holds table, column and index names,
statement shapes and row counts, and a `manifest.json` recording when the
capture ran and over which intervals. It holds no data from your tables and no
literal values from your queries. See
[Privacy](#privacy-what-leaves-your-database-and-what-never-does) below, which
includes how to check the bundle yourself before you send it.

When the migration is planned, delete the bundle and the session directory, and
remove the cron entry. Nothing persists on the database server.

## What it reads

The tool reads catalogs and statistics views only. It reads no user table. It
does not sample, count or `TABLESAMPLE` a user table.

It reads `pg_stat_statements`, `pg_stat_user_tables`, `pg_stat_user_indexes`,
`pg_class`, `pg_index`, `pg_attribute`, `pg_namespace`, `pg_settings`,
`pg_extension`, `information_schema.columns`, and the catalogs that the schema
analyzer already reads.

It also calls `pg_total_relation_size`, `pg_relation_size` and
`pg_indexes_size` on each table and index in scope. These report how many bytes
an object occupies on disk. They read no row, take no lock, and need no
privilege beyond the ones above.

The tool never runs `ANALYZE`, never calls `pg_stat_statements_reset()` and
never terminates a backend. It holds no table locks, because the schema comes
from catalog reads and not from `pg_dump`.

Session settings apply to this tool's own connection only. The tool sets
`default_transaction_read_only = on`, a statement timeout, a lock timeout, an
idle-in-transaction timeout, and `jit = off`. It never runs `ALTER SYSTEM`,
`ALTER DATABASE` or `ALTER ROLE`. Some connection poolers reject these settings
as startup parameters. The tool then applies them with `SET` and verifies them.

## What the server must provide

Capture needs two things, and `init` checks both before it writes anything:

| Requirement | Why |
| --- | --- |
| `pg_monitor` on the capture role | to read the statistics views at all, and to see statement text belonging to other roles |
| `pg_stat_statements`, installed and readable | it is the only record of the query workload, which is what a sharding scheme is planned from |

`init` stops with exit code 5 when either is missing, and names which one. It
reports the case where the extension is installed but not preloaded separately,
because `CREATE EXTENSION` then succeeds and collects nothing forever. When the
connected role is not a superuser, `init` names where the setting lives instead
of telling you to run a statement that will fail: a parameter group on RDS and
Aurora, or a database flag on Cloud SQL and AlloyDB.

## Privacy: what leaves your database, and what never does

You are about to send a file to another company, so you should know exactly what
is in it. The short version: the bundle describes the *shape* of your queries and
the *size* of your tables. It contains none of your data.

| In the bundle | Never in the bundle |
| --- | --- |
| Table, column and index names | Any row from any table |
| Query shapes, with values removed | The values your queries search for |
| How often each query ran, and how long it took | Customer names, emails, tokens, or any other content |
| Row counts and table sizes | Passwords, connection strings or credentials |
| How evenly each column's values are spread | Which values those are |
| How many rows were written to each table | The rows themselves |
| Your PostgreSQL settings and version | Sampled column values from database statistics |

### What a captured query looks like

Your application runs this:

    SELECT * FROM orders WHERE customer_email = 'ada@example.com' AND total > 500

The bundle records this:

    SELECT * FROM orders WHERE customer_email = $1 AND total > $2

The structure is what a sharding scheme is planned from: which tables a query
touches, which columns it filters on, and how often it runs. The values are not
needed for that, so they are removed before anything is written to disk.

This matters more than it might appear, because PostgreSQL does not always
return pre-normalized text. Statements such as `CREATE ROLE app PASSWORD 'secret'`
are stored by the database verbatim, so every statement passes through the same
removal step regardless of where it came from. A password is a string literal
like any other, and is removed like any other.

### The tool never reads your data

It reads the PostgreSQL catalogs and statistics views — the same information
`\d` in `psql` shows you, plus counters. It runs no `SELECT` against a table of
yours, takes no sample, and counts no rows. It also never reads the sampled
column values PostgreSQL keeps for its own planner in `pg_stats`, which are the
one place in the catalogs where real values from your tables appear.

### You can check all of this yourself

The bundle is plain text. Before you send it, read it:

    # The query log, in full
    less workload-session/workload-*/workload.sql

    # Search it for anything that worries you
    grep -i "@" workload-session/workload-*/workload.sql

Every value should appear as `$1`, `$2` and so on. If you find anything that
looks like real data, stop and tell your PlanetScale migration engineer before
you send the file — that is a bug in the tool, and PlanetScale wants to hear
about it.

Your security team can also read the removal logic directly. It is one function,
in `planetscale_discovery/common/sanitize.py`, and every statement in the tool
passes through it.

### Handling and retention

The tool sets every file it writes to mode `0600`, owner-only, and puts a
`.gitignore` in the bundle directory so it cannot be committed to a repository
by accident. Nothing is written to your database server, and nothing is sent
anywhere by the tool itself: you decide when and how the bundle travels.

Once the sharding scheme is planned, delete the bundle and the session
directory. Ask your migration engineer if you would like PlanetScale to confirm
deletion of its copy in writing.

## Which queries are kept

`workload.sql` holds the queries a sharding scheme has to be designed around:
`SELECT`, `INSERT`, `UPDATE`, `DELETE` and `MERGE`. Two kinds of statement are
left out, and `finalize` reports how many of each.

- **Statements that read and write no data**, such as `COMMIT`, `SET`, `GRANT`
  and schema changes. There is no data for them to reach, so there is nothing
  to decide about where they run.
- **Statements that are not your application's.** Monitoring agents, `psql`
  sessions, and this tool's own reads of the system catalogs. They name no table
  or view from your schema. On one 90-table capture that was 100 statements out
  of 572, and leaving them in produced every routing error reported against that
  capture, which buried the real result.

Nothing else is removed. Every query that touches one of your tables is kept,
however rarely it ran, because how much a query matters depends on a cost model
that the planning tools apply later.

## Exit codes

A cron wrapper must be able to tell a real fault from a finished capture.

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | usage or config error |
| 2 | no session at that path |
| 3 | a volume cap was reached; run `finalize` |
| 5 | the server cannot support collection; read the printed remediation |

## Message codes

Every condition the tool reports carries a code, and each has a section below
saying what it means and what to do about it.

`E1xx` stops the capture. `init` exits 5 and writes no session.
`W2xx` does not. The capture continues and the result carries a qualification.

There is no middle case. A sharding scheme is planned from your query workload,
and `pg_stat_statements` is the only place that workload exists. A run that
cannot read it has nothing to plan against, so it fails rather than producing a
bundle nobody can use.

### E101 absent

`pg_stat_statements` is not available on this server at all.

Install the PostgreSQL contrib package, add `pg_stat_statements` to
`shared_preload_libraries`, restart the server, then run
`CREATE EXTENSION pg_stat_statements;`. On a managed service the library setting
lives in the control plane: a parameter group on RDS and Aurora, a database flag
on Cloud SQL and AlloyDB.

### E102 not_installed

The library is loaded and only the extension is missing.

Run `CREATE EXTENSION pg_stat_statements;`. The extension is not *trusted*, so
this needs a superuser. If your role is not one, ask whoever administers the
database. No restart is needed.

### E103 not_installed_not_preloaded

Neither the library nor the extension is in place. Two steps, in this order.

Add `pg_stat_statements` to `shared_preload_libraries` and restart the server,
then run `CREATE EXTENSION pg_stat_statements;`. Creating the extension alone
leaves the view permanently empty, which is the trap this code exists to name.

### E104 not_preloaded

The extension exists but the library is not loaded, so the view is there and
stays empty forever.

Add `pg_stat_statements` to `shared_preload_libraries` and restart. Until the
restart nothing is recorded, so a capture started now would measure nothing.

### E105 unreadable

The extension is installed and this role cannot read it.

Grant `pg_monitor` to the capturing role, or capture with a role that already
has it. If the role does hold `pg_monitor` and you still see this, check where
the extension is installed: the tool reads the schema from `pg_extension`, so a
mismatch usually means the view was dropped and recreated by hand.

### E108 tracking_disabled

`pg_stat_statements` is installed and readable, and recording nothing:
`pg_stat_statements.track` is set to `none`.

The view will stay empty however long a capture runs, so this stops `init`
rather than producing a bundle with no queries in it. Set the parameter to
`top` to record statements, or to `all` to also record statements run inside
functions and procedures. It takes effect without a restart.

The view starts empty afterwards, so let traffic accumulate before capturing.
An hour is usually enough to tell whether statements are being recorded: check
with `SELECT count(*) FROM pg_stat_statements`.

### E106 insufficient_statistics_privileges

The role cannot read the statistics views at all, so nothing can be collected.

Grant `pg_monitor`. This is a stronger failure than [W213](#w213-statement_text_may_be_masked),
which is the case where the statistics are readable and only other roles'
statement text is hidden.

### E107 replica_target

You are connected to a standby.

`pg_stat_statements` and the relation counters are per instance, so a replica
shows only the queries routed to it, and the write workload is what decides a
shard key. Capture the primary instead. If you mean to measure this replica's
traffic, pass `--allow-replica` to `init`; the same code is then reported as a
warning and the capture continues.

### W201 nested_statements_not_tracked

`pg_stat_statements.track` is set to `top`, so a statement executed inside a
function or procedure is attributed to the call rather than recorded on its own.

If your application's work happens inside stored procedures, the query log will
describe the calls rather than the queries. Set `pg_stat_statements.track = all`
to record both. It costs more shared memory and more entries, so raise
`pg_stat_statements.max` with it.

### W202 statement_eviction_possible

`pg_stat_statements.max` is at or below its default, and PostgreSQL evicts the
least-used statements when the table fills.

An evicted statement is one the capture never sees, so a busy database loses its
long tail, which is often the queries that shard worst. Raise
`pg_stat_statements.max` (4 to 5 times the default is a reasonable start) and
restart. Compare `pg_stat_statements_info.dealloc` before and after: if it keeps
climbing, the value is still too low.

### W203 io_timing_off

`track_io_timing` is off, so block read and write times are reported as zero
rather than measured.

Every other counter is real. What you lose is the ability to tell a query that
is slow from disk from one that is slow from CPU. Set `track_io_timing = on` to
measure it. It needs no restart, and the overhead is small on modern hardware.

### W204 transaction_boundaries_invisible

Always reported, because it is a property of the data source rather than a
setting.

`pg_stat_statements` records statements, not transactions, so nothing in a
capture shows which tables your application writes together in one transaction.
That matters, because two tables written in the same transaction usually want to
be on the same shard. There is nothing to change. Bring that knowledge to the
review instead: list the transactions that matter most and the tables each one
touches.

### W205 relation_read_failed

A snapshot could not read `pg_stat_user_tables` or `pg_stat_user_indexes`.

The snapshot is kept, so the window can still be measured from the ticks around
it. A single occurrence is usually transient, such as a failover or a statement
timeout. If every tick reports it, treat it as
[E106](#e106-insufficient_statistics_privileges) and check the role's privileges.

### W206 no_tables_in_scope

The statistics views returned no table at all.

Either the database is empty, or `workload.schemas` in the config names schemas
that do not exist. Check the spelling, and note that the setting is case
sensitive.

### W207 statistics_not_readable

Table counters came back but every `idx_scan` was null, which means the role can
list the relations and not read their statistics.

Grant `pg_monitor`. This is the misleading case the code exists to name: the
table list looks correct while the counters behind it are blank.

### W208 pg_stat_statements_unavailable

A snapshot collected no statements, although `init` confirmed the extension was
readable.

Something changed mid-capture: the extension was dropped, or the role lost its
grant. Re-run `init` in a new session directory once the cause is fixed. The
snapshots already taken remain valid for the window they cover.

### W209 columns_unavailable

This server lacks some counters the tool collects, usually because it is an
older major version.

Nothing to fix. The missing columns are recorded by name so a consumer cannot
read "the server does not have this" as "the value is zero". The tool supports
PostgreSQL 12 through 18, and the older the server, the more counters are
absent.

### W210 statement_read_failed

Reading `pg_stat_statements` failed for this tick, and the error text follows
the code.

The snapshot is kept. If the message is `relation "pg_stat_statements" does not
exist` while the extension is installed, the view is in a schema the tool could
not resolve; check
`SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'pg_stat_statements'`. If it is a permission error, see
[E105](#e105-unreadable).

### W211 row_cap_reached

The snapshot hit `workload.statement_row_limit`, so the tail of the workload is
missing from this tick.

Raise `workload.statement_row_limit` in the config. The default is 20,000
statements, and a database with more distinct query shapes than that is worth a
look on its own: an application that inlines literals instead of using
parameters produces a new shape per value.

### W212 statement_text_masked

PostgreSQL returned `<insufficient privilege>` instead of the statement text for
some entries. Those statements belong to another role.

Grant `pg_read_all_stats`, or capture with a role that has it. Masked statements
keep their counters and lose their text, so they cannot be planned.

### W214 column_stats_read_failed

A snapshot could not read `pg_stats`, so it recorded no column distributions.

The capture continues, and the query log, schema and row counts are unaffected.
What is lost is the evidence of whether a candidate shard key spreads evenly,
which the migration team would otherwise read from `column_stats.json`. A single
occurrence is usually transient. If every tick reports it, the role is missing
`pg_monitor`, so see [E106](#e106-insufficient_statistics_privileges).

### W213 statement_text_may_be_masked

Reported by `init`, before any statement is read: the role holds neither
`pg_monitor` nor `pg_read_all_stats`, so text belonging to other roles will be
hidden.

Grant `pg_monitor` before starting a long capture. Left unfixed, the workload is
biased toward the statements this role happens to run, and the bias is invisible
in the result. [W212](#w212-statement_text_masked) is the same problem observed
after the fact, with a count.

## What the capture cannot see

No measurement tool sees everything, and it is better to know the edges before
you plan a migration around the result. None of the following blocks a sharding
plan. Each one is a place where your knowledge of the application is worth more
than the measurement, so bring what you know to the review.

**Which tables you write together in one transaction.** PostgreSQL records
statements, not transactions, so the capture sees an `INSERT` into `orders` and
an `INSERT` into `order_items` without knowing they happened together. That
matters, because two tables written in the same transaction usually want to live
on the same shard. If they end up apart, those writes become distributed
transactions, which are slower and can fail in ways a single-shard write cannot.

*What to do:* list the transactions that matter most to you — checkout, signup,
whatever your critical write path is — and name the tables each one touches.
Your migration engineer will use that list directly.

**The real values your queries search for.** The capture knows a query filters
on `tenant_id`. It does not know that one tenant is 40% of your traffic. A shard
key that looks evenly distributed on paper can still put half your load on one
shard.

*What to do:* tell your migration engineer about any tenant, customer or region
that is much larger than the rest. If you can run a `GROUP BY` on the candidate
key yourself and share the top few counts, that answers the question
completely.

**Anything that did not run during the capture window.** A weekly billing job
that falls outside the window is invisible to the plan, and those jobs are often
the ones that read across every tenant at once. See
[Choosing the capture period](#choosing-the-capture-period).

*What to do:* check your list of scheduled jobs against the capture window, and
tell your migration engineer which ones ran and which did not.

**Queries PostgreSQL forgot before you started.** `pg_stat_statements` holds a
fixed number of statements and discards the least-used ones when it fills. Rare
queries can be dropped before the first snapshot is ever taken.

*What to do:* raise `pg_stat_statements.max` before the capture if you can. See
[W202](#w202-statement_eviction_possible).

**How much time your queries spend waiting on disk**, when `track_io_timing` is
off. Those figures come back as zero, meaning "not measured" rather than "fast".
Everything else — call counts, row counts, execution time — is measured
regardless.

*What to do:* nothing, unless you want that detail. Turning `track_io_timing` on
needs no restart. See [W203](#w203-io_timing_off).

## Related

- [Workload bundle format](workload-bundle.md)
- [Performance considerations](performance_considerations.md)
