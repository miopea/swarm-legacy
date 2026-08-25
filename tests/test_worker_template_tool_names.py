"""Bundled worker command/skill templates must name real tools and real args.

The templates installed by :mod:`swarm.hooks.install` instruct a worker to
call MCP tools by name.  Nothing executes them at install time, so a wrong
tool name or a wrong argument name fails *silently* at the point of use --
the worker reads the instruction, the call is rejected, and the surrounding
procedure still reports success.

These tests pin every ``mcp__swarm__*`` reference in the bundled templates
to the live ``TOOLS`` registry the daemon publishes, so drift between the
templates and the tool surface fails here instead of in a worker session.
"""

from __future__ import annotations

import re

import pytest

from swarm.hooks.install import (
    _COMMANDS_SRC_DIR,
    _SKILLS_SRC_DIR,
    WORKER_COMMAND_FILES,
    WORKER_SKILL_NAMES,
)
from swarm.mcp.tools import TOOLS

_TOOL_RE = re.compile(r"mcp__swarm__(\w+)")
# `to=<worker>` and `` `title` = ... `` are both used in the templates.
_ARG_RE = re.compile(r"`([a-z_]+)(?:`\s*=|=)")

_SCHEMAS = {t["name"]: t.get("inputSchema") or {} for t in TOOLS}


def _template_files():
    for fname in WORKER_COMMAND_FILES:
        yield _COMMANDS_SRC_DIR / fname
    for name in WORKER_SKILL_NAMES:
        yield _SKILLS_SRC_DIR / name / "SKILL.md"


def _call_sites():
    """Yield (path, tool_name, argument_names) for each template call site."""
    for path in _template_files():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            for tool in _TOOL_RE.findall(line):
                # Arguments may continue onto following lines (see
                # swarm-handoff's bulleted swarm_create_task call). Stop at a
                # blank line or at the next tool reference.
                block = [line]
                for nxt in lines[i + 1 : i + 6]:
                    if not nxt.strip() or _TOOL_RE.search(nxt):
                        break
                    block.append(nxt)
                text = "\n".join(block)
                args = {a for a in _ARG_RE.findall(text) if a != tool}
                yield path, tool, args


@pytest.mark.parametrize(
    ("path", "tool"),
    [(p, t) for p, t, _ in _call_sites()],
    ids=lambda v: getattr(v, "name", v),
)
def test_template_tool_name_exists(path, tool):
    assert tool in _SCHEMAS, (
        f"{path.name} names tool {tool!r}, which the daemon does not publish. "
        f"A worker reading this finds nothing to call and fails silently."
    )


@pytest.mark.parametrize(
    ("path", "tool", "args"),
    [(p, t, a) for p, t, a in _call_sites() if a],
    ids=lambda v: getattr(v, "name", v),
)
def test_template_argument_names_exist(path, tool, args):
    schema = _SCHEMAS.get(tool)
    assert schema is not None, f"{path.name} names unknown tool {tool!r}"
    accepted = set((schema.get("properties") or {}).keys())
    unknown = args - accepted
    assert not unknown, (
        f"{path.name} passes {sorted(unknown)} to {tool}, which accepts "
        f"{sorted(accepted)}. The call is rejected and nothing is done."
    )


def test_allowed_tools_frontmatter_names_real_tools():
    """Skill frontmatter allow-lists must not pin dead tool names either."""
    for name in WORKER_SKILL_NAMES:
        path = _SKILLS_SRC_DIR / name / "SKILL.md"
        for tool in _TOOL_RE.findall(path.read_text()):
            assert tool in _SCHEMAS, f"{name}/SKILL.md allow-lists unknown tool {tool!r}"
