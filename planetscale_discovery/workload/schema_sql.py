"""
Render ``CREATE TABLE`` DDL from collected schema metadata.

The Neki sharding planning tools want a plain SQL file of ``CREATE TABLE``
statements. This module produces that file from the ``analysis_results.schema``
section the existing SchemaAnalyzer already collects -- so it needs no database
connection and works against an archived discovery JSON.

Why reconstruct instead of shelling out to ``pg_dump``:

* ``pg_dump --schema-only`` takes ACCESS SHARE on every table in one
  repeatable-read snapshot. For its duration it blocks ALTER TABLE, TRUNCATE and
  VACUUM FULL, and it pins xmin so vacuum cleanup is deferred. Reading catalogs
  costs the production database nothing.
* It needs a client binary whose major version is at least the server's, which
  is a hard failure in a slim container or a locked-down bastion.
* Its output carries owners, privileges, RLS policies and provider-specific
  extension DDL that we would have to filter out anyway.

The parts that would be genuinely hard to render by hand are not rendered by
hand. PostgreSQL has ``pg_get_indexdef`` and ``pg_get_constraintdef``, and the
SchemaAnalyzer already stores their output verbatim, so opclasses, INCLUDE
columns, partial predicates, exclusion constraints and foreign-key actions come
back as strings the server itself produced. Only the ``CREATE TABLE`` skeleton is
assembled here.

Every statement is re-parsed before it is emitted (see :func:`validate_round_trip`),
so malformed DDL fails loudly at bundle time instead of inside the consumer.

A note on what fidelity is achievable. ``pg_get_constraintdef`` is not a fixed
point under re-parse: feed PostgreSQL its own output for a constraint like
``CHECK (col::text = ANY ((ARRAY['a'::varchar])::text[]))`` and it re-renders the
casts as ``ANY (ARRAY[('a'::varchar)::text])`` -- same meaning, different text.
Verified directly against PostgreSQL 18, and ``pg_dump`` has the same behaviour
because it emits the same string. So byte-identical catalog text is not an
achievable invariant. The invariant that *is* achievable, and which the
integration tests assert, is **idempotency**: rendering a reloaded schema and
reloading that again reproduces the catalog exactly.
"""

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Schemas that are never part of a customer's application schema. "__neki"
# matches the exclusion list the planning tools apply.
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast", "__neki"})

# relkind values that represent a table the planning tools can plan against.
# 'r' = ordinary table, 'p' = partitioned table.
TABLE_RELKINDS = frozenset({"r", "p"})

# Constraint types, from pg_constraint.contype.
CONTYPE_PRIMARY_KEY = "p"
CONTYPE_FOREIGN_KEY = "f"
CONTYPE_UNIQUE = "u"
CONTYPE_CHECK = "c"
# PostgreSQL 18 materializes NOT NULL as a named catalog constraint, so a
# column marked NOT NULL now also appears in pg_constraint with contype 'n'.
# Earlier versions have no such row. The column definition we render already
# carries NOT NULL, so emitting these as well would be redundant, and would
# make the output differ between server versions for the same logical schema.
CONTYPE_NOT_NULL = "n"

# Constraint types that are not rendered as ALTER TABLE statements.
SKIPPED_CONTYPES = frozenset({CONTYPE_NOT_NULL})


def quote_identifier(name: str) -> str:
    """Quote an identifier only when it needs it.

    Unquoted lowercase identifiers keep the output readable; anything with
    uppercase, punctuation, a leading digit or a reserved-word risk gets quoted.
    Embedded double quotes are doubled.
    """
    if not name:
        return '""'
    safe = name[0].isalpha() and name[0].islower() or name[0] == "_"
    if safe:
        safe = all(c.islower() or c.isdigit() or c == "_" for c in name)
    if safe and not _is_reserved(name):
        return name
    return '"' + name.replace('"', '""') + '"'


