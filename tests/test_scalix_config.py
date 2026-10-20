"""Run-mode and AWS settings: SIMULATION by default, AWS modes need explicit names, LIVE needs two switches."""

from __future__ import annotations

import pytest

import scalix.config as config_module
from scalix.config import (
    DEFAULT_CONFIG_PATH,
    PROJECT_ROOT,
    AwsSettings,
    ConfigError,
    Mode,
    ScalixConfig,
    load_config,
    merge_settings,
    parse_config,
)

AWS = {"profile": "scalix-academy", "region": "us-east-1"}


@pytest.fixture
def isolated_paths(monkeypatch, tmp_path):
    """Point the default and local config paths at a temp folder."""
    base, local = tmp_path / "scalix.yaml", tmp_path / "scalix.local.yaml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", base)
    monkeypatch.setattr(config_module, "LOCAL_CONFIG_PATH", local)
    return base, local


def test_defaults_are_simulation_with_live_disabled_and_no_aws_names():
    cfg = ScalixConfig()
    assert cfg.mode is Mode.SIMULATION and cfg.live_enabled is False
    assert not cfg.mode.uses_aws and not cfg.mode.may_change_aws
    assert cfg.aws == AwsSettings()
    assert cfg.aws.profile is None and cfg.aws.region is None and cfg.aws.auto_scaling_group is None
    assert cfg.aws.metric_period_seconds == 300


def test_shipped_config_file_is_simulation_without_aws_names():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.mode is Mode.SIMULATION and cfg.live_enabled is False
    assert cfg.aws.profile is None and cfg.aws.region is None


def test_example_local_file_is_valid_read_only():
    cfg = load_config(PROJECT_ROOT / "config" / "scalix.local.example.yaml")
    assert cfg.mode is Mode.READ_ONLY and cfg.aws.profile == "scalix-academy"


def test_no_files_gives_safe_defaults(isolated_paths):
    assert load_config() == ScalixConfig()


def test_local_file_overrides_the_committed_file(isolated_paths):
    base, local = isolated_paths
    base.write_text("mode: SIMULATION\naws:\n  region: eu-west-1\n  metric_period_seconds: 300\n")
    local.write_text("mode: READ_ONLY\naws:\n  profile: mine\n  auto_scaling_group: scalix-workers\n")
    cfg = load_config()
    assert cfg.mode is Mode.READ_ONLY
    assert (cfg.aws.profile, cfg.aws.region, cfg.aws.auto_scaling_group) == ("mine", "eu-west-1", "scalix-workers")


def test_only_the_committed_file_when_no_local(isolated_paths):
    base, _ = isolated_paths
    base.write_text("mode: SIMULATION\n")
    assert load_config().mode is Mode.SIMULATION


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yaml")


@pytest.mark.parametrize("text", ["", "# only comments\n"])
def test_empty_file_gives_defaults(tmp_path, text):
    f = tmp_path / "scalix.yaml"
    f.write_text(text)
    assert load_config(f) == ScalixConfig()


@pytest.mark.parametrize("raw, expected", [
    ("simulation", Mode.SIMULATION), ("READ_ONLY", Mode.READ_ONLY), (" read_only ", Mode.READ_ONLY),
])
def test_mode_names_are_case_insensitive(raw, expected):
    assert parse_config({"mode": raw, "aws": AWS}).mode is expected


def test_read_only_uses_aws_but_cannot_change_it():
    cfg = parse_config({"mode": "READ_ONLY", "aws": AWS})
    assert cfg.mode.uses_aws and not cfg.mode.may_change_aws


@pytest.mark.parametrize("aws", [{}, {"profile": "x"}, {"region": "us-east-1"}])
def test_aws_modes_need_an_explicit_profile_and_region(aws):
    with pytest.raises(ConfigError, match="needs aws"):
        parse_config({"mode": "READ_ONLY", "aws": aws})


def test_live_without_enable_switch_is_rejected():
    with pytest.raises(ConfigError, match="live.enabled"):
        parse_config({"mode": "LIVE", "aws": AWS})
    with pytest.raises(ConfigError, match="live.enabled"):
        parse_config({"mode": "LIVE", "live": {"enabled": False}, "aws": AWS})
    with pytest.raises(ConfigError):
        ScalixConfig(mode=Mode.LIVE)


LIVE_OK = {"mode": "LIVE", "live": {"enabled": True, "allowed_groups": ["scalix-workers"]},
           "aws": {**AWS, "auto_scaling_group": "scalix-workers"}}


def test_live_with_both_switches_and_an_allowlisted_group_is_accepted():
    cfg = parse_config(LIVE_OK)
    assert cfg.mode.may_change_aws and cfg.live.allowed_groups == ("scalix-workers",)


def test_live_needs_a_group_that_is_on_the_allowlist():
    with pytest.raises(ConfigError, match="auto_scaling_group"):
        parse_config({**LIVE_OK, "aws": AWS})
    with pytest.raises(ConfigError, match="not in live.allowed_groups"):
        parse_config({**LIVE_OK, "live": {"enabled": True, "allowed_groups": []}})
    with pytest.raises(ConfigError, match="not in live.allowed_groups"):
        parse_config({**LIVE_OK, "live": {"enabled": True, "allowed_groups": ["other-group"]}})


