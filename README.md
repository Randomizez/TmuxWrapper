# tmux-wrapper

`tmux-wrapper` is a Python module and CLI for driving a tmux session like a
human: type text, press key chords, inspect what changed, and scroll through
history.

It is aimed at agent workflows, test automation, and other situations where
tmux should be treated as an interactive device instead of a bag of ad-hoc tmux
CLI commands.

## Installation

```bash
pip install tmux-wrapper
```

After installation, the CLI command is:

```bash
tmux-c
```

To print the bundled inline skill/help text:

```bash
tmux-c skill
```

## Requirements

- Python 3.6+
- `tmux` installed and available on `PATH`

## Core Model

Treat one tmux session as a serialized device.

For a given session:
- send one action
- wait briefly or inspect
- then send the next action

Do not overlap wrapper actions against the same session.

The most important mental model is:

- `tmux-wrapper` is not a screenshot reader
- it is an incremental observer whose output depends on the previous capture

Once you treat it that way, the workflow becomes much more predictable.

## Main API

```python
from tmux_wrapper import Keys, TMUXWrapper
```

- `type(text)` sends literal text only.
- `press(chords)` sends one or more key chords.
- `glance()` is the default inspection method.
- `view()` is the fallback inspection method when `glance()` is too compressed.
- `scroll_up(lines=3)` / `scroll_down(lines=3)` provide line-based history scrolling.
- `snapshot()` is intentionally disabled.

## Inspection Semantics

### `glance()`

`glance()` is the default inspection method.

It returns:
- newly added lines as `!!...`
- unchanged regions as `...[N unchanged line(s)]`
- `[Nothing Changed]` when no new additions appeared

Example:

```text
...[12 unchanged lines]
!!new output line
...[3 unchanged lines]
```

### `view()`

`view()` is the fallback when you need more context.

Compared with `glance()`:
- it keeps unchanged context lines
- it is better when you want the precise current screen region
- it is noisier than `glance()`

## Recommended Workflow

Default to `glance()`. Use `view()` only when `glance()` does not provide
enough context.

### Python

```python
from tmux_wrapper import Keys, TMUXWrapper

tmux = TMUXWrapper(session="demo")

tmux.glance()
tmux.type("echo hello")
tmux.glance()
tmux.press([(Keys.Enter,)])
print(tmux.glance())
print(tmux.view())
```

### CLI

```bash
tmux-c demo glance
tmux-c demo type "echo hello"
tmux-c demo glance
tmux-c demo press Enter
tmux-c demo glance
tmux-c demo view
```

## Command Entry Safety

- Before typing into an existing shell, inspect first.
- If the pane may still be running something, use `press Ctrl+C`, then inspect again until you see a stable prompt.
- If a shell line may already contain partial input, clear it before retyping.
- Prefer `press Ctrl+C` for interrupting a running process.
- Use `press Ctrl+U` only when you specifically want to clear the current shell line.
- Do not send `type "..."` and `press Enter` in the same parallel batch.
- For long commands, prefer:
  - `glance`
  - `type`
  - `glance` or a short wait
  - `press Enter`
  - `glance`
- If the command text is visible at the prompt but did not execute, press `Enter` once and inspect. Do not retype until you know the line is clean.

Recommended shared-shell pattern:

```bash
tmux-c demo glance
tmux-c demo press Ctrl+C
tmux-c demo glance
tmux-c demo type "python your_long_running_command.py ..."
tmux-c demo glance
tmux-c demo press Enter
tmux-c demo glance
```

## Common Patterns

### Run a command

```bash
tmux-c demo type "pytest -q"
tmux-c demo press Enter
tmux-c demo glance
```

### Interrupt

```bash
tmux-c demo press Ctrl+C
```

### Pane navigation

```bash
tmux-c demo press Ctrl+B Left
tmux-c demo press Ctrl+B Right
```

### Zoom toggle

```bash
tmux-c demo press Ctrl+B Z
```

### Scroll through output

```bash
tmux-c demo scroll_up 20
tmux-c demo glance
tmux-c demo scroll_down 20
tmux-c demo glance
```

## Practical Usage Notes

- `glance()` is best for “what was added?”
- `view()` is better when you want the exact visible screen region
- `scroll_up()` enters tmux history; it does not rerun the command
- `scroll_down 9999` is a practical way to return to the bottom and usually exits copy mode too
- When reading the top of a big file, the most reliable flow is:
  - print the whole file once
  - scroll up in small steps
  - inspect with `glance()`
- If you scroll too far in one jump, you may end up in older terminal history instead of the output you just produced
- The diff baseline persists per session, so always keep track of what the last capture was
- If the session becomes messy, the usual recovery pattern is:
  - `press Ctrl+C`
  - `scroll_down 9999`
  - inspect
  - rerun the command

## Session Behavior

- `TMUXWrapper(session="name")` creates the session if it does not already exist.
- If the wrapper created the session, object cleanup deletes it by default.
- Calling `delete()` always deletes the session immediately.
- `view()` and `glance()` are stateful because they update the stored baseline.
- CLI baseline state is persisted per session so separate `tmux-c` invocations can still diff correctly.

## Development

Install development dependencies with uv:

```bash
uv sync --dev
```

Run tests with the repo default interpreter:

```bash
env -u VIRTUAL_ENV uv run pytest -q
```

Run the same suite against specific Python versions:

```bash
env -u VIRTUAL_ENV uv run --python 3.6 --group dev pytest -q
env -u VIRTUAL_ENV uv run --python 3.8 --group dev pytest -q
env -u VIRTUAL_ENV uv run --python 3.12 --group dev pytest -q
```

Build distributions:

```bash
env -u VIRTUAL_ENV uv build
```