# A small reserved-word set. Not exhaustive: the cost of over-quoting is zero,
# and format_type / pg_get_*def output is never passed through this function.
_RESERVED = frozenset(
    {
        "all",
        "analyse",
        "analyze",
        "and",
        "any",
        "array",
        "as",
        "asc",
        "authorization",
        "between",
        "binary",
        "both",
        "case",
        "cast",
        "check",
        "collate",
        "column",
        "constraint",
        "create",
        "cross",
        "current_date",
        "current_time",
        "current_timestamp",
        "current_user",
        "default",
        "deferrable",
        "desc",
        "distinct",
        "do",
        "else",
        "end",
        "except",
        "false",
        "for",
        "foreign",
        "freeze",
        "from",
        "full",
        "grant",
        "group",
        "having",
        "ilike",
        "in",
        "initially",
        "inner",
        "intersect",
        "into",
        "is",
        "isnull",
        "join",
        "leading",
        "left",
        "like",
        "limit",
        "localtime",
        "localtimestamp",
        "natural",
        "not",
        "notnull",
        "null",
        "offset",
        "on",
        "only",
        "or",
        "order",
        "outer",
        "overlaps",
        "placing",
        "primary",
        "references",
        "right",
        "select",
        "session_user",
        "similar",
        "some",
        "table",
        "then",
        "to",
        "trailing",
        "true",
        "union",
        "unique",
        "user",
        "using",
        "verbose",
        "when",
        "where",
        "with",
    }
)


def _is_reserved(name: str) -> bool:
    return name.lower() in _RESERVED


def qualified_name(schema: str, table: str) -> str:
    """Return a schema-qualified, minimally-quoted relation name."""
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _render_column(column: Dict[str, Any]) -> Optional[str]:
    """Render one column definition, or None when it cannot be rendered.

    The type comes from ``format_type``, which already includes every modifier
    (length, precision, array brackets, time zone), so it is emitted verbatim.
    """
    name = column.get("column_name")
    data_type = column.get("data_type")
    if not name or not data_type:
        return None

    parts = [quote_identifier(name), data_type]

    # Emit COLLATE only when it differs from the type's default. "default" is
    # the name of the database default collation and adding it is noise.
    collation = column.get("collation_name")
    if collation and collation not in ("default", "C.UTF-8"):
        parts.append(f"COLLATE {quote_identifier(collation)}")

    # A generated or identity column carries its own clause and never also a
    # plain DEFAULT.
    generated_kind = (column.get("generated_kind") or "").strip()
    identity_kind = (column.get("identity_kind") or "").strip()
    default_expr = column.get("column_default")

    if generated_kind == "s":
        # attgenerated 's' means STORED. The expression lives in the default slot.
        if default_expr:
            parts.append(f"GENERATED ALWAYS AS ({default_expr}) STORED")
    elif identity_kind in ("a", "d"):
        always = "ALWAYS" if identity_kind == "a" else "BY DEFAULT"
        parts.append(f"GENERATED {always} AS IDENTITY")
    elif default_expr:
        parts.append(f"DEFAULT {default_expr}")

    if column.get("not_null"):
        parts.append("NOT NULL")

    return " ".join(parts)


def _partition_key(table: Dict[str, Any]) -> Optional[str]:
    """Return the PARTITION BY clause for a partitioned parent, if any.

    SchemaAnalyzer nests this under ``partition_info.partition_key``; the
    top-level fallback keeps this working if that is ever flattened.
    """
    info = table.get("partition_info") or {}
    return info.get("partition_key") or table.get("partition_key")


