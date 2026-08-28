"""Take Swarm (legacy) off a machine, in the one order that works.

There was no ``uninstall`` command until 2026.8.28.  ``SwarmCLI`` routes an
unrecognised word to ``start`` as a target, so typing ``swarm uninstall``
*started a daemon* and then failed against the daemon already running —
which is what the operator saw, twice, before it restarted itself.

Ordering
--------

The systemd unit goes **first**, and everything else follows.  The unit
carries ``Restart=always``, so a run that kills the daemon before removing
the unit is undone by systemd five seconds later: the operator watches the
thing they just uninstalled come back, with no error to explain it.  Remove
the unit and the daemon has nothing to resurrect it.

What this does not do
---------------------

It does not remove the package.  ``uv`` owns the ``swarm`` and
``swarm-legacy`` entrypoints and one of them is the process running this
code; deleting it mid-run is a way to fail halfway with no way to retry.
The last step is printed for the operator to run instead.

It does not delete the state directory unless asked.  ``swarm.db`` is the
entire history of the hive — every task, every decision — and an uninstall
is not a request to destroy it.  ``--purge`` is that request.
"""

from __future__ import annotations

import os
import shutil
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from swarm.logging import get_logger
from swarm.paths import state_dir
from swarm.relocate import (
    LiveProcess,
    _entrypoint_candidates,
    _pid_alive,
    _unit_is_active,
    dir_size_bytes,
    find_live_processes,
)

_log = get_logger("uninstall")

PACKAGE = "swarm-ai"
"""The distribution name, which is *not* either entrypoint's name.

``uv tool uninstall swarm-legacy`` fails with "not installed"; the package
has been ``swarm-ai`` on PyPI throughout, and the renames only ever moved
the console scripts.  Printed rather than guessed at.
"""


@dataclass
class UninstallPlan:
    """What removing this install would touch, resolved against the machine."""

    unit_name: str
    unit_path: Path | None
    unit_active: bool
    unit_is_ours: bool
    state: Path
    state_bytes: int | None
    live: list[LiveProcess] = field(default_factory=list)
    entrypoints: list[Path] = field(default_factory=list)
    package: str = PACKAGE

    @property
    def state_exists(self) -> bool:
        return self.state.is_dir()


def plan() -> UninstallPlan:
    """Resolve what an uninstall would do, touching nothing."""
    from swarm.service import _unit_is_ours, current_unit_name, current_unit_path

    unit_name = current_unit_name()
    unit_path = current_unit_path()
    installed = unit_path.exists()
    state = state_dir()
    return UninstallPlan(
        unit_name=unit_name,
        unit_path=unit_path if installed else None,
        unit_active=_unit_is_active(unit_name),
        # A unit by our name that something else installed.  Reported so the
        # operator is told we are leaving it, rather than being told there
        # was nothing there.
        unit_is_ours=_unit_is_ours(unit_path.read_text()) if installed else True,
        state=state,
        state_bytes=dir_size_bytes(state),
        live=find_live_processes(state),
        entrypoints=_entrypoint_candidates(),
    )


def _terminate(proc: LiveProcess, *, timeout: float = 5.0) -> str:
    """SIGTERM, wait, then SIGKILL.  Returns what happened, for reporting."""
    try:
        os.kill(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return f"{proc.kind} (PID {proc.pid}) had already exited"
    except OSError as exc:
        _log.warning("could not signal %s PID %d: %s", proc.kind, proc.pid, exc)
        return f"could not stop {proc.kind} (PID {proc.pid}): {exc}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(proc.pid):
            return f"Stopped {proc.kind} (PID {proc.pid})"
        time.sleep(0.2)
    try:
        os.kill(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.3)
    if _pid_alive(proc.pid):
        return f"{proc.kind} (PID {proc.pid}) did not stop"
    return f"Killed {proc.kind} (PID {proc.pid}) after {timeout:.0f}s"


def perform(plan_: UninstallPlan, *, purge: bool = False) -> list[str]:
    """Carry out *plan_*, returning one line per step actually taken.

    Idempotent throughout: a half-finished uninstall is finished by running
    the command again, not by unpicking it by hand.
    """
    from swarm.service import uninstall_service

    steps: list[str] = []

    # 1. systemd FIRST — see the module docstring.  Nothing below this line
    #    is safe while a unit with Restart=always is still installed.
    if uninstall_service():
        steps.append(f"Removed {plan_.unit_name}")
    elif plan_.unit_path is not None and not plan_.unit_is_ours:
        steps.append(
            f"Left {plan_.unit_name} alone — it does not launch Swarm (legacy), "
            "so it is not ours to remove"
        )
    else:
        steps.append(f"No {plan_.unit_name} to remove")

    # 2. Whatever is still running, now that nothing will restart it.  The
    #    plan's list is re-read: stopping the unit may already have taken
    #    the daemon down, and reporting a kill that did not happen is worse
    #    than saying nothing.
    for proc in find_live_processes(plan_.state):
        steps.append(_terminate(proc))

    # 3. State, only on request.
    if purge:
        if plan_.state.is_dir():
            try:
                shutil.rmtree(plan_.state)
                steps.append(f"Deleted {plan_.state}")
            except OSError as exc:
                _log.warning("could not delete %s: %s", plan_.state, exc)
                steps.append(f"Could not delete {plan_.state}: {exc}")
        else:
            steps.append(f"No state directory at {plan_.state}")
    return steps
