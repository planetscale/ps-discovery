"""search_path is pinned so a planted table cannot shadow a catalog read."""

from unittest.mock import MagicMock

from planetscale_discovery.database.discovery import PostgreSQLDiscovery
from planetscale_discovery.workload.cli_workload import SESSION_SETTINGS


class TestWorkloadConnection:
    def test_search_path_is_a_session_setting(self):
        assert ("search_path", "pg_catalog") in SESSION_SETTINGS

    def test_it_reaches_the_libpq_options_string(self):
        options = " ".join(f"-c {n}={v}" for n, v in SESSION_SETTINGS if v)
        assert "-c search_path=pg_catalog" in options


class TestAnalyzerConnection:
    """run_analysis opens its own connection, so it needs its own pin."""

    def test_the_analyzer_connection_pins_search_path(self, mocker):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        mocker.patch(
            "planetscale_discovery.database.discovery.psycopg2.connect",
            return_value=connection,
        )
        discovery = PostgreSQLDiscovery({"host": "h", "database": "d", "user": "u"})
        discovery.connection = MagicMock()
        discovery.run_analysis([])

        issued = [str(c.args[0]) for c in cursor.execute.call_args_list]
        assert any("search_path = pg_catalog" in s for s in issued), issued