def render_create_table(
    table: Dict[str, Any],
    partition_bounds: Optional[Dict[Tuple[str, str], str]] = None,
) -> Optional[str]:
    """Render a single ``CREATE TABLE`` statement, or None if not renderable.

    ``partition_bounds`` maps ``(schema, table)`` to the ``FOR VALUES ...``
    clause of a partition, so a child can be emitted as ``PARTITION OF`` its
    parent rather than as a standalone table. That distinction matters: a
    detached table plans differently from a partition.
    """
    schema = table.get("schema_name")
    name = table.get("table_name")
    if not schema or not name:
        return None

    unlogged = "UNLOGGED " if table.get("persistence") == "u" else ""
    relation = qualified_name(schema, name)

    # A partition inherits its parent's column list, so PARTITION OF replaces
    # the column definitions entirely.
    parent = table.get("parent_table") or {}
    bound = (partition_bounds or {}).get((schema, name))
    if table.get("is_partition") and parent.get("parent_table") and bound:
        parent_relation = qualified_name(
            parent.get("parent_schema") or schema, parent["parent_table"]
        )
        return (
            f"CREATE {unlogged}TABLE {relation} "
            f"PARTITION OF {parent_relation} {bound};"
        )

    columns = table.get("columns") or []
    rendered = [c for c in (_render_column(col) for col in columns) if c]
    if not rendered:
        # A table with no readable columns cannot be planned against.
        return None

    body = ",\n".join(f"    {line}" for line in rendered)
    statement = f"CREATE {unlogged}TABLE {relation} (\n{body}\n)"

    # A partitioned parent carries its PARTITION BY clause; without it the
    # planner sees a plain table and plans differently.
    partition_key = _partition_key(table)
    if partition_key:
        statement += f" PARTITION BY {partition_key}"

    return statement + ";"


def render_create_sequence(sequence: Dict[str, Any]) -> Optional[str]:
    """Render a ``CREATE SEQUENCE`` from a pg_sequences row.

    Sequences must be emitted before the tables that reference them: a ``serial``
    column's default is ``nextval('name'::regclass)``, which fails to load if the
    sequence does not exist yet. ``last_value`` is deliberately not carried over
    -- the current position of a sequence is runtime state, not schema, and it is
    irrelevant to query planning.
    """
    schema = sequence.get("schema_name")
    name = sequence.get("sequence_name")
    if not schema or not name:
        return None

    parts = [f"CREATE SEQUENCE {qualified_name(schema, name)}"]
    for keyword, key in (
        ("INCREMENT BY", "increment"),
        ("MINVALUE", "min_value"),
        ("MAXVALUE", "max_value"),
        ("START WITH", "start_value"),
        ("CACHE", "cache_size"),
    ):
        value = sequence.get(key)
        if value is not None:
            parts.append(f"{keyword} {value}")
    if sequence.get("is_cycle"):
        parts.append("CYCLE")
    return " ".join(parts) + ";"


