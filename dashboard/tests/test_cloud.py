import base64
import hashlib
import hmac

import pandas as pd
import pytest

from scalix_dash.cloud import MockCloudProvider, ProviderSafetyError, VmOperation, operations_from_events, replay
from scalix_dash.cloud.cloudstack import CloudStackConfig, CloudStackProvider, sign

SETTINGS = {"cloudstack": {"enabled": True, "zone_id": "z1", "template_id": "t1", "service_offering_id": "s1"}}
ENV = {"CLOUDSTACK_ENDPOINT": "https://cloud.example/client/api", "CLOUDSTACK_API_KEY": "KEY123",
       "CLOUDSTACK_SECRET_KEY": "SECRET456"}


def events(deltas):
    return pd.DataFrame({"time_s": [15.0 * (i + 1) for i in range(len(deltas))], "applied_delta": deltas,
                         "reason": [f"r{i}" for i in range(len(deltas))], "run_id": "run-1"})


# ------------------------------------------------------------------ mock adapter

def test_mock_records_operations_and_never_provisions():
    p = MockCloudProvider(initial_vms=2)
    assert p.can_provision_real_resources is False
    rec = p.apply(VmOperation("SCALE_OUT", 3, "busy"))
    assert rec.executed is False and rec.outcome == "recorded"
    assert rec.detail["would_create"] == ["mock-vm-0003", "mock-vm-0004", "mock-vm-0005"]
    rec = p.apply(VmOperation("SCALE_IN", 2, "idle"))
    assert rec.detail["would_destroy"] == ["mock-vm-0005", "mock-vm-0004"]     # newest first
    assert len(p.vms) == 3 and len(p.records) == 2


def test_mock_never_removes_last_vm():
    p = MockCloudProvider(initial_vms=2, min_vms=1)
    rec = p.apply(VmOperation("SCALE_IN", 5))
    assert rec.outcome == "partially refused" and len(p.vms) == 1
    assert p.apply(VmOperation("SCALE_IN", 1)).outcome == "refused" and len(p.vms) == 1


def test_mock_and_operation_validation():
    with pytest.raises(ValueError):
        MockCloudProvider(min_vms=0)
    with pytest.raises(ValueError):
        MockCloudProvider(initial_vms=1, min_vms=2)
    with pytest.raises(ValueError):
        VmOperation("RESIZE", 1)
    with pytest.raises(ValueError):
        VmOperation("SCALE_OUT", 0)


def test_replay_engine_events_in_time_order():
    ev = events([0, 2, 0, -1]).iloc[::-1]              # out of order on purpose
    ops = operations_from_events(ev)
    assert [(o.kind, o.count, o.sim_time_s) for o in ops] == [("SCALE_OUT", 2, 30.0), ("SCALE_IN", 1, 60.0)]
    p = MockCloudProvider(initial_vms=4)
    recs = replay(ev, p)
    assert len(recs) == 2 and len(p.vms) == 5
    assert recs[0].as_row()["run_id"] == "run-1"
    assert operations_from_events(pd.DataFrame()) == []


# ------------------------------------------------------------------ CloudStack adapter (safety)

def test_cloudstack_disabled_by_default_only_plans():
    cfg = CloudStackConfig.from_settings({}, env=ENV)
    assert cfg.enabled is False
    calls = []
    p = CloudStackProvider(cfg, confirm=True, transport=calls.append)
    rec = p.apply(VmOperation("SCALE_OUT", 2))
    assert rec.outcome == "planned" and rec.executed is False and calls == []
    assert [c["command"] for c in rec.detail["commands"]] == ["deployVirtualMachine"] * 2


def test_cloudstack_requires_operator_confirmation():
    cfg = CloudStackConfig.from_settings(SETTINGS, env=ENV)
    p = CloudStackProvider(cfg, confirm=False, transport=lambda url: {})
    with pytest.raises(ProviderSafetyError, match="confirmation"):
        p.apply(VmOperation("SCALE_OUT", 1))


def test_cloudstack_requires_credentials_from_environment():
    cfg = CloudStackConfig.from_settings(SETTINGS, env={})
    assert set(cfg.missing()) == {"endpoint", "api key", "secret key"}
    with pytest.raises(ProviderSafetyError, match="not fully configured"):
        CloudStackProvider(cfg, confirm=True, transport=lambda url: {}).apply(VmOperation("SCALE_OUT", 1))


def test_cloudstack_execution_with_fake_transport_and_redaction():
    urls = []

    def transport(url):
        urls.append(url)
        if "deployVirtualMachine" in url:
            return {"deployvirtualmachineresponse": {"id": f"vm-{len(urls)}", "jobid": "j"}}
        return {"destroyvirtualmachineresponse": {"jobid": "j2", "apikey": "echoed-secret"}}

    cfg = CloudStackConfig.from_settings(SETTINGS, env=ENV)
    p = CloudStackProvider(cfg, confirm=True, transport=transport)
    rec = p.apply(VmOperation("SCALE_OUT", 2))
    assert rec.executed and rec.outcome == "ok" and p.created_vm_ids == ["vm-1", "vm-2"]
    assert all(u.startswith("https://cloud.example/client/api?") and "signature=" in u for u in urls)
    # SCALE_IN destroys only adapter-created VMs, newest first, and never the last one.
    rec = p.apply(VmOperation("SCALE_IN", 1))
    assert rec.detail["commands"][0]["id"] == "vm-2" and p.created_vm_ids == ["vm-1"]
    assert rec.detail["responses"][0]["destroyvirtualmachineresponse"]["apikey"] == "<redacted>"
    refused = p.apply(VmOperation("SCALE_IN", 1))
    assert refused.outcome == "refused" and p.created_vm_ids == ["vm-1"]
    # Secrets never appear in records or the config repr.
    text = str([r.as_row() for r in p.records]) + repr(cfg) + str(cfg)
    assert "SECRET456" not in text and "KEY123" not in text


def test_signature_algorithm():
    params = {"command": "listZones", "apikey": "abc", "response": "json", "name": "a b"}
    query = "apikey=abc&command=listzones&name=a%20b&response=json"
    expected = base64.b64encode(hmac.new(b"s3cr3t", query.encode(), hashlib.sha1).digest()).decode()
    assert sign(params, "s3cr3t") == expected
    assert sign(dict(reversed(list(params.items()))), "s3cr3t") == expected      # order-independent
    assert sign(params, "other") != expected
