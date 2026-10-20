"""Run-mode configuration: SIMULATION by default, LIVE only with two explicit switches."""

from __future__ import annotations

import pytest

from scalix.config import (
    DEFAULT_CONFIG_PATH,
    ConfigError,
    Mode,
    ScalixConfig,
    load_config,
    parse_config,
)


def test_defaults_are_simulation_with_live_disabled():
    cfg = ScalixConfig()
    assert cfg.mode is Mode.SIMULATION
    assert cfg.live_enabled is False
    assert not cfg.mode.uses_aws
    assert not cfg.mode.may_change_aws


def test_shipped_config_file_is_simulation():
    assert DEFAULT_CONFIG_PATH.is_file()
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.mode is Mode.SIMULATION
    assert cfg.live_enabled is False


def test_missing_default_file_falls_back_to_simulation(monkeypatch, tmp_path):
    monkeypatch.setattr("scalix.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")
    assert load_config() == ScalixConfig()


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml")


@pytest.mark.parametrize("text", ["", "# only comments\n"])
def test_empty_file_gives_defaults(tmp_path, text):
    f = tmp_path / "scalix.yaml"
    f.write_text(text)
    assert load_config(f) == ScalixConfig()


@pytest.mark.parametrize("raw, expected", [
    ("simulation", Mode.SIMULATION),
    ("READ_ONLY", Mode.READ_ONLY),
    (" read_only ", Mode.READ_ONLY),
])
def test_mode_names_are_case_insensitive(raw, expected):
    assert parse_config({"mode": raw}).mode is expected


def test_read_only_uses_aws_but_cannot_change_it():
    cfg = parse_config({"mode": "READ_ONLY"})
    assert cfg.mode.uses_aws
    assert not cfg.mode.may_change_aws


def test_live_without_enable_switch_is_rejected():
    with pytest.raises(ConfigError, match="live.enabled"):
        parse_config({"mode": "LIVE"})
    with pytest.raises(ConfigError, match="live.enabled"):
        parse_config({"mode": "LIVE", "live": {"enabled": False}})
    with pytest.raises(ConfigError):
        ScalixConfig(mode=Mode.LIVE)


def test_live_with_both_switches_is_accepted():
    cfg = parse_config({"mode": "LIVE", "live": {"enabled": True}})
    assert cfg.mode.may_change_aws


@pytest.mark.parametrize("data, match", [
    ({"mode": "PRODUCTION"}, "unknown mode"),
    ({"mode": 1}, "must be text"),
    ({"mode": "LIVE", "live": {"enabled": "true"}}, "true or false"),
    ({"live": "on"}, "key: value"),
    ({"mdoe": "LIVE"}, "unknown setting"),
    ({"live": {"enabeld": True}}, "unknown live setting"),
    (["SIMULATION"], "top level"),
])
def test_invalid_settings_are_rejected(data, match):
    with pytest.raises(ConfigError, match=match):
        parse_config(data)


def test_invalid_yaml_is_rejected(tmp_path):
    f = tmp_path / "scalix.yaml"
    f.write_text("mode: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(f)


def test_environment_variables_do_not_change_the_mode(monkeypatch, tmp_path):
    for name in ("SCALIX_MODE", "MODE", "SCALIX_LIVE_ENABLED"):
        monkeypatch.setenv(name, "LIVE")
    f = tmp_path / "scalix.yaml"
    f.write_text("mode: SIMULATION\n")
    assert load_config(f).mode is Mode.SIMULATION