def test_live_gate_defaults_are_conservative():
    live = ScalixConfig().live
    assert (live.enabled, live.allowed_groups, live.max_capacity, live.max_step) == (False, (), 2, 1)
    assert live.min_seconds_between_actions == 300
    assert ScalixConfig().controller.policy == "THRESHOLD"


@pytest.mark.parametrize("live, match", [
    ({"allowed_groups": "scalix-workers"}, "list"),
    ({"allowed_groups": ["scalix-*"]}, "exact names"),
    ({"allowed_groups": ["bad;name"]}, "invalid name"),
    ({"max_capacity": 0}, "max_capacity"), ({"max_capacity": 50}, "max_capacity"),
    ({"max_capacity": 2.5}, "max_capacity"), ({"max_step": 0}, "max_step"),
    ({"min_seconds_between_actions": 10}, "min_seconds"), ({"enabled": "yes"}, "true or false"),
])
def test_invalid_live_gates(live, match):
    with pytest.raises(ConfigError, match=match):
        parse_config({"live": live})


@pytest.mark.parametrize("controller, match", [
    ({"policy": "MAGIC"}, "controller.policy"),
    ({"policy_settings": {"max_capacity": 10}}, "may not set capacity"),
    ({"policy_settings": {"worker_count": 10}}, "may not set capacity"),
    ({"policy_settings": "fast"}, "key: value"),
    ({"decision_interval_seconds": 5}, "decision_interval"),
    ({"dry_run": False}, "unknown controller setting"),
])
def test_invalid_controller_settings(controller, match):
    with pytest.raises(ConfigError, match=match):
        parse_config({"controller": controller})


def test_kill_switch(tmp_path):
    from scalix.config import kill_switch_active

    flag = tmp_path / "LIVE_DISABLED"
    assert kill_switch_active(flag, {}) is None
    assert "SCALIX_LIVE_DISABLED" in kill_switch_active(flag, {"SCALIX_LIVE_DISABLED": "1"})
    for off in ("0", "false", "NO", ""):
        assert kill_switch_active(flag, {"SCALIX_LIVE_DISABLED": off}) is None
    flag.touch()
    assert "LIVE_DISABLED" in kill_switch_active(flag, {})


@pytest.mark.parametrize("data, match", [
    ({"mode": "PRODUCTION"}, "unknown mode"),
    ({"mode": 1}, "must be text"),
    ({"mode": "LIVE", "live": {"enabled": "true"}}, "true or false"),
    ({"live": "on"}, "key: value"),
    ({"mdoe": "LIVE"}, "unknown setting"),
    ({"live": {"enabeld": True}}, "unknown live setting"),
    ({"aws": {"secret_access_key": "x"}}, "unknown aws setting"),
    ({"aws": {"region": "Mumbai"}}, "region"),
    ({"aws": {"profile": "bad name; rm -rf"}}, "plain name"),
    ({"aws": {"metric_period_seconds": 30}}, "multiple of 60"),
    ({"aws": {"metric_period_seconds": 300, "max_metric_age_seconds": 120}}, ">= the metric period"),
    (["SIMULATION"], "top level"),
])
def test_invalid_settings_are_rejected(data, match):
    with pytest.raises(ConfigError, match=match):
        parse_config(data)


def test_credentials_cannot_be_put_in_the_config():
    for key in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "password"):
        with pytest.raises(ConfigError, match="unknown aws setting"):
            parse_config({"aws": {key: "anything"}})


def test_missing_for_monitoring_lists_names():
    assert AwsSettings().missing_for_monitoring() == ["aws.profile", "aws.region", "aws.auto_scaling_group"]
    assert AwsSettings(**AWS, auto_scaling_group="g").missing_for_monitoring() == []


def test_merge_settings():
    assert merge_settings({"a": 1, "aws": {"x": 1, "y": 2}}, {"aws": {"y": 3}}) == {"a": 1, "aws": {"x": 1, "y": 3}}
    assert merge_settings(None, {"mode": "SIMULATION"}) == {"mode": "SIMULATION"}
    with pytest.raises(ConfigError):
        merge_settings({"a": 1}, ["b"])


def test_invalid_yaml_is_rejected(tmp_path):
    f = tmp_path / "scalix.yaml"
    f.write_text("mode: [unclosed\n")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(f)


def test_environment_variables_do_not_change_the_mode(monkeypatch, isolated_paths):
    for name in ("SCALIX_MODE", "MODE", "SCALIX_LIVE_ENABLED", "AWS_PROFILE"):
        monkeypatch.setenv(name, "LIVE")
    base, _ = isolated_paths
    base.write_text("mode: SIMULATION\n")
    cfg = load_config()
    assert cfg.mode is Mode.SIMULATION and cfg.aws.profile is None


def test_local_config_file_is_git_ignored():
    import shutil
    import subprocess

    if shutil.which("git") is None or not (PROJECT_ROOT / ".git").exists():
        pytest.skip("git not available")
    assert subprocess.run(["git", "check-ignore", "-q", "--no-index", "config/scalix.local.yaml"],
                          cwd=PROJECT_ROOT).returncode == 0
    assert subprocess.run(["git", "check-ignore", "-q", "--no-index", "config/scalix.local.example.yaml"],
                          cwd=PROJECT_ROOT).returncode == 1
