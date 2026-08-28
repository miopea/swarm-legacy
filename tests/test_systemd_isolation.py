"""The suite must not be able to touch the operator's real systemd (2026-08-28).

WHAT HAPPENED. A test needed to reach past its own fixture's stub, and used
``monkeypatch.undo()`` to do it. ``undo()`` does not revert one patch — it
reverts every patch monkeypatch has recorded in scope, including
``conftest._isolate_systemd_unit``, which exists to keep the suite away from
``~/.config/systemd/user``. The test then invoked ``swarm-legacy uninstall
--yes`` through the CLI runner. With the isolation gone, that call ran against
the real machine: it stopped the operator's live ``swarm-legacy.service`` at
20:46:13 and deleted the unit file. The hive was down for eight minutes.

The database survived, but only by luck of design — uninstall keeps state
unless ``--purge`` is passed. Nothing about the test said "this is destructive".

WHY THE EXISTING GUARD WAS NOT ENOUGH, and this is the general lesson: an
isolation fixture built out of monkeypatch protects against tests that do not
know about it, and not against tests that step around it. ``undo()`` is a
supported, ordinary-looking API. Any test may call it. So the last line of
defence cannot itself be a monkeypatch — it has to be something undo() has no
record of, which is a plain module-level rebind in conftest.

These tests assert the property, not the mechanism: after the most aggressive
un-patching a test can perform, systemctl still cannot be reached and unit
writes still cannot land in the operator's systemd directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import swarm.relocate as _relocate
import swarm.service as _service


class TestTheGuardSurvivesUndo:
    def test_systemctl_is_unreachable_after_undo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact escape that took the hive down."""
        monkeypatch.undo()

        result = _service._systemctl("stop", "swarm-legacy.service")

        assert _service._systemctl.__name__ == "_refuse_systemctl", (
            "a test can reach the real systemctl again — the conftest rebind is gone"
        )
        assert result.returncode == 0

    def test_relocate_systemctl_is_unreachable_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``relocate`` owns a second, separate systemctl wrapper."""
        monkeypatch.undo()

        assert _relocate._systemctl.__name__ == "_refuse_systemctl"

    def test_the_unit_path_is_never_the_operators(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``uninstall_service`` unlinks this path. It must not be the real one."""
        monkeypatch.undo()

        real = Path.home() / ".config" / "systemd" / "user"
        assert real not in _service._SERVICE_PATH.parents, (
            f"unit operations would land in {real} — the operator's own systemd"
        )
        assert real not in _service.current_unit_path().parents

    def test_a_full_uninstall_after_undo_touches_nothing_real(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """End to end, at the level that actually caused the incident.

        Runs the same call the escaped test ran, with the same un-patching,
        and asserts the operator's unit directory is untouched afterwards.
        """
        from click.testing import CliRunner

        from swarm.cli import main

        real_dir = Path.home() / ".config" / "systemd" / "user"
        before = sorted(p.name for p in real_dir.glob("*")) if real_dir.is_dir() else []

        monkeypatch.undo()
        monkeypatch.setenv("SWARM_STATE_DIR", str(tmp_path / "state"))
        CliRunner().invoke(main, ["uninstall", "--yes"])

        after = sorted(p.name for p in real_dir.glob("*")) if real_dir.is_dir() else []
        assert after == before, "the uninstall removed something from the operator's systemd"
