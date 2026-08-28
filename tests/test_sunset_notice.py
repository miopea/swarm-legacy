"""The handover notice: Legacy telling its operator where the work moved.

WHY IT IS IN THE DASHBOARD AND NOT THE README. The people still running this
have a browser tab open to it and no reason to re-read a README they followed
once. A repo notice reaches whoever is deciding to install; a dashboard notice
reaches whoever is already running it, which is the whole remaining audience.

WHY IT COMES BACK EVERY SESSION. Every other banner on this page describes a
condition the operator can clear — an update to take, a name to free, a holder
to bounce — so a permanent dismissal is reasonable for those. This one
describes a permanent fact about the software. Dismissed for good, an operator
who clicked once would never see it again, which is indistinguishable from
never having been told.

THE ACCURACY CONSTRAINT IS THE POINT. A handover notice that oversells the
replacement costs more than no notice: an operator who migrates expecting their
skills and approval rules to follow finds out afterwards. Swarm Next's
migration bundle carries workers and tasks. It does not carry skills, groups,
approval rules or identity files, and the notice says so in as many words.
These tests exist to keep it saying so.
"""

from __future__ import annotations

from pathlib import Path

_HTML = Path("src/swarm/web/templates/dashboard.html").read_text()
_BASE = Path("src/swarm/web/templates/base.html").read_text()
_JS = Path("src/swarm/web/static/dashboard.js").read_text()


def _js_without_comments() -> str:
    """Whole-line ``//`` comments dropped.

    The comment above this feature explains the choice by naming the thing it
    rejected — "sessionStorage, not localStorage". A scan over raw source
    therefore matches the prose that says localStorage is wrong and reports it
    as localStorage being used. ``test_dashboard_panel_mode`` records this
    happening five separate times in this repo; it happened again here.
    """
    return "\n".join(
        line for line in _JS.split("\n") if not line.lstrip().startswith(("//", "/*", "*", "*/"))
    )


class TestTheBannerIsPresentAndFirst:
    def test_the_banner_exists(self) -> None:
        assert 'id="sunset-banner"' in _HTML

    def test_it_says_the_thing_plainly(self) -> None:
        assert "no longer maintained" in _HTML
        assert "Swarm Next" in _HTML

    def test_it_says_the_hive_keeps_working(self) -> None:
        """The first question a banner like this raises is "am I about to lose
        this?".  Answering it in the banner costs one clause."""
        assert "nothing here stops working" in _HTML

    def test_it_is_the_first_banner_in_the_stack(self) -> None:
        """Below a transient banner it reads as another passing warning.

        It is the only one of the four that is true on every load forever.
        """
        sunset = _HTML.index('id="sunset-banner"')
        for other in ("update-banner", "holder-drift-banner", "relocate-banner"):
            assert sunset < _HTML.index(f'id="{other}"'), f"{other} precedes the notice"

    def test_both_buttons_are_wired(self) -> None:
        assert 'data-action="showSunsetNotice"' in _HTML
        assert 'data-action="dismissSunsetBanner"' in _HTML


class TestTheDetail:
    def test_the_modal_exists_and_is_dismissible(self) -> None:
        assert 'id="sunset-modal"' in _HTML
        assert 'data-modal-dismiss="hideSunsetNotice"' in _HTML

    def test_it_carries_the_install_command(self) -> None:
        """Verbatim from Swarm Next's own install doc — a notice that sends
        someone to a command that does not work is worse than a link."""
        assert (
            "curl -fsSL https://raw.githubusercontent.com/miopea/swarm-next/main/install.sh | sh"
            in _HTML
        )

    def test_it_links_the_migration_guide(self) -> None:
        assert "docs/moving-from-legacy.md" in _HTML

    def test_it_names_what_migration_brings(self) -> None:
        for promised in ("repository", "Conversations", "Open tasks"):
            assert promised in _HTML, f"the notice no longer mentions {promised}"

    def test_it_names_what_migration_does_not_bring(self) -> None:
        """The half a marketing page would leave out.

        Swarm Next's bundle carries workers and tasks. Claiming more than that
        is the one failure mode of this notice that costs an operator real
        work, so each omission is named individually rather than hidden behind
        "some settings".
        """
        assert "What does not come across" in _HTML
        for left in ("identity files", "groups", "approval rules", "skills"):
            assert left in _HTML, f"the notice stopped naming {left} as staying behind"

    def test_it_says_the_two_can_coexist(self) -> None:
        assert "side by side" in _HTML

    def test_it_names_the_way_out(self) -> None:
        """An operator who decides to stop should not have to go looking."""
        assert "swarm-legacy uninstall" in _HTML
        assert "--purge" in _HTML

    def test_the_prose_has_its_own_styling(self) -> None:
        assert ".sunset-body" in _BASE


class TestTheDismissalIsSessionScoped:
    def test_all_three_actions_are_registered(self) -> None:
        for action in ("showSunsetNotice", "hideSunsetNotice", "dismissSunsetBanner"):
            assert f"{action}: function()" in _JS, f"{action} is not in the action registry"

    def test_it_uses_session_storage_not_local_storage(self) -> None:
        """The entire difference between "quiet for now" and "never again"."""
        js = _JS.replace('"', "'")
        assert "_SUNSET_KEY = 'swarm_sunset_dismissed'" in js
        assert "sessionStorage.setItem(_SUNSET_KEY" in js
        assert "sessionStorage.getItem(_SUNSET_KEY" in js
        code = _js_without_comments().replace('"', "'")
        block = code[code.index("var _SUNSET_KEY") : code.index("function updateRelocateBanner")]
        assert "localStorage" not in block

    def test_storage_failure_shows_the_banner(self) -> None:
        """Private mode must not silently suppress the notice.

        The read is wrapped and returns false on throw, so a browser with
        storage disabled shows the banner every load rather than never.
        """
        block = _JS[_JS.index("function _sunsetDismissed()") :]
        block = block[: block.index("function initSunsetBanner")]
        assert "catch" in block
        assert "return false;" in block

    def test_the_banner_is_initialised_on_load(self) -> None:
        assert "\n    initSunsetBanner();" in _JS
