"""Apache CloudStack adapter (optional, disabled by default).

Mapping of SCALIX decisions to the CloudStack API:

* SCALE_OUT n -> n x ``deployVirtualMachine`` (zoneid, templateid, serviceofferingid, group=<vm_group>)
* SCALE_IN n  -> n x ``destroyVirtualMachine`` (id=<newest VM SCALIX created>, expunge=true)

Safety rules enforced here:

1. Without ``enabled: true`` in settings, every call is only *planned* (returned, not sent).
2. Endpoint, API key and secret come only from environment variables; they are never stored in
   settings files, records or logs (records list parameters without ``apikey``/``signature``).
3. Executing requires ``confirm=True`` from the operator for each provider instance.
4. SCALE_IN only destroys VMs this adapter created itself, never pre-existing VMs, never the last one.

This adapter has been unit-tested with a fake transport only. It has NOT been run against a live
CloudStack deployment; asynchronous job polling (``queryAsyncJobResult``) is not implemented.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .base import CloudProvider, OperationRecord, ProviderSafetyError, VmOperation

Transport = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class CloudStackConfig:
    enabled: bool
    endpoint: str
    api_key: str
    secret_key: str
    zone_id: str
    template_id: str
    service_offering_id: str
    vm_group: str = "scalix"

    @classmethod
    def from_settings(cls, cloud_settings: dict[str, Any], env: dict[str, str] | None = None) -> "CloudStackConfig":
        env = os.environ if env is None else env
        cs = dict((cloud_settings or {}).get("cloudstack") or {})
        return cls(
            enabled=bool(cs.get("enabled", False)),
            endpoint=env.get(cs.get("endpoint_env", "CLOUDSTACK_ENDPOINT"), ""),
            api_key=env.get(cs.get("api_key_env", "CLOUDSTACK_API_KEY"), ""),
            secret_key=env.get(cs.get("secret_key_env", "CLOUDSTACK_SECRET_KEY"), ""),
            zone_id=str(cs.get("zone_id", "")),
            template_id=str(cs.get("template_id", "")),
            service_offering_id=str(cs.get("service_offering_id", "")),
            vm_group=str(cs.get("vm_group", "scalix")),
        )

    def missing(self) -> list[str]:
        names = {"endpoint": self.endpoint, "api key": self.api_key, "secret key": self.secret_key,
                 "zone_id": self.zone_id, "template_id": self.template_id,
                 "service_offering_id": self.service_offering_id}
        return [k for k, v in names.items() if not v]

    def __repr__(self) -> str:  # never print secrets
        return (f"CloudStackConfig(enabled={self.enabled}, endpoint={'<set>' if self.endpoint else '<unset>'}, "
                f"api_key={'<redacted>' if self.api_key else '<unset>'}, secret_key=<redacted>, "
                f"zone_id={self.zone_id!r}, template_id={self.template_id!r}, "
                f"service_offering_id={self.service_offering_id!r}, vm_group={self.vm_group!r})")

    __str__ = __repr__


def sign(params: dict[str, str], secret_key: str) -> str:
    """CloudStack request signature: sort by key, URL-encode values, lowercase, HMAC-SHA1, base64."""
    query = "&".join(f"{k}={urllib.parse.quote(str(params[k]), safe='*')}" for k in sorted(params, key=str.lower))
    digest = hmac.new(secret_key.encode("utf-8"), query.lower().encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _default_transport(url: str) -> dict[str, Any]:  # pragma: no cover - requires a live server
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


class CloudStackProvider(CloudProvider):
    name = "cloudstack"
    can_provision_real_resources = True

    def __init__(self, config: CloudStackConfig, confirm: bool = False, transport: Transport | None = None):
        self.config = config
        self.confirm = confirm
        self._transport = transport or _default_transport
        self.created_vm_ids: list[str] = []
        self.records: list[OperationRecord] = []

    # -------------------------------------------------------------- planning

    def plan(self, op: VmOperation) -> list[dict[str, str]]:
        """API commands (without credentials) that ``op`` maps to."""
        if op.kind == "SCALE_OUT":
            return [{"command": "deployVirtualMachine", "zoneid": self.config.zone_id,
                     "templateid": self.config.template_id, "serviceofferingid": self.config.service_offering_id,
                     "group": self.config.vm_group, "response": "json"} for _ in range(op.count)]
        targets = list(reversed(self.created_vm_ids))[:op.count]
        targets += [f"<newest {self.config.vm_group} VM #{i + 1}>" for i in range(op.count - len(targets))]
        return [{"command": "destroyVirtualMachine", "id": vid, "expunge": "true", "response": "json"} for vid in targets]

    # -------------------------------------------------------------- execution

    def scale_out(self, op: VmOperation) -> OperationRecord:
        return self._handle(op)

    def scale_in(self, op: VmOperation) -> OperationRecord:
        return self._handle(op)

    def _handle(self, op: VmOperation) -> OperationRecord:
        commands = self.plan(op)
        if not self.config.enabled:
            return self._record(op, False, "planned", {"commands": commands,
                                                        "note": "CloudStack disabled in settings; nothing sent"})
        self._check_can_execute()
        if op.kind == "SCALE_IN":
            # Only VMs created by this adapter may be destroyed, and never all of them.
            removable = max(0, len(self.created_vm_ids) - 1)
            if op.count > removable:
                return self._record(op, False, "refused", {
                    "commands": commands,
                    "note": f"only {removable} adapter-created VM(s) can be removed without removing the last one"})
        responses = []
        for cmd in commands:
            resp = self._call(cmd)
            responses.append(_redact(resp))
            if cmd["command"] == "deployVirtualMachine":
                vm_id = _deep_get(resp, "id")
                if vm_id:
                    self.created_vm_ids.append(str(vm_id))
            else:
                if cmd["id"] in self.created_vm_ids:
                    self.created_vm_ids.remove(cmd["id"])
        return self._record(op, True, "ok", {"commands": commands, "responses": responses})

    def _check_can_execute(self) -> None:
        missing = self.config.missing()
        if missing:
            raise ProviderSafetyError("CloudStack is enabled but not fully configured; missing: " + ", ".join(missing))
        if not self.confirm:
            raise ProviderSafetyError("Real CloudStack operations require operator confirmation (confirm=True)")

    def _call(self, command: dict[str, str]) -> dict[str, Any]:
        params = dict(command, apikey=self.config.api_key)
        signature = sign(params, self.config.secret_key)
        query = urllib.parse.urlencode(sorted(params.items())) + "&signature=" + urllib.parse.quote(signature, safe="")
        return self._transport(self.config.endpoint.rstrip("?") + "?" + query)

    def _record(self, op: VmOperation, executed: bool, outcome: str, detail: dict) -> OperationRecord:
        rec = OperationRecord(op, self.name, executed=executed, outcome=outcome, detail=detail)
        self.records.append(rec)
        return rec


def _deep_get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _deep_get(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _deep_get(v, key)
            if found is not None:
                return found
    return None


def _redact(obj: Any) -> Any:
    sensitive = {"apikey", "secretkey", "signature", "password"}
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k.lower() in sensitive else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj
