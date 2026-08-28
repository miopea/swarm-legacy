"""Tests for uninstall.py — taking Swarm (legacy) off a machine.

The bug these exist for: ``swarm-legacy uninstall`` was not a command, so
``SwarmCLI`` routed it to ``start`` and it launched a daemon, which then
failed against the daemon it was competing with.  Stopping the daemon first
did not help either — the systemd unit carries ``Restart=always`` and put it
straight back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from swarm import uninstall as un
from swarm.cli import main
from swarm.relocate import LiveProcess


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A pretend install: a state directory and a unit file, both in tmp_path."""
    state = tmp_path / ".swarm-legacy"
    state.mkdir()
    (state / "swarm.db").write_bytes(b"x" * 2048)
    # conftest's _isolate_systemd_unit already owns this directory.
    unit_dir = tmp_path / "systemd-user"
    unit_dir.mkdir(exist_ok=True)
    unit = unit_dir / "swarm-legacy.service"
    unit.write_text("[Service]\nRestart=always\n")

    monkeypatch.setenv("SWARM_STATE_DIR", str(state))
    monkeypatch.setattr("swarm.service.current_unit_name", lambda: "swarm-legacy.service")
    monkeypatch.setattr("swarm.service.current_unit_path", lambda: unit)
    monkeypatch.setattr("swarm.service._systemctl", lambda *a: None)
    monkeypatch.setattr(un, "_unit_is_active", lambda _n: True)
    # Stub the directory scan, not the function under test, so a test that
    # cares about entrypoint reporting can supply its own bin directory.
    monkeypatch.setattr(un, "_shim_directories", list)
    monkeypatch.setattr(un, "find_live_processes", lambda _s: [])
    return {"state": state, "unit": unit}


class TestPlan:
    def test_reports_the_unit_and_the_state_it_would_touch(self, install) -> None:
        plan = un.plan()

        assert plan.unit_name == "swarm-legacy.service"
        assert plan.unit_path == install["unit"]
        assert plan.unit_active is True
        assert plan.state == install["state"]
        assert plan.state_bytes == 2048

    def test_reports_no_unit_when_none_is_installed(self, install) -> None:
        install["unit"].unlink()

        assert un.plan().unit_path is None

    def test_the_package_name_is_not_either_entrypoint(self) -> None:
        """``uv tool uninstall swarm-legacy`` fails; the package is swarm-ai."""
        assert un.PACKAGE == "swarm-ai"


class TestPerform:
    def test_the_unit_goes_before_the_processes(self, install, monkeypatch) -> None:
        """Order is the whole point: Restart=always undoes any other order.

        Kill the daemon while the unit is still installed and systemd brings
        it back five seconds later, so the uninstall appears to do nothing.
        """
        order: list[str] = []
        monkeypatch.setattr(
            "swarm.service.uninstall_service",
            lambda: (order.append("unit"), True)[1],
        )
        monkeypatch.setattr(
            un, "find_live_processes", lambda _s: [LiveProcess("daemon", 4242, "holds swarm.db")]
        )
        monkeypatch.setattr(un, "_terminate", lambda p, **k: order.append("kill") or "Stopped")

        un.perform(un.plan())

        assert order == ["unit", "kill"]

    def test_state_survives_by_default(self, install) -> None:
        un.perform(un.plan())

        assert (install["state"] / "swarm.db").exists()

    def test_purge_deletes_the_state_directory(self, install) -> None:
        steps = un.perform(un.plan(), purge=True)

        assert not install["state"].exists()
        assert any("Deleted" in s for s in steps)

    def test_running_twice_is_not_an_error(self, install) -> None:
        un.perform(un.plan(), purge=True)
        steps = un.perform(un.plan(), purge=True)

        assert any("No state directory" in s for s in steps)


