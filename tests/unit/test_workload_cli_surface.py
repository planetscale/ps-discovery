"""The flags `workload --help` shows must be flags this command reads.

The workload subcommands originally borrowed the shared discovery argument
helpers, which carry --engine {postgres,mysql}, --analyzers, --providers and
four MySQL SSL options. None of them reach a capture: it is PostgreSQL only, it
runs no analyzer and it writes to --session. A customer reading that help saw
MySQL support this command has never had, and passing --engine mysql was
accepted and then silently ignored.
"""

import argparse

import pytest

from planetscale_discovery.workload.cli_workload import (
    EXIT_USAGE,
    add_workload_parser,
    handle_workload,
)

COMMANDS = ("init", "collect", "finalize", "status")

# Flags the shared discovery helpers add that a capture never reads.
IRRELEVANT = (
    "--engine",
    "--providers",
    "--analyzers",
    "--ssl-mode",
    "--mysql-ssl-ca",
    "--mysql-ssl-cert",
    "--mysql-ssl-key",
    "--local-summary",
    "--output-dir",
)


def parser():
    root = argparse.ArgumentParser(prog="ps-discovery")
    subparsers = root.add_subparsers(dest="command")
    add_workload_parser(subparsers)
    return root


def help_for(command):
    for action in parser()._subparsers._group_actions[0].choices["workload"]._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[command].format_help()
    raise AssertionError("workload subcommands not found")


class TestHelpShowsOnlyRelevantFlags:
    @pytest.mark.parametrize("command", COMMANDS)
    def test_no_irrelevant_flag_is_offered(self, command):
        text = help_for(command)
        for flag in IRRELEVANT:
            assert flag not in text, f"{command} --help still offers {flag}"

    @pytest.mark.parametrize("command", COMMANDS)
    def test_the_word_mysql_never_appears(self, command):
        assert "mysql" not in help_for(command).lower()

    @pytest.mark.parametrize("command", COMMANDS)
    def test_the_session_and_config_are_offered(self, command):
        text = help_for(command)
        assert "--session" in text
        assert "--config" in text

    @pytest.mark.parametrize("command", ("init", "collect", "finalize"))
    def test_a_capture_takes_connection_options(self, command):
        text = help_for(command)
        for flag in ("--host", "--port", "--database", "--username", "--password"):
            assert flag in text

    def test_status_needs_no_connection_options(self):
        """The help promises status runs without a database."""
        assert "--host" not in help_for("status")

    @pytest.mark.parametrize("command", COMMANDS)
    def test_the_flags_still_parse(self, command):
        args = parser().parse_args(["workload", command, "--session", "./wl"])
        assert args.session == "./wl"
        assert args.workload_command == command

    def test_engine_is_no_longer_accepted_after_the_subcommand(self):
        with pytest.raises(SystemExit):
            parser().parse_args(
                ["workload", "init", "--session", "./wl", "--engine", "mysql"]
            )


class TestMysqlIsRefused:
    """A config file, or --engine before the subcommand, can still say mysql."""

    class _Config:
        def __init__(self, engine):
            self.engine = engine

    class _Args:
        workload_command = "init"
        session = "./wl"

    def test_a_mysql_engine_is_a_usage_error(self, mocker):
        logger = mocker.Mock()
        code = handle_workload(self._Args(), self._Config("mysql"), logger)
        assert code == EXIT_USAGE
        assert "PostgreSQL only" in logger.error.call_args[0][0]

    def test_it_is_not_silently_ignored(self, mocker):
        """It ran a PostgreSQL capture and exited 0, which looked like success."""
        logger = mocker.Mock()
        assert handle_workload(self._Args(), self._Config("mysql"), logger) != 0

    def test_status_needs_no_engine(self, mocker):
        """status touches no database, so the engine cannot matter."""

        class Args:
            workload_command = "status"
            session = "/nonexistent-session"

        logger = mocker.Mock()
        assert handle_workload(Args(), self._Config("mysql"), logger) != EXIT_USAGE
