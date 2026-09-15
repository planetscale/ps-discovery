from datetime import datetime, timezone

from planetscale_discovery.workload.logs.timestamps import (
    instant_utc,
    parse_log_stamp,
    parse_window_bound,
    to_rfc3339,
)


class TestInstantUtc:
    def test_a_utc_suffixed_stamp(self):
        result = instant_utc("2024-01-15 10:30:00.123 UTC")
        assert result == datetime(2024, 1, 15, 10, 30, 0, 123000, tzinfo=timezone.utc)

    def test_a_gmt_suffixed_stamp(self):
        result = instant_utc("2024-01-15 10:30:00 GMT")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_a_z_suffixed_iso_stamp(self):
        result = instant_utc("2024-01-15T10:30:00Z")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_a_bare_numeric_offset_stamp(self):
        result = instant_utc("2024-01-15 10:30:00-05:00")
        assert result == datetime(2024, 1, 15, 15, 30, 0, tzinfo=timezone.utc)

    def test_a_zone_abbreviation_resolved_via_log_timezone(self):
        result = instant_utc("2024-06-15 10:30:00 EDT", "America/New_York")
        assert result == datetime(2024, 6, 15, 14, 30, 0, tzinfo=timezone.utc)

    def test_an_unresolvable_abbreviation_with_no_log_timezone_is_none(self):
        assert instant_utc("2024-06-15 10:30:00 XYZ") is None

    def test_empty_and_none_input(self):
        assert instant_utc("") is None
        assert instant_utc(None) is None

    def test_a_malformed_string(self):
        assert instant_utc("not-a-timestamp") is None

    def test_a_naive_datetime_value_is_none(self):
        assert instant_utc(datetime(2024, 1, 15, 10, 30, 0)) is None

    def test_an_aware_datetime_value_converts(self):
        aware = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert instant_utc(aware) == aware


class TestToRfc3339:
    def test_a_utc_suffixed_stamp(self):
        result = to_rfc3339("2024-01-15 10:30:00.123 UTC")
        assert result == "2024-01-15T10:30:00.123+00:00"

    def test_a_gmt_suffixed_stamp(self):
        result = to_rfc3339("2024-01-15 10:30:00 GMT")
        assert result == "2024-01-15T10:30:00+00:00"

    def test_a_bare_numeric_offset_stamp(self):
        result = to_rfc3339("2024-01-15 10:30:00-05")
        assert result == "2024-01-15T10:30:00-05:00"

    def test_a_zone_abbreviation_resolved_via_log_timezone(self):
        result = to_rfc3339("2024-06-15 10:30:00 EDT", "America/New_York")
        assert result == "2024-06-15T10:30:00.000-04:00"

    def test_an_unresolvable_abbreviation_falls_back_without_the_zone(self):
        result = to_rfc3339("2024-06-15 10:30:00 XYZ")
        assert result == "2024-06-15T10:30:00"

    def test_empty_and_none_input(self):
        assert to_rfc3339("") == ""
        assert to_rfc3339(None) == ""

    def test_a_string_with_no_time_component_is_returned_unchanged(self):
        assert to_rfc3339("garbage") == "garbage"


class TestParseWindowBound:
    def test_a_z_suffixed_iso_stamp(self):
        result = parse_window_bound("2024-01-15T10:30:00Z")
        assert result == datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

    def test_none_and_empty_input(self):
        assert parse_window_bound(None) is None
        assert parse_window_bound("") is None

    def test_a_malformed_string(self):
        assert parse_window_bound("not-a-date") is None


class TestParseLogStamp:
    def test_a_utc_suffixed_stamp_with_microseconds(self):
        result = parse_log_stamp("2024-01-15 10:30:00.123456 UTC")
        assert result == datetime(2024, 1, 15, 10, 30, 0, 123456)

    def test_a_zone_abbreviation_is_stripped(self):
        result = parse_log_stamp("2024-01-15 10:30:00 EDT")
        assert result == datetime(2024, 1, 15, 10, 30, 0)

    def test_a_malformed_string(self):
        assert parse_log_stamp("garbage") is None
