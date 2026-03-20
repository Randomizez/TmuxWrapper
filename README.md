# tmux-wrapper

`tmux-wrapper` is a small Python module and CLI for driving a tmux session like
a human: type text, press key chords, inspect what changed, and scroll through
history.

It is designed for agent workflows, test automation, and other cases where you
want a simple tmux control surface instead of shelling out to a large stack of
custom tmux commands.

## Features

- `type(text)` sends literal text to the active pane.
- `press(chords)` sends key chords such as `Enter`, `Ctrl+C`, or `Ctrl+B Z`.
- `snapshot()` captures the full current screen and resets the diff baseline.
- `view()` is the recommended inspection API. It returns a contextual,
  line-oriented delta against the previous capture.
- `glance()` returns incremental additions plus collapsed
  `...[N unchanged lines]` markers for unchanged regions.
- `scroll_up(lines=3)` and `scroll_down(lines=3)` emulate mouse-wheel style
  scrolling via tmux copy mode.
- `tmux-c` provides the same workflow from the command line.

## Requirements

- Python 3.9+
- `tmux` installed and available on `PATH`

## Installation

Install from PyPI:

```bash
pip install tmux-wrapper
```

After installation, the CLI command `tmux-c` is available:

```bash
tmux-c 1 glance
```

## Quick Start

### Python API

```python
from tmux_wrapper import Keys, TMUXWrapper

tmux = TMUXWrapper(session="demo")

# Establish a baseline for future view()/glance() calls.
tmux.snapshot()

tmux.type("echo hello")
tmux.press([(Keys.Enter,)])
print(tmux.view())

# For a compact "only what was newly added" report:
print(tmux.glance())

tmux.scroll_up(5)
print(tmux.view())

tmux.scroll_down(999)
tmux.delete()
```

### CLI

```bash
tmux-c demo snapshot
tmux-c demo type "ls /data"
tmux-c demo press Enter
tmux-c demo view
tmux-c demo glance
tmux-c demo scroll_up 5
tmux-c demo view
tmux-c demo scroll_down 999
```

## How Inspection Works

`view()` is the default inspection method.

- `snapshot()` captures the full screen and stores it as the new baseline.
- `view()` compares the current screen against the previous capture.
- `glance()` uses the same diff basis, but returns only added lines plus
  `...[N unchanged lines]` markers for unchanged regions.
- Added lines are marked with `!!`.
- Removed lines are hidden.
- `?` helper lines from `difflib.ndiff` are also hidden.
- If there are no new additions, `glance()` returns `[Nothing Changed]`.

Example:

```text
!!new output line
  existing prompt context
```

For compact incremental output, `glance()` returns abbreviated output such as:

```text
...[12 unchanged lines]
!!new output line
...[3 unchanged lines]
```

## Press Syntax

In Python, `press()` accepts a list of chords:

```python
tmux.press([(Keys.Enter,)])
tmux.press([(Keys.Ctrl, Keys.C)])
tmux.press([(Keys.Ctrl, Keys.B), (Keys.Z,)])
tmux.press([(Keys.Ctrl, Keys.B), (Keys.Left,)])
```

In the CLI, each chord is passed as an argument:

```bash
tmux-c demo press Enter
tmux-c demo press Ctrl+C
tmux-c demo press Ctrl+B Z
tmux-c demo press Ctrl+B Left
```

## Scrolling

`scroll_up()` and `scroll_down()` are line-based helpers built on tmux copy
mode.

- `scroll_up(lines)` enters copy mode and scrolls up by `lines`.
- `scroll_down(lines)` scrolls down by `lines`.
- When `scroll_down()` reaches the bottom, it exits copy mode automatically.

This matches the intended "mouse wheel with `set -g mouse on`" feel more closely
than page-based movement.

## Session Behavior

- `TMUXWrapper(session="name")` creates the session if it does not already
  exist.
- If the wrapper created the session, object cleanup will delete it by default.
- Calling `delete()` always deletes the session immediately.
- CLI snapshot/view/glance state is persisted per session so repeated
  `tmux-c ...` calls can diff across separate invocations.

## Development

Install development dependencies with uv:

```bash
uv sync --dev
```

Run tests:

```bash
env -u VIRTUAL_ENV uv run pytest -q
```

## Notes

- The package focuses on a practical tmux-driving workflow, not a full tmux
  abstraction layer.
- The renderer captures the full tmux window, not just a single pane.
- The cursor is rendered visibly in full-screen snapshots.