class TestCommand:
    def test_it_is_a_real_command_now(self, runner, install) -> None:
        """Not routed to ``start``: the daemon must not come up."""
        result = runner.invoke(main, ["uninstall"], input="n\n")

        assert result.exit_code == 0, result.output
        assert "Another" not in result.output  # the daemon-already-running error

    def test_declining_changes_nothing(self, runner, install) -> None:
        result = runner.invoke(main, ["uninstall"], input="n\n")

        assert "Nothing was changed." in result.output
        assert install["unit"].exists()

    def test_it_names_the_last_step_the_operator_must_run(self, runner, install) -> None:
        result = runner.invoke(main, ["uninstall", "--yes"])

        assert "uv tool uninstall swarm-ai" in result.output

    def test_it_points_at_swarm_next(self, runner, install) -> None:
        result = runner.invoke(main, ["uninstall", "--yes"])

        assert "github.com/miopea/swarm-next" in result.output

    def test_purge_is_spelled_out_before_it_is_done(self, runner, install) -> None:
        result = runner.invoke(main, ["uninstall", "--purge"], input="n\n")

        assert "DELETE" in result.output
        assert "swarm.db" in result.output
        assert install["state"].exists()


class TestForeignUnit:
    """A unit by our name that Swarm Next installed is reported, not removed."""

    @pytest.fixture
    def foreign(self, install):
        install["unit"].write_text(
            "[Unit]\nDescription=Swarm application and web UI\n"
            "[Service]\nExecStart=/home/u/.local/lib/swarm/current/bin/swarm-api\n"
        )
        return install

    def test_the_plan_says_it_is_not_ours(self, foreign) -> None:
        assert un.plan().unit_is_ours is False

    def test_performing_says_so_rather_than_nothing_to_remove(self, foreign) -> None:
        steps = un.perform(un.plan())

        assert any("not ours to remove" in s for s in steps)
        assert foreign["unit"].exists()

    def test_the_prompt_warns_before_the_operator_agrees(self, runner, foreign) -> None:
        result = runner.invoke(main, ["uninstall"], input="n\n")

        assert "LEAVE" in result.output


class TestTheReportMatchesThePlan:
    """Everything the plan promised must be accounted for in the result.

    Seen in the first real run: the plan said it would stop the daemon
    (PID 3359646) and the results never mentioned it again. Nothing was
    wrong — `systemctl stop` had already taken it, and `perform` re-reads
    the live list rather than claim a kill it did not make — but "we will
    stop X" followed by silence about X reads as a step that failed.
    """

    def test_a_process_the_unit_took_down_is_still_reported(self, install, monkeypatch) -> None:
        daemon = LiveProcess("daemon", 4242, "holds swarm.db and the listen port")
        monkeypatch.setattr(un, "find_live_processes", lambda _s: [daemon])
        plan = un.plan()
        # Removing the unit stops the daemon, so by step 2 it is gone.
        monkeypatch.setattr(un, "find_live_processes", lambda _s: [])

        steps = un.perform(plan)

        assert any("daemon" in s and "4242" in s for s in steps), (
            "the plan promised to stop the daemon and the result never mentioned it"
        )

    def test_it_does_not_invent_a_kill_it_did_not_make(self, install, monkeypatch) -> None:
        daemon = LiveProcess("daemon", 4242, "holds swarm.db")
        monkeypatch.setattr(un, "find_live_processes", lambda _s: [daemon])
        plan = un.plan()
        monkeypatch.setattr(un, "find_live_processes", lambda _s: [])

        steps = un.perform(plan)

        assert not any("Stopped daemon" in s for s in steps)


class TestItNamesEveryEntrypoint:
    """`uv tool uninstall swarm-ai` removes both shims, so say both.

    The first real run printed "(that is what removes swarm)" while the
    command it was describing removed `swarm` AND `swarm-legacy` — including
    the one the operator had just typed.
    """

    def test_both_shims_are_reported(self, install, monkeypatch, tmp_path, runner) -> None:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "swarm").write_text("#!/bin/sh\n")
        (bindir / "swarm-legacy").write_text("#!/bin/sh\n")
        monkeypatch.setattr(un, "_shim_directories", lambda: [bindir])

        result = runner.invoke(main, ["uninstall", "--yes"])

        assert "swarm, swarm-legacy" in result.output