def _sequences_referenced_by(
    tables: Sequence[Dict[str, Any]], sequences: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Keep only sequences a captured column default actually references.

    A database can hold sequences nothing in the captured set uses; emitting
    those would be noise in a file whose only purpose is to let a reader
    resolve the tables.
    """
    defaults = " ".join(
        column.get("column_default") or ""
        for table in tables
        for column in (table.get("columns") or [])
    )
    if not defaults.strip():
        return []
    return [
        sequence
        for sequence in sequences
        if sequence.get("sequence_name") and f"{sequence['sequence_name']}" in defaults
    ]


def _split_constraints(
    constraints: Iterable[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split constraints into (non-foreign-key, foreign-key) lists.

    Constraint types in SKIPPED_CONTYPES are dropped entirely -- see
    CONTYPE_NOT_NULL for why PostgreSQL 18's NOT NULL rows are not rendered.
    """
    non_fk, fk = [], []
    for constraint in constraints:
        contype = constraint.get("constraint_type")
        if contype in SKIPPED_CONTYPES:
            continue
        if contype == CONTYPE_FOREIGN_KEY:
            fk.append(constraint)
        else:
            non_fk.append(constraint)
    return non_fk, fk


def _render_constraint(constraint: Dict[str, Any]) -> Optional[str]:
    """Render an ALTER TABLE ... ADD CONSTRAINT from pg_get_constraintdef."""
    schema = constraint.get("schema_name")
    table = constraint.get("table_name")
    name = constraint.get("constraint_name")
    definition = constraint.get("constraint_definition")
    if not (schema and table and name and definition):
        return None
    return (
        f"ALTER TABLE {qualified_name(schema, table)} "
        f"ADD CONSTRAINT {quote_identifier(name)} {definition};"
    )


def application_views(
    schema_analysis: Dict[str, Any],
    target_schemas: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """The views this application defines.

    An extension installs its views into an ordinary schema: ``CREATE EXTENSION
    pg_stat_statements`` puts ``public.pg_stat_statements`` next to the
    application's own tables. Filtering on the schema alone therefore lets this
    tool's own statistics reads back into the query log. PostgreSQL reserves the
    ``pg_`` prefix for system use, so the name settles it.
    """
    wanted = set(target_schemas) if target_schemas else None
    views = []
    for view in schema_analysis.get("view_analysis") or []:
        schema = view.get("schema_name")
        name = (view.get("view_name") or "").lower()
        if schema in SYSTEM_SCHEMAS or not name or name.startswith("pg_"):
            continue
        if wanted is not None and schema not in wanted:
            continue
        views.append(view)
    return views


def render_create_view(view: Dict[str, Any]) -> Optional[str]:
    """Render one view from the definition ``pg_get_viewdef`` produced.

    The definition is the server's own text, so it is used verbatim. Only the
    ``CREATE`` header is assembled here.
    """
    schema = view.get("schema_name")
    name = view.get("view_name")
    definition = (view.get("view_definition") or "").strip()
    if not schema or not name or not definition:
        return None
    keyword = (
        "CREATE MATERIALIZED VIEW" if view.get("view_type") == "m" else "CREATE VIEW"
    )
    return f"{keyword} {qualified_name(schema, name)} AS\n{definition.rstrip(';')};"


def _sort_views_by_dependency(
    views: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Order views so a view precedes any view that selects from it.

    Unlike a foreign key, this ordering is required: ``CREATE VIEW`` fails when
    a relation it names does not exist yet. Cycles are impossible in PostgreSQL,
    but the loop still falls back to name order so it always terminates.
    """
    key_of: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for view in views:
        schema = view.get("schema_name")
        name = view.get("view_name")
        if schema and name:
            key_of[(schema, name)] = view

    dependencies: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {
        key: set() for key in key_of
    }
    for key, view in key_of.items():
        body = (view.get("view_definition") or "").lower()
        for other in key_of:
            if other == key:
                continue
            qualified = f"{other[0]}.{other[1]}".lower()
            if qualified in body:
                dependencies[key].add(other)

    ordered: List[Dict[str, Any]] = []
    emitted: Set[Tuple[str, str]] = set()
    remaining = sorted(key_of, key=lambda k: (k[0] or "", k[1] or ""))

    while remaining:
        progressed = False
        still_waiting = []
        for key in remaining:
            if dependencies[key] <= emitted:
                ordered.append(key_of[key])
                emitted.add(key)
                progressed = True
            else:
                still_waiting.append(key)
        remaining = still_waiting
        if not progressed:
            ordered.extend(key_of[key] for key in remaining)
            break

    return ordered


def _sort_tables_by_dependency(
    tables: Sequence[Dict[str, Any]],
    foreign_keys: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Order tables so a referenced table precedes its referencer.

    Foreign keys are emitted as separate ALTER statements, so this ordering is
    cosmetic rather than required. It still helps a human read the file, and it
    keeps the output stable. Cycles (including self-references) are broken by
    falling back to name order, so this always terminates.
    """
    # A table missing either name cannot be rendered at all, so it is dropped
    # here rather than carried through the sort with a (None, None) key.
    key_of: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for table in tables:
        schema = table.get("schema_name")
        name = table.get("table_name")
        if schema and name:
            key_of[(schema, name)] = table

    dependencies: Dict[Tuple[str, str], Set[Tuple[str, str]]] = {
        key: set() for key in key_of
    }
    for fk in foreign_keys:
        child = (fk.get("schema_name"), fk.get("table_name"))
        parent = (fk.get("foreign_schema"), fk.get("foreign_table"))
        if child in dependencies and parent in key_of and parent != child:
            dependencies[child].add(parent)

    ordered: List[Dict[str, Any]] = []
    emitted: Set[Tuple[str, str]] = set()
    remaining = sorted(key_of, key=lambda k: (k[0] or "", k[1] or ""))

    while remaining:
        progressed = False
        still_waiting = []
        for key in remaining:
            if dependencies[key] <= emitted:
                ordered.append(key_of[key])
                emitted.add(key)
                progressed = True
            else:
                still_waiting.append(key)
        remaining = still_waiting
        if not progressed:
            # A cycle. Emit the rest in name order; FKs are separate statements
            # so the result is still loadable.
            ordered.extend(key_of[key] for key in remaining)
            break

    return ordered


def render_schema_sql(
    schema_analysis: Dict[str, Any],
    target_schemas: Optional[Sequence[str]] = None,
    include_foreign_keys: bool = True,
) -> Dict[str, Any]:
    """Render a full schema SQL file from an ``analysis_results.schema`` dict.

    Returns a dict with:
      ``sql``            -- the complete file text
      ``tables_sql``     -- CREATE TABLE statements only (minimal fallback file)
      ``statements``     -- every statement, in emission order
      ``tables``         -- list of ``(schema, table)`` actually emitted
      ``views``          -- list of ``(schema, view)`` actually emitted
      ``skipped``        -- relations that could not be rendered, with a reason
    """
    tables = [
        t
        for t in (schema_analysis.get("table_analysis") or [])
        if t.get("schema_name") not in SYSTEM_SCHEMAS
        and (t.get("table_type") or "r") in TABLE_RELKINDS
    ]
    if target_schemas:
        wanted = set(target_schemas)
        tables = [t for t in tables if t.get("schema_name") in wanted]

    emitted_keys = {(t.get("schema_name"), t.get("table_name")) for t in tables}

    indexes = [
        i
        for i in (schema_analysis.get("index_analysis") or [])
        if (i.get("schema_name"), i.get("table_name")) in emitted_keys
    ]
    constraints = [
        c
        for c in (schema_analysis.get("constraint_analysis") or [])
        if (c.get("schema_name"), c.get("table_name")) in emitted_keys
    ]
    non_fk, foreign_keys = _split_constraints(constraints)

    # Partition bounds live in their own analysis section, keyed by the
    # partition's own schema and name.
    partition_bounds: Dict[Tuple[str, str], str] = {}
    for entry in schema_analysis.get("partition_analysis") or []:
        bound = entry.get("partition_bound")
        if bound:
            key = (entry.get("partition_schema"), entry.get("partition_name"))
            partition_bounds[key] = bound

    ordered_tables = _sort_tables_by_dependency(tables, foreign_keys)
    # A partition cannot be created before its parent exists, so partitioned
    # parents are emitted first. Foreign-key ordering above is cosmetic; this
    # one is required for the file to load.
    ordered_tables.sort(key=lambda t: 1 if t.get("is_partition") else 0)

    sections: List[Tuple[str, List[str]]] = []
    skipped: List[Dict[str, str]] = []

    # Sequences first: a serial column's DEFAULT is nextval('seq'::regclass),
    # which cannot resolve until the sequence exists.
    sequences = [
        s
        for s in (schema_analysis.get("sequence_analysis") or [])
        if s.get("schema_name") not in SYSTEM_SCHEMAS
        and (not target_schemas or s.get("schema_name") in set(target_schemas))
    ]
    sequence_statements = [
        statement
        for statement in (
            render_create_sequence(s)
            for s in _sequences_referenced_by(tables, sequences)
        )
        if statement
    ]
    sections.append(("Sequences", sequence_statements))

    create_statements = []
    for table in ordered_tables:
        statement = render_create_table(table, partition_bounds)
        if statement:
            create_statements.append(statement)
        else:
            skipped.append(
                {
                    "table": f"{table.get('schema_name')}.{table.get('table_name')}",
                    "reason": "no renderable columns",
                }
            )
    sections.append(("Tables", create_statements))

    # Indexes come from pg_get_indexdef, which emits a complete CREATE INDEX
    # including partial predicates, INCLUDE columns, opclasses and sort order.
    # A primary-key or unique-constraint index is created by its constraint, so
    # emitting it here as well would be a duplicate.
    constraint_index_names = {
        c.get("constraint_name") for c in non_fk if c.get("constraint_name")
    }
    index_statements = []
    for index in indexes:
        if index.get("index_name") in constraint_index_names:
            continue
        definition = index.get("index_definition")
        if definition:
            index_statements.append(definition.rstrip(";") + ";")
    sections.append(("Indexes", index_statements))

    constraint_statements = [s for s in (_render_constraint(c) for c in non_fk) if s]
    sections.append(("Keys, unique and check constraints", constraint_statements))

    # Views come after the tables they read. An application that queries through
    # a view names only the view, so a schema file without it cannot plan that
    # statement, and the query log would carry traffic the planner must reject.
    views = application_views(schema_analysis, target_schemas)
    view_statements = []
    emitted_views: List[Tuple[str, str]] = []
    for view in _sort_views_by_dependency(views):
        statement = render_create_view(view)
        if statement:
            view_statements.append(statement)
            emitted_views.append((view["schema_name"], view["view_name"]))
        else:
            skipped.append(
                {
                    "table": f"{view.get('schema_name')}.{view.get('view_name')}",
                    "reason": "no view definition available",
                }
            )
    sections.append(("Views", view_statements))

    if include_foreign_keys:
        fk_statements = [s for s in (_render_constraint(c) for c in foreign_keys) if s]
        # Grouped last behind one banner so they can be removed in a single
        # range. Neki strips every foreign key when deploying a target schema,
        # so they are informational for planning purposes.
        sections.append(("Foreign keys", fk_statements))

    lines: List[str] = []
    all_statements: List[str] = []
    for title, statements in sections:
        if not statements:
            continue
        lines.append("--")
        lines.append(f"-- {title}")
        lines.append("--")
        lines.append("")
        for statement in statements:
            lines.append(statement)
            lines.append("")
        all_statements.extend(statements)

    return {
        "sql": "\n".join(lines).rstrip() + "\n" if lines else "",
        "tables_sql": (
            ("\n\n".join(create_statements).rstrip() + "\n")
            if create_statements
            else ""
        ),
        "statements": all_statements,
        "tables": sorted(emitted_keys),
        "views": sorted(emitted_views),
        "skipped": skipped,
    }


def validate_round_trip(sql_text: str) -> Dict[str, Any]:
    """Re-parse every statement in ``sql_text`` with the PostgreSQL parser.

    The consumer feeds this file to a PostgreSQL parser, so SQL that almost
    parses is the worst outcome. Running the same parser here turns that into a
    loud failure at bundle time.

    Returns ``{"available", "ok", "statement_count", "failures"}``. When pglast
    is not installed, ``available`` is False and ``ok`` is None -- the caller
    should surface that rather than assume success.
    """
    try:
        from pglast import parser
    except ImportError:
        return {
            "available": False,
            "ok": None,
            "statement_count": 0,
            "failures": [],
            "reason": "pglast is not installed",
        }

    failures: List[Dict[str, Any]] = []
    statements: List[str] = []
    if isinstance(sql_text, (list, tuple)):
        # Our own rendered statements. Preferred, because parser.split() parses
        # as it splits and so fails on the whole file if any one statement is
        # bad -- which makes it impossible to tell the good ones from the bad.
        statements = [str(s) for s in sql_text]
    else:
        try:
            statements = list(parser.split(sql_text))
        except Exception as e:
            return {
                "available": True,
                "ok": False,
                "statement_count": 0,
                "failures": [{"statement": None, "error": f"could not split: {e}"}],
                "clean_sql": None,
            }

    kept: List[str] = []
    for statement in statements:
        text = statement.strip()
        if not text:
            continue
        try:
            parser.parse_sql(text)
        except Exception as e:
            # The full text is kept, not a truncated copy: the caller writes it
            # to the unparsed file and needs to be able to remove it from the
            # emitted schema, which a 200-character prefix cannot support.
            failures.append(
                {"statement": text[:200], "full_statement": text, "error": str(e)}
            )
        else:
            kept.append(text)

    return {
        "available": True,
        "ok": not failures,
        "statement_count": len(statements),
        "failures": failures,
        # Only the statements that parsed. Emitting the rest would hand the
        # planner DDL its own parser rejected, which is the failure this gate
        # exists to prevent.
        "clean_sql": ";\n\n".join(s.rstrip(";") for s in kept) + ";\n" if kept else "",
    }
