"""
Database query response fixtures for testing
"""

POSTGRES_VERSION_RESPONSE = [
    {
        "version": "PostgreSQL 17.4 on aarch64-unknown-linux-gnu, compiled by gcc (GCC) 12.4.0, 64-bit"
    }
]

POSTGRES_VERSION_NUM_RESPONSE = [{"server_version_num": "170004"}]

POSTGRES_DATABASES_RESPONSE = [
    {
        "datname": "postgres",
        "datdba": "10",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 7715840,
    },
    {
        "datname": "template0",
        "datdba": "10",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 7715840,
    },
    {
        "datname": "template1",
        "datdba": "10",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 7715840,
    },
    {
        "datname": "testdb",
        "datdba": "16384",
        "encoding": "6",
        "datcollate": "C",
        "datctype": "C",
        "pg_database_size": 73000000,
    },
]

POSTGRES_TABLES_RESPONSE = [
    {
        "schemaname": "public",
        "tablename": "users",
        "tableowner": "postgres",
        "pg_total_relation_size": 16384000,
        "n_tup_ins": 1000,
        "n_tup_upd": 100,
        "n_tup_del": 10,
    },
    {
        "schemaname": "public",
        "tablename": "orders",
        "tableowner": "postgres",
        "pg_total_relation_size": 32768000,
        "n_tup_ins": 5000,
        "n_tup_upd": 500,
        "n_tup_del": 50,
    },
]

POSTGRES_INDEXES_RESPONSE = [
    {
        "schemaname": "public",
        "tablename": "users",
        "indexname": "users_pkey",
        "indexdef": "CREATE UNIQUE INDEX users_pkey ON public.users USING btree (id)",
        "pg_total_relation_size": 8192,
    },
    {
        "schemaname": "public",
        "tablename": "users",
        "indexname": "idx_users_email",
        "indexdef": "CREATE INDEX idx_users_email ON public.users USING btree (email)",
        "pg_total_relation_size": 16384,
    },
]

POSTGRES_EXTENSIONS_RESPONSE = [
    {
        "extname": "plpgsql",
        "extversion": "1.0",
        "extrelocatable": False,
        "extschema": "pg_catalog",
    }
]

POSTGRES_USERS_RESPONSE = [
    {
        "rolname": "postgres",
        "rolsuper": False,
        "rolinherit": True,
        "rolcreaterole": True,
        "rolcreatedb": True,
        "rolcanlogin": True,
        "rolreplication": False,
        "rolconnlimit": -1,
        "rolvaliduntil": None,
    },
    {
        "rolname": "rdsadmin",
        "rolsuper": True,
        "rolinherit": True,
        "rolcreaterole": True,
        "rolcreatedb": True,
        "rolcanlogin": True,
        "rolreplication": True,
        "rolconnlimit": -1,
        "rolvaliduntil": None,
    },
]

POSTGRES_SETTINGS_RESPONSE = [
    {
        "name": "max_connections",
        "setting": "100",
        "unit": None,
        "category": "Connections and Authentication / Connection Settings",
        "short_desc": "Sets the maximum number of concurrent connections.",
        "context": "postmaster",
        "vartype": "integer",
        "source": "configuration file",
        "min_val": "1",
        "max_val": "1000000",
        "boot_val": "100",
        "reset_val": "100",
    },
    {
        "name": "shared_buffers",
        "setting": "32768",
        "unit": "8kB",
        "category": "Resource Usage / Memory",
        "short_desc": "Sets the number of shared memory buffers.",
        "context": "postmaster",
        "vartype": "integer",
        "source": "configuration file",
        "min_val": "16",
        "max_val": "1073741823",
        "boot_val": "1024",
        "reset_val": "32768",
    },
]

POSTGRES_CONNECTIONS_RESPONSE = [
    {
        "pid": 12345,
        "usename": "postgres",
        "datname": "testdb",
        "client_addr": "127.0.0.1",
        "state": "active",
        "query": "SELECT * FROM users;",
        "backend_start": "2024-01-01 12:00:00",
    },
    {
        "pid": 12346,
        "usename": "appuser",
        "datname": "testdb",
        "client_addr": "10.0.1.100",
        "state": "idle",
        "query": "",
        "backend_start": "2024-01-01 11:00:00",
    },
]
