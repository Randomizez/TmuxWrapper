---
name: tmux-wrapper
description: Use when tmux must be driven strictly through `tmux_wrapper.py` / `TMUXWrapper` (`type` / `press` / `snapshot` / `view` / `glance`), with wrapper-based inspection instead of direct tmux CLI/API control.
---

# TMUX Wrapper

## Overview
Use this skill when tmux interaction should go through `TMUXWrapper` rather than direct tmux CLI/API calls.

Canonical Python import:

```python
from tmux_wrapper import Keys, TMUXWrapper
```

Primary actions:
- `type(text)` sends literal text only.
- `press(chords)` sends one or more key chords.
- `snapshot()` captures the whole current screen and resets the baseline.
- `view()` is the default inspection method. It returns a contextual diff against the previous capture.
- `glance()` returns only incremental additions, plus collapsed `...[N unchanged lines]` markers. If nothing new appeared, it returns `[Nothing Changed]`.
- `scroll_up(lines=3)` / `scroll_down(lines=3)` provide line-based history scrolling via tmux copy mode.

CLI examples assume the package exposes `tmux-c`:

```bash
tmux-c demo snapshot
tmux-c demo type "ls"
tmux-c demo press Enter
tmux-c demo view
tmux-c demo glance
tmux-c demo scroll_up 5
```

## Default Workflow
Use one small action at a time and inspect after it.

Recommended pattern:

```bash
tmux-c demo snapshot
tmux-c demo type "echo hello"
tmux-c demo press Enter
tmux-c demo view
tmux-c demo glance
```

Rules of thumb:
- Prefer `view()` for the normal “what changed, with context?” workflow.
- Use `glance()` when you want a smaller incremental summary.
- Call `snapshot()` when you need to reset the baseline before the next action.
- `type()` does not press Enter; pair it with `press Enter` when needed.
- Keep prefix sequences as separate chords, for example `press Ctrl+B Z`.
- If the screen is slow to refresh, wait briefly before `view()` or `glance()`.

## Common Patterns
- Run a command:
  - `tmux-c demo type "pytest -q"`
  - `tmux-c demo press Enter`
  - `tmux-c demo view`
- Interrupt:
  - `tmux-c demo press Ctrl+C`
- Pane navigation:
  - `tmux-c demo press Ctrl+B Left`
  - `tmux-c demo press Ctrl+B Right`
- Zoom toggle:
  - `tmux-c demo press Ctrl+B Z`
- Scroll through output:
  - `tmux-c demo scroll_up 20`
  - `tmux-c demo view`
  - `tmux-c demo scroll_down 20`
  - `tmux-c demo view`

## Behavior Notes
- `TMUXWrapper(session=...)` creates the session if it does not already exist.
- If the wrapper created the session, object cleanup deletes it by default.
- Calling `delete()` always deletes the session immediately.
- `snapshot()`, `view()`, and `glance()` are stateful because they update the stored baseline.
- `view()` keeps unchanged context; `glance()` compresses unchanged regions.
- In CLI mode, baseline state is persisted per session so separate invocations can still diff correctly.

## Pitfalls
- Do not mix this wrapper workflow with direct tmux CLI/API control in the same sequence unless explicitly required.
- If focus looks wrong, inspect first before sending more keys.
- Some prefix actions depend on tmux state; for example `last-pane` can fail if no previous pane exists.
- `scroll_down()` exits copy mode automatically when it reaches the bottom.
- If you zoom a pane for inspection, unzoom it before leaving a shared session.
