import json
import sys
import types
from pathlib import Path

import pytest

import tmux_wrapper


class FakeTMUXWrapper:
    instances = []

    def __init__(self, session: str, tmux_bin: str = "tmux") -> None:
        self.session = session
        self.tmux_bin = tmux_bin
        self._owns_session = True
        self._afterimage = []
        self.calls = []
        self.view_seen_afterimage = None
        self.glance_seen_afterimage = None
        self.view_afterimage = ["first line", "third line"]
        self.glance_afterimage = ["first line", "fourth line"]
        type(self).instances.append(self)

    def view(self) -> tmux_wrapper.literal:
        self.view_seen_afterimage = list(self._afterimage)
        self._afterimage = list(self.view_afterimage)
        return tmux_wrapper.literal("  first line\n!!third line")

    def glance(self) -> tmux_wrapper.literal:
        self.glance_seen_afterimage = list(self._afterimage)
        self._afterimage = list(self.glance_afterimage)
        return tmux_wrapper.literal("!!fourth line")

    def type(self, text: str) -> None:
        self.calls.append(("type", text))

    def press(self, chords: list[tuple[tmux_wrapper.Keys, ...]]) -> None:
        self.calls.append(("press", chords))

    def delete(self) -> None:
        self.calls.append(("delete",))

    def scroll_up(self, lines: int = 3) -> None:
        self.calls.append(("scroll_up", lines))

    def scroll_down(self, lines: int = 3) -> None:
        self.calls.append(("scroll_down", lines))


@pytest.fixture(autouse=True)
def reset_fake_wrapper() -> None:
    FakeTMUXWrapper.instances = []


def test_parse_cli_key_supports_aliases_and_case() -> None:
    assert tmux_wrapper._parse_cli_key("enter") == tmux_wrapper.Keys.Enter
    assert tmux_wrapper._parse_cli_key("Return") == tmux_wrapper.Keys.Enter
    assert tmux_wrapper._parse_cli_key("pgdn") == tmux_wrapper.Keys.PageDown
    assert tmux_wrapper._parse_cli_key("leftbracket") == tmux_wrapper.Keys.LeftBracket


def test_parse_cli_chord_supports_plus_and_dash_separators() -> None:
    assert tmux_wrapper._parse_cli_chord("Ctrl+B") == (
        tmux_wrapper.Keys.Ctrl,
        tmux_wrapper.Keys.B,
    )
    assert tmux_wrapper._parse_cli_chord("Ctrl-Shift-Z") == (
        tmux_wrapper.Keys.Ctrl,
        tmux_wrapper.Keys.Shift,
        tmux_wrapper.Keys.Z,
    )


def test_press_parses_each_cli_chord(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_wrapper, "TMUXWrapper", FakeTMUXWrapper)
    cli = tmux_wrapper._TMUXWrapperCLI("demo")

    assert cli._tmux._owns_session is False
    cli.press("Enter", "Ctrl+B", "Z")

    assert cli._tmux.calls == [
        (
            "press",
            [
                (tmux_wrapper.Keys.Enter,),
                (tmux_wrapper.Keys.Ctrl, tmux_wrapper.Keys.B),
                (tmux_wrapper.Keys.Z,),
            ],
        )
    ]


def test_cli_scroll_helpers_forward_line_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_wrapper, "TMUXWrapper", FakeTMUXWrapper)
    cli = tmux_wrapper._TMUXWrapperCLI("demo")

    cli.scroll_up()
    cli.scroll_down(5)

    assert cli._tmux.calls == [("scroll_up", 3), ("scroll_down", 5)]


def test_view_and_glance_persist_afterimage_between_invocations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tmux_wrapper, "TMUXWrapper", FakeTMUXWrapper)

    first = tmux_wrapper._TMUXWrapperCLI("demo")
    first._state_path = tmp_path / "afterimage.json"
    first._state_path.write_text(json.dumps(["first line", "second line"]))

    second = tmux_wrapper._TMUXWrapperCLI("demo")
    second._state_path = first._state_path
    diff = second.view()

    assert diff == "  first line\n!!third line"
    assert second._tmux.view_seen_afterimage == ["first line", "second line"]
    assert json.loads(second._state_path.read_text()) == ["first line", "third line"]

    third = tmux_wrapper._TMUXWrapperCLI("demo")
    third._state_path = first._state_path
    diff = third.glance()

    assert diff == "!!fourth line"
    assert third._tmux.glance_seen_afterimage == ["first line", "third line"]
    assert json.loads(third._state_path.read_text()) == ["first line", "fourth line"]


def test_delete_removes_saved_afterimage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tmux_wrapper, "TMUXWrapper", FakeTMUXWrapper)
    cli = tmux_wrapper._TMUXWrapperCLI("demo")
    cli._state_path = tmp_path / "afterimage.json"
    cli._state_path.write_text(json.dumps(["old frame"]))

    cli.delete()

    assert not cli._state_path.exists()
    assert cli._tmux.calls == [("delete",)]


def _make_wrapper() -> tmux_wrapper.TMUXWrapper:
    wrapper = object.__new__(tmux_wrapper.TMUXWrapper)
    wrapper.session = "demo"
    return wrapper


