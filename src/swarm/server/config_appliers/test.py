"""``test`` section applier — swarm-legacy test harness configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from swarm.server.config_manager import FieldOutcome, _apply_dataclass_dict

if TYPE_CHECKING:
    from swarm.config import HiveConfig
    from swarm.server.config_appliers._base import ApplierDeps


# Test keys with bespoke range / non-empty validation.  Generic
# dispatch handles ``enabled`` (boolean) which was silently dropped by
# the previous cherry-pick implementation — the audit caught it as a
# Bug C class drift.
_CUSTOM_KEYS: frozenset[str] = frozenset(
    {"port", "auto_resolve_delay", "auto_complete_min_idle", "report_dir"}
)


def apply_test(
    cfg: HiveConfig,
    body: dict[str, Any],
    *,
    deps: ApplierDeps,  # protocol-uniform; test doesn't use it
) -> FieldOutcome:
    """Validate and apply the ``test`` section of a config update."""
    tc = cfg.test
    consumed_custom: list[str] = []
    for key in _CUSTOM_KEYS:
        if key in body:
            consumed_custom.append(key)
    if "port" in body:
        val = body["port"]
        if not isinstance(val, int) or not (1024 <= val <= 65535):
            raise ValueError("test.port must be an integer between 1024 and 65535")
        tc.port = val
    if "auto_resolve_delay" in body:
        val = body["auto_resolve_delay"]
        if not isinstance(val, (int, float)) or val < 0:
            raise ValueError("test.auto_resolve_delay must be >= 0")
        tc.auto_resolve_delay = float(val)
    if "auto_complete_min_idle" in body:
        val = body["auto_complete_min_idle"]
        if not isinstance(val, (int, float)) or val < 1:
            raise ValueError("test.auto_complete_min_idle must be >= 1")
        tc.auto_complete_min_idle = float(val)
    if "report_dir" in body:
        val = body["report_dir"]
        if not isinstance(val, str) or not val.strip():
            raise ValueError("test.report_dir must be a non-empty string")
        tc.report_dir = val.strip()
    # Generic dispatch covers ``enabled`` and any future TestConfig
    # field added without updating this handler.  Also emits the
    # unknown-sub-key WARNING for drift detection.
    outcome = _apply_dataclass_dict(body, tc, "test", skip_keys=_CUSTOM_KEYS)
    outcome.consumed.extend(consumed_custom)
    return outcome
