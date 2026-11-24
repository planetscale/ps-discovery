"""The PostgreSQL sslmode a run connects with.

ssl_mode is always passed to libpq, which then ignores PGSSLMODE. With a config
file loaded, ssl_mode defaulted to prefer, so a customer's PGSSLMODE=verify-full
was replaced by a mode that checks no certificate and falls back to plaintext.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from planetscale_discovery.config.config_manager import (
    ConfigManager,
    effective_ssl_mode,
)
from planetscale_discovery.database.discovery import (
    DatabaseDiscoveryTool,
    PostgreSQLDiscovery,
)
from planetscale_discovery.workload import cli_workload

TLS_ENV = ("PGSSLMODE", "PGSSLROOTCERT", "PGSSLCERT", "PGSSLKEY")


@pytest.fixture(autouse=True)
def clean_tls_env(monkeypatch):
    for name in TLS_ENV:
        monkeypatch.delenv(name, raising=False)


def database(**kwargs):
    return SimpleNamespace(
        **{"ssl_mode": "prefer", "ssl_root_cert": None, "ssl_cert": None, **kwargs}
    )


def load(tmp_path, database_block):
    path = tmp_path / "config.yaml"
    path.write_text("engine: postgres\ndatabase:\n" + database_block)
    return ConfigManager(str(path)).load_config(validate=False, config_required=True)


class TestEffectiveSslMode:
    def test_pgsslmode_applies_when_ssl_mode_is_left_at_its_default(self, monkeypatch):
        """Catches the default prefer replacing PGSSLMODE=verify-full."""
        monkeypatch.setenv("PGSSLMODE", "verify-full")
        assert effective_ssl_mode(database()) == "verify-full"

    def test_a_stronger_pgsslmode_wins_over_a_weaker_configured_mode(self, monkeypatch):
        """Catches a configured require weakening PGSSLMODE=verify-ca."""
        monkeypatch.setenv("PGSSLMODE", "verify-ca")
        assert effective_ssl_mode(database(ssl_mode="require")) == "verify-ca"

    def test_a_weaker_pgsslmode_never_weakens_the_configured_mode(self, monkeypatch):
        """Catches PGSSLMODE=disable downgrading a configured verify-full."""
        monkeypatch.setenv("PGSSLMODE", "disable")
        assert effective_ssl_mode(database(ssl_mode="verify-full")) == "verify-full"

    def test_the_configured_mode_is_used_without_pgsslmode(self):
        """Catches the configured mode being replaced when nothing overrides it."""
        assert effective_ssl_mode(database(ssl_mode="require")) == "require"

    def test_an_unknown_pgsslmode_stops_the_run(self, monkeypatch):
        """Catches a mistyped PGSSLMODE being dropped for the default prefer."""
        monkeypatch.setenv("PGSSLMODE", "verify_full")
        with pytest.raises(ValueError, match="PGSSLMODE='verify_full'"):
            effective_ssl_mode(database())

    def test_an_unknown_configured_mode_stops_the_run(self, monkeypatch):
        """Catches a mistyped ssl_mode silently discarding a valid PGSSLMODE."""
        monkeypatch.setenv("PGSSLMODE", "verify-full")
        with pytest.raises(ValueError, match="ssl_mode='verify_full'"):
            effective_ssl_mode(database(ssl_mode="verify_full"))

    def test_the_mode_in_effect_is_logged(self, caplog):
        """Catches a run that never says which sslmode it connected with."""
        caplog.set_level(logging.INFO)
        effective_ssl_mode(database(ssl_mode="verify-full"))
        assert "sslmode=verify-full" in caplog.text

    @pytest.mark.parametrize(
        "settings,env",
        [
            ({"ssl_root_cert": "/ca.crt"}, {}),
            ({"ssl_cert": "/client.crt"}, {}),
            ({}, {"PGSSLROOTCERT": "/ca.crt"}),
            ({}, {"PGSSLKEY": "/client.key"}),
        ],
        ids=["config-ca", "config-cert", "env-ca", "env-key"],
    )
    def test_a_certificate_without_a_verifying_mode_is_warned_about(
        self, settings, env, monkeypatch, caplog
    ):
        """Catches a CA or client certificate silently left unverified."""
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        effective_ssl_mode(database(ssl_mode="require", **settings))
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "sslmode=require does not check the server's host name" in (
            warnings[0].message
        )

    def test_a_certificate_with_a_verifying_mode_is_not_warned_about(self, caplog):
        """Catches the warning firing on a correct verify-full setup."""
        effective_ssl_mode(database(ssl_mode="verify-full", ssl_root_cert="/ca.crt"))
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]


class TestConfigFileWithPgsslmode:
    """The reported case: a config file without ssl_mode, and PGSSLMODE set."""

    def test_discovery_connects_with_pgsslmode(self, tmp_path, monkeypatch):
        """Catches discover() passing the config default prefer to libpq."""
        monkeypatch.setenv("PGSSLMODE", "verify-full")
        config = load(tmp_path, "  host: crdb.example.com\n  username: ro\n")
        assert config.database.ssl_mode == "prefer"

        with patch(
            "psycopg2.connect", side_effect=psycopg2.OperationalError("stop")
        ) as connect:
            with pytest.raises(ConnectionError):
                DatabaseDiscoveryTool(config).discover()

        assert connect.call_args.kwargs["sslmode"] == "verify-full"

    def test_the_analyzer_connection_uses_the_same_mode(self):
        """Catches the analyzer connection opening with different settings."""
        discovery = PostgreSQLDiscovery({"host": "h", "sslmode": "verify-full"})
        discovery.connection = MagicMock()
        with patch(
            "psycopg2.connect", side_effect=psycopg2.OperationalError("stop")
        ) as connect:
            discovery.run_analysis([])

        assert connect.call_args.kwargs["sslmode"] == "verify-full"

    def test_the_workload_connection_uses_pgsslmode_and_the_ca(
        self, tmp_path, monkeypatch
    ):
        """Catches workload capture connecting with prefer and without the CA
        and client certificate the config file names."""
        monkeypatch.setenv("PGSSLMODE", "verify-full")
        config = load(
            tmp_path,
            "  host: crdb.example.com\n  username: ro\n"
            "  ssl_root_cert: /etc/ca.crt\n"
            "  ssl_cert: /etc/client.crt\n"
            "  ssl_key: /etc/client.key\n",
        )

        with patch("psycopg2.connect", return_value=MagicMock()) as connect:
            cli_workload._connect(config, logging.getLogger("test"))

        assert connect.call_args.kwargs["sslmode"] == "verify-full"
        assert connect.call_args.kwargs["sslrootcert"] == "/etc/ca.crt"
        assert connect.call_args.kwargs["sslcert"] == "/etc/client.crt"
        assert connect.call_args.kwargs["sslkey"] == "/etc/client.key"
