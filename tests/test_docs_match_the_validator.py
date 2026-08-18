"""The docs in this repo describe rules that live in aw-workspace's code.

`docs/contributing-tasks.md` stated that `agent_slug` was required only for
`agent_prompt`. Core's `src/apps/manifest.py` has required it for
`agentic_output` too since the tasks app's runner started resolving the agent
before running the command. An app built from this template hit that as an
install refusal quoting a field the documentation never mentioned.

A doc and a validator in different repos will drift; the only question is
whether anyone finds out. This test is how.
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _core_manifest_source():
    """aw-workspace's validator, from a sibling checkout."""
    path = os.path.normpath(os.path.join(REPO, "..", "..", "src", "apps", "manifest.py"))
    if not os.path.isfile(path):
        path = os.path.normpath(os.path.join(REPO, "..", "aw-workspace",
                                             "src", "apps", "manifest.py"))
    if not os.path.isfile(path):
        pytest.skip("aw-workspace not checked out next to this repo")
    return open(path).read()


def test_task_types_requiring_agent_slug_match_the_docs():
    source = _core_manifest_source()
    m = re.search(r'if task_type in \(([^)]*)\):\s*\n\s*if not str\(task\.get\("agent_slug"',
                  source)
    assert m, "core's agent_slug rule moved — this test needs updating with it"
    enforced = set(re.findall(r'"([a-z_]+)"', m.group(1)))

    doc = open(os.path.join(REPO, "docs", "contributing-tasks.md")).read()

    # Parse a declared list, not prose. Two prose-matching versions of this
    # test passed against the exact wording they existed to reject: a
    # paragraph split lumped the whole task-type bullet list together, and a
    # sentence split still matched the "Fields:" line, which names
    # `agentic_output` for the `command` field and `agent_slug` for another.
    # Co-occurrence is not a claim. This line is.
    m = re.search(r"<!--\s*agent_slug-required:\s*([^>]+?)\s*-->", doc)
    assert m, ("docs/contributing-tasks.md must carry an "
               "`<!-- agent_slug-required: ... -->` line for this to be checkable")
    documented = {t.strip() for t in m.group(1).split(",") if t.strip()}

    assert documented == enforced, (
        f"the docs claim agent_slug is required for {sorted(documented)}, core "
        f"enforces it for {sorted(enforced)} — an app author meets the "
        f"difference as an install refused over a field the page never "
        f"mentioned"
    )
    # and the prose has to actually say it, not just the machine-readable line
    for t in sorted(enforced):
        assert f"`{t}`" in doc, t


def test_the_docs_do_not_promise_a_default_that_core_does_not_give():
    """`enabled` defaults to false. If that ever flips in core, an app author
    reading this page would ship a schedule that starts firing on install."""
    doc = open(os.path.join(REPO, "docs", "contributing-tasks.md")).read()
    assert "defaults to `false`" in doc