def test_try_copy_mode_action_places_target_before_action(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    commands = []

    monkeypatch.setattr(wrapper, "_target", lambda: "demo")
    monkeypatch.setattr(wrapper, "_run_tmux", lambda args: commands.append(args) or "")

    assert wrapper._try_copy_mode_action(["scroll-up"], repeat=4) is True
    assert commands == [["send-keys", "-X", "-N", "4", "-t", "demo", "scroll-up"]]


def test_view_returns_contextual_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    wrapper._afterimage = ["line1", "line2", "line4"]
    monkeypatch.setattr(wrapper, "_attach_capture", lambda: ["line1", "line3", "line4"])

    rendered = wrapper.view()

    assert rendered.splitlines() == [
        "  line1",
        "!!line3",
        "  line4",
    ]


def test_glance_returns_incremental_only(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    wrapper._afterimage = ["line1", "line2", "line4"]
    monkeypatch.setattr(wrapper, "_attach_capture", lambda: ["line1", "line3", "line4"])

    rendered = wrapper.glance()

    assert rendered.splitlines() == [
        "...[1 unchanged line]",
        "!!line3",
        "...[1 unchanged line]",
    ]


def test_glance_hides_pure_deletion_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    wrapper._afterimage = ["line1", "line2", "line3"]
    monkeypatch.setattr(wrapper, "_attach_capture", lambda: ["line1", "line3"])

    rendered = wrapper.glance()

    assert rendered == "[Nothing Changed]"


def test_glance_collapses_multiple_unchanged_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    wrapper._afterimage = ["a", "b", "c", "d", "e"]
    monkeypatch.setattr(wrapper, "_attach_capture", lambda: ["a", "x", "c", "y", "e"])

    rendered = wrapper.glance()

    assert rendered.splitlines() == [
        "...[1 unchanged line]",
        "!!x",
        "...[1 unchanged line]",
        "!!y",
        "...[1 unchanged line]",
    ]


def test_glance_uses_plural_for_longer_unchanged_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    wrapper._afterimage = ["a", "b", "c", "d", "e", "f"]
    monkeypatch.setattr(wrapper, "_attach_capture", lambda: ["a", "b", "x", "d", "e", "f"])

    rendered = wrapper.glance()

    assert rendered.splitlines() == [
        "...[2 unchanged lines]",
        "!!x",
        "...[3 unchanged lines]",
    ]


def test_snapshot_is_disabled() -> None:
    wrapper = _make_wrapper()
    with pytest.raises(RuntimeError, match="snapshot\\(\\) is disabled"):
        wrapper.snapshot()


def test_cli_snapshot_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_wrapper, "TMUXWrapper", FakeTMUXWrapper)
    cli = tmux_wrapper._TMUXWrapperCLI("demo")

    with pytest.raises(RuntimeError, match="snapshot is disabled"):
        cli.snapshot()


def test_scroll_up_enters_copy_mode_and_uses_repeat_count(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    events = []

    monkeypatch.setattr(wrapper, "_enter_copy_mode", lambda: events.append("enter"))
    monkeypatch.setattr(
        wrapper,
        "_try_copy_mode_action",
        lambda actions, repeat=1: events.append(("action", actions, repeat)) or True,
    )

    wrapper.scroll_up(5)

    assert events == ["enter", ("action", ["scroll-up"], 5)]


def test_scroll_down_exits_copy_mode_when_reaching_bottom(monkeypatch: pytest.MonkeyPatch) -> None:
    wrapper = _make_wrapper()
    events = []

    monkeypatch.setattr(wrapper, "_enter_copy_mode", lambda: events.append("enter"))

    def fake_action(actions, repeat=1):
        events.append(("action", actions, repeat))
        return True

    monkeypatch.setattr(wrapper, "_try_copy_mode_action", fake_action)
    monkeypatch.setattr(wrapper, "_in_copy_mode", lambda: True)
    monkeypatch.setattr(wrapper, "_scroll_position", lambda: 0)

    wrapper.scroll_down(2)

    assert events == [
        "enter",
        ("action", ["scroll-down"], 2),
        ("action", ["cancel"], 1),
    ]


def test_scroll_methods_reject_negative_line_counts() -> None:
    with pytest.raises(ValueError, match="lines must be >= 0"):
        tmux_wrapper.TMUXWrapper._normalize_scroll_lines(-1)


def test_main_without_args_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    rc = tmux_wrapper.main([])

    assert rc == 1
    assert "Usage: tmux-c <session> <command> [args...]" in capsys.readouterr().out


def test_main_skill_prints_embedded_or_repo_skill(capsys: pytest.CaptureFixture[str]) -> None:
    rc = tmux_wrapper.main(["skill"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "name: tmux-wrapper" in out
    assert "TMUX Wrapper" in out


def test_main_dispatches_to_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    fire_calls = []

    def fake_fire(component, command):
        fire_calls.append((component, command))

    fake_fire_module = types.SimpleNamespace(Fire=fake_fire)
    monkeypatch.setattr(tmux_wrapper, "TMUXWrapper", FakeTMUXWrapper)
    monkeypatch.setitem(sys.modules, "fire", fake_fire_module)

    rc = tmux_wrapper.main(["test", "press", "Enter"])

    assert rc == 0
    assert len(fire_calls) == 1
    component, command = fire_calls[0]
    assert isinstance(component, tmux_wrapper._TMUXWrapperCLI)
    assert command == ["press", "Enter"]
