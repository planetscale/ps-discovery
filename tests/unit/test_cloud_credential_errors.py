"""A provider credential never reaches the report or the log.

A line break or leading space in a header value makes requests raise an error
that quotes the whole header, so each analyzer trims the credential, rejects
one that still holds whitespace, and records an authentication failure without
the exception's text.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from planetscale_discovery.cloud.analyzers.heroku_analyzer import HerokuAnalyzer
from planetscale_discovery.cloud.analyzers.neon_analyzer import NeonAnalyzer
from planetscale_discovery.cloud.analyzers.planetscale_analyzer import (
    PlanetScaleAnalyzer,
)
from planetscale_discovery.cloud.analyzers.supabase_analyzer import (
    SupabaseAnalyzer,
)

SECRET = "pscale_tkn_s3cr3tvalue"

# (analyzer, config built from one token, the header that token must produce)
PROVIDERS = [
    pytest.param(
        HerokuAnalyzer,
        lambda token: SimpleNamespace(api_key=token),
        f"Bearer {SECRET}",
        id="heroku",
    ),
    pytest.param(
        NeonAnalyzer,
        lambda token: SimpleNamespace(api_key=token),
        f"Bearer {SECRET}",
        id="neon",
    ),
    pytest.param(
        SupabaseAnalyzer,
        lambda token: SimpleNamespace(access_token=token),
        f"Bearer {SECRET}",
        id="supabase",
    ),
    pytest.param(
        PlanetScaleAnalyzer,
        lambda token: SimpleNamespace(service_token_id="tokenid", service_token=token),
        f"tokenid:{SECRET}",
        id="planetscale-token",
    ),
    pytest.param(
        PlanetScaleAnalyzer,
        lambda token: SimpleNamespace(service_token_id=token, service_token="tok"),
        f"{SECRET}:tok",
        id="planetscale-token-id",
    ),
]


def _ok():
    response = MagicMock()
    response.status_code = 200
    return response


def _recorded(analyzer, caplog):
    """Everything the report and the log would hold for this analyzer."""
    return repr(analyzer.errors) + repr(analyzer.warnings) + caplog.text


@pytest.mark.parametrize("analyzer_class,config,header", PROVIDERS)
@pytest.mark.parametrize("token", [SECRET + "\n", SECRET + "\r", " " + SECRET])
def test_surrounding_whitespace_is_trimmed_before_the_header_is_built(
    analyzer_class, config, header, token
):
    """Catches a credential sent with the line ending or leading space that
    makes requests raise an error quoting it."""
    analyzer = analyzer_class(config(token))
    with patch("requests.Session.get", return_value=_ok()):
        assert analyzer.authenticate() is True

    assert analyzer.session.headers["Authorization"] == header
    assert analyzer.errors == []


@pytest.mark.parametrize("analyzer_class,config,header", PROVIDERS)
@pytest.mark.parametrize(
    "token", ["s3cr3tpartA\ns3cr3tpartB", "s3cr3tpartA s3cr3tpartB", "s3cr3tpartA\x00"]
)
def test_inner_whitespace_or_a_control_character_is_rejected_without_its_value(
    analyzer_class, config, header, token, caplog
):
    """Catches a credential that still holds a line break being put in a
    header, or its value being named in the error."""
    caplog.set_level(logging.DEBUG)
    analyzer = analyzer_class(config(token))
    with patch("requests.Session.get") as get:
        assert analyzer.authenticate() is False

    get.assert_not_called()
    assert analyzer.errors
    assert "empty or contains whitespace or control characters" in (
        analyzer.errors[0]["message"]
    )
    assert "s3cr3tpart" not in _recorded(analyzer, caplog)


@pytest.mark.parametrize("analyzer_class,config,header", PROVIDERS)
def test_an_authentication_exception_is_recorded_without_its_text(
    analyzer_class, config, header, caplog
):
    """Catches add_error storing str(exception), which for a header error
    holds the whole Authorization header."""
    caplog.set_level(logging.DEBUG)
    analyzer = analyzer_class(config(SECRET))
    raised = requests.exceptions.InvalidHeader(
        f"Invalid return character or leading space in header: {header!r}"
    )
    with patch("requests.Session.get", side_effect=raised):
        assert analyzer.authenticate() is False

    recorded = _recorded(analyzer, caplog)
    assert SECRET not in recorded
    assert "authentication failed (InvalidHeader)" in analyzer.errors[0]["message"]


@pytest.mark.parametrize(
    "analyzer_class,config,env,header",
    [
        (HerokuAnalyzer, SimpleNamespace(api_key=None), {"HEROKU_API_KEY"}, "Bearer"),
        (NeonAnalyzer, SimpleNamespace(api_key=None), {"NEON_API_KEY"}, "Bearer"),
        (
            SupabaseAnalyzer,
            SimpleNamespace(access_token=None),
            {"SUPABASE_ACCESS_TOKEN"},
            "Bearer",
        ),
        (
            PlanetScaleAnalyzer,
            SimpleNamespace(service_token_id=None, service_token=None),
            {"PLANETSCALE_SERVICE_TOKEN_ID", "PLANETSCALE_SERVICE_TOKEN"},
            None,
        ),
    ],
    ids=["heroku", "neon", "supabase", "planetscale"],
)
def test_a_credential_from_the_environment_is_trimmed_too(
    analyzer_class, config, env, header
):
    """Catches the trim running before the environment fallback, which would
    send a variable read from a CRLF .env file as-is."""
    variables = {name: SECRET + "\r\n" for name in env}
    analyzer = analyzer_class(config)
    with patch.dict("os.environ", variables, clear=True):
        with patch("requests.Session.get", return_value=_ok()):
            assert analyzer.authenticate() is True

    expected = f"Bearer {SECRET}" if header else f"{SECRET}:{SECRET}"
    assert analyzer.session.headers["Authorization"] == expected


class TestCleanCredential:
    def _analyzer(self):
        return HerokuAnalyzer(SimpleNamespace(api_key=None))

    def test_returns_the_trimmed_value(self):
        assert self._analyzer().clean_credential("\tabc\r\n", "Heroku API key") == (
            "abc"
        )

    def test_a_value_of_only_whitespace_is_rejected(self):
        """Catches a blank credential being sent as an empty bearer token."""
        analyzer = self._analyzer()
        assert analyzer.clean_credential(" \n", "Heroku API key") is None
        assert analyzer.errors[0]["message"].startswith("The Heroku API key is empty")

    def test_a_non_string_value_is_converted(self):
        """Catches a YAML value read as a number failing on strip()."""
        analyzer = HerokuAnalyzer(SimpleNamespace(api_key=12345))
        with patch("requests.Session.get", return_value=_ok()):
            assert analyzer.authenticate() is True
        assert analyzer.session.headers["Authorization"] == "Bearer 12345"
