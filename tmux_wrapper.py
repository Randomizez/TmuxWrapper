#!/usr/bin/env -S -u VIRTUAL_ENV uv run python

"""Keyboard-driven tmux automation helpers.

This module exposes a small wrapper around tmux plus a renderer that turns
``tmux attach`` output into a printable text snapshot. It is meant for tests
and agents that must drive tmux the same way a human would:

1) create or attach to a session,
2) send literal text or key chords,
3) inspect the whole tmux window after each action.

Public entry points:
1) ``Keys`` enumerates the supported modifier, character, navigation, and
   function keys used by ``TMUXWrapper.press()``.
2) ``TMUXWrapper.type(text)`` sends literal text to the active pane without
   adding a trailing newline.
3) ``TMUXWrapper.press(chords)`` sends one or more key chords, including tmux
   prefix sequences such as ``(Keys.Ctrl, Keys.B)`` followed by another key.
4) ``TMUXWrapper.snapshot()`` captures the whole current window and resets the
   diff baseline.
5) ``TMUXWrapper.view()`` is the recommended inspection API. It compares the
   current window against the previous capture and keeps unchanged context.
6) ``TMUXWrapper.glance()`` shows only the incremental additions since the
   previous capture.
7) ``TMUXWrapper.scroll_up(lines=3)`` and ``scroll_down(lines=3)`` emulate
   mouse-wheel scrolling by operating tmux copy mode in line increments.

Behavior notes:
1) ``TMUXWrapper`` creates the target session on demand.
2) If this wrapper created the session, object cleanup deletes it by default.
3) Common tmux prefix bindings such as pane navigation and page scrolling are
   translated through tmux commands when direct key injection is unreliable.

Example:
    >>> from tmux_wrapper import Keys, TMUXWrapper
    >>> tmux = TMUXWrapper(session="demo")
    >>> tmux.snapshot()  # establish an initial baseline
    >>> tmux.type("echo hello")
    >>> tmux.press([(Keys.Enter,)])
    >>> print(tmux.view())
    ...
    >>> tmux.delete()

CLI:
    $ tmux-c demo snapshot
    $ tmux-c demo type "ls"
    $ tmux-c demo press Enter
    $ tmux-c demo view
    $ tmux-c demo glance
    $ tmux-c demo press Ctrl+B Z
    $ tmux-c demo scroll_up 5
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import fcntl
import struct
import subprocess
import tempfile
import time
from typing import Iterable, Optional
import difflib

class literal(str):
    """String subclass whose repr is the raw text block itself."""

    def __repr__(self):
        return self

class Keys(str, Enum):
    """Keyboard keys accepted by :meth:`TMUXWrapper.press`."""
    # Modifiers
    Ctrl = "Ctrl"
    Alt = "Alt"
    Shift = "Shift"
    # Letters
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"
    I = "I"
    J = "J"
    K = "K"
    L = "L"
    M = "M"
    N = "N"
    O = "O"
    P = "P"
    Q = "Q"
    R = "R"
    S = "S"
    T = "T"
    U = "U"
    V = "V"
    W = "W"
    X = "X"
    Y = "Y"
    Z = "Z"
    # Digits
    Digit0 = "Digit0"
    Digit1 = "Digit1"
    Digit2 = "Digit2"
    Digit3 = "Digit3"
    Digit4 = "Digit4"
    Digit5 = "Digit5"
    Digit6 = "Digit6"
    Digit7 = "Digit7"
    Digit8 = "Digit8"
    Digit9 = "Digit9"
    # Punctuation (ANSI US)
    Backtick = "Backtick"
    Minus = "Minus"
    Equal = "Equal"
    LeftBracket = "LeftBracket"
    RightBracket = "RightBracket"
    Backslash = "Backslash"
    Semicolon = "Semicolon"
    Quote = "Quote"
    Comma = "Comma"
    Period = "Period"
    Slash = "Slash"
    Space = "Space"
    # Control keys
    Enter = "Enter"
    Tab = "Tab"
    Escape = "Escape"
    Backspace = "Backspace"
    CapsLock = "CapsLock"
    # Navigation
    Up = "Up"
    Down = "Down"
    Left = "Left"
    Right = "Right"
    Home = "Home"
    End = "End"
    PageUp = "PageUp"
    PageDown = "PageDown"
    Insert = "Insert"
    Delete = "Delete"
    # Function keys
    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"
    F5 = "F5"
    F6 = "F6"
    F7 = "F7"
    F8 = "F8"
    F9 = "F9"
    F10 = "F10"
    F11 = "F11"
    F12 = "F12"
    # System keys
    PrintScreen = "PrintScreen"
    ScrollLock = "ScrollLock"
    Pause = "Pause"


class TMUXRenderer:
    """Render tmux attach output into a fixed-size text buffer."""

    _ALT_CHARSET_MAP = {
        "q": "─",
        "x": "│",
        "n": "┼",
        "l": "┌",
        "k": "┐",
        "m": "└",
        "j": "┘",
        "t": "├",
        "u": "┤",
        "w": "┬",
        "v": "┴",
    }

    def render(
        self,
        text: str,
        width: int,
        height: int,
    ) -> list[str]:
        """Render a captured tmux screen and overlay the cursor position."""
        lines, cursor_pos = self._render_pty(text, width, height)
        if cursor_pos is not None:
            row, col = cursor_pos
            if 0 <= row < len(lines):
                line = lines[row]
                if 0 <= col < len(line):
                    lines[row] = line[:col] + "▁" + line[col + 1 :]
        if height is not None and len(lines) != height:
            if len(lines) > height:
                lines = lines[-height:]
            else:
                lines = [" " * width for _ in range(height - len(lines))] + lines
        return lines

    def _render_pty(self, text: str, width: int, height: int) -> tuple[list[str], Optional[tuple[int, int]]]:
        screen = [[" " for _ in range(width)] for _ in range(height)]
        row = 0
        col = 0
        g0_line = False
        g1_line = True
        use_g1 = False
        saved_row = 0
        saved_col = 0
        scroll_top = 0
        scroll_bottom = height - 1
        cursor_visible = True
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\x0e":  # SO
                use_g1 = True
                i += 1
                continue
            if ch == "\x0f":  # SI
                use_g1 = False
                i += 1
                continue
            if ch == "\x1b":
                i += 1
                if i >= len(text):
                    break
                if text[i] == "7":
                    saved_row, saved_col = row, col
                    i += 1
                    continue
                if text[i] == "8":
                    row, col = saved_row, saved_col
                    i += 1
                    continue
                if text[i] in ("(", ")"):
                    if i + 1 < len(text):
                        set_g1 = text[i] == ")"
                        mode = text[i + 1]
                        if set_g1:
                            g1_line = mode == "0"
                        else:
                            g0_line = mode == "0"
                        i += 2
                        continue
                if text[i] == "[":
                    i += 1
                    params, final, private, i = self._parse_csi(text, i)
                    if private and final in ("h", "l") and (params[0] if params else 0) == 25:
                        cursor_visible = final == "h"
                    else:
                        row, col, saved_row, saved_col, scroll_top, scroll_bottom = self._apply_csi(
                            params,
                            final,
                            row,
                            col,
                            screen,
                            saved_row,
                            saved_col,
                            scroll_top,
                            scroll_bottom,
                        )
                    continue
                if text[i] == "]":
                    i = self._skip_osc(text, i + 1)
                    continue
                i += 1
                continue
            if ch == "\r":
                col = 0
            elif ch == "\n":
                row += 1
                if row > scroll_bottom:
                    del screen[scroll_top]
                    screen.insert(scroll_bottom, [" " for _ in range(width)])
                    row = scroll_bottom
            elif ch == "\b":
                col = max(0, col - 1)
            elif ch == "\t":
                col = min(width - 1, (col // 8 + 1) * 8)
            elif ch >= " " and ch != "\x7f":
                if col >= width:
                    row += 1
                    col = 0
                    if row >= height:
                        screen.pop(0)
                        screen.append([" " for _ in range(width)])
                        row = height - 1
                line_drawing = (use_g1 and g1_line) or ((not use_g1) and g0_line)
                if line_drawing:
                    ch = self._ALT_CHARSET_MAP.get(ch, ch)
                if 0 <= row < height and 0 <= col < width:
                    screen[row][col] = ch
                col += 1
            i += 1

        lines = [self._strip_control_chars("".join(line)) for line in screen]
        if not cursor_visible:
            return lines, None
        return lines, (row, min(max(col, 0), max(width - 1, 0)))

    @staticmethod
    def _parse_csi(text: str, i: int) -> tuple[list[int], str, bool, int]:
        params: list[int] = []
        current = ""
        private = False
        while i < len(text):
            ch = text[i]
            if ch.isdigit():
                current += ch
            elif ch == ";":
                params.append(int(current) if current else 0)
                current = ""
            elif ch == "?":
                private = True
                current = ""
            else:
                if current or params:
                    params.append(int(current) if current else 0)
                return params, ch, private, i + 1
            i += 1
        return params, "m", private, i

    @staticmethod
    def _skip_osc(text: str, i: int) -> int:
        while i < len(text):
            if text[i] == "\x07":
                return i + 1
            if text[i] == "\x1b" and i + 1 < len(text) and text[i + 1] == "\\":
                return i + 2
            i += 1
        return i

    def _apply_csi(
        self,
        params: list[int],
        final: str,
        row: int,
        col: int,
        screen: list[list[str]],
        saved_row: int,
        saved_col: int,
        scroll_top: int,
        scroll_bottom: int,
    ) -> tuple[int, int, int, int, int, int]:
        height = len(screen)
        width = len(screen[0]) if height else 0
        param = params[0] if params else 0
        if final in ("H", "f"):
            r = (params[0] - 1) if len(params) >= 1 and params[0] else 0
            c = (params[1] - 1) if len(params) >= 2 and params[1] else 0
            return max(0, min(height - 1, r)), max(0, min(width - 1, c)), saved_row, saved_col, scroll_top, scroll_bottom
        if final == "A":
            return max(0, row - (param or 1)), col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "B":
            return min(height - 1, row + (param or 1)), col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "C":
            return row, min(width - 1, col + (param or 1)), saved_row, saved_col, scroll_top, scroll_bottom
        if final == "D":
            return row, max(0, col - (param or 1)), saved_row, saved_col, scroll_top, scroll_bottom
        if final == "G":
            c = (param - 1) if param else 0
            return row, max(0, min(width - 1, c)), saved_row, saved_col, scroll_top, scroll_bottom
        if final == "E":
            r = min(height - 1, row + (param or 1))
            return r, 0, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "F":
            r = max(0, row - (param or 1))
            return r, 0, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "s":
            return row, col, row, col, scroll_top, scroll_bottom
        if final == "u":
            return saved_row, saved_col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "r":
            top = (params[0] - 1) if len(params) >= 1 and params[0] else 0
            bottom = (params[1] - 1) if len(params) >= 2 and params[1] else height - 1
            top = max(0, min(height - 1, top))
            bottom = max(top, min(height - 1, bottom))
            return row, col, saved_row, saved_col, top, bottom
        if final == "J":
            mode = param or 0
            if mode == 2:
                for r in range(height):
                    screen[r] = [" " for _ in range(width)]
            elif mode == 0:
                for r in range(row, height):
                    start = col if r == row else 0
                    for c in range(start, width):
                        screen[r][c] = " "
            return row, col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "K":
            mode = param or 0
            if mode == 2:
                for c in range(width):
                    screen[row][c] = " "
            elif mode == 0:
                for c in range(col, width):
                    screen[row][c] = " "
            elif mode == 1:
                for c in range(0, col + 1):
                    screen[row][c] = " "
            return row, col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "L":
            count = param or 1
            count = min(count, scroll_bottom - row + 1)
            for _ in range(count):
                screen.insert(row, [" " for _ in range(width)])
                del screen[scroll_bottom + 1]
            return row, col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "M":
            count = param or 1
            count = min(count, scroll_bottom - row + 1)
            for _ in range(count):
                del screen[row]
                screen.insert(scroll_bottom, [" " for _ in range(width)])
            return row, col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "@":
            count = param or 1
            for _ in range(count):
                screen[row].insert(col, " ")
                screen[row].pop()
            return row, col, saved_row, saved_col, scroll_top, scroll_bottom
        if final == "P":
            count = param or 1
            for _ in range(count):
                if col < width:
                    del screen[row][col]
                    screen[row].append(" ")
            return row, col, saved_row, saved_col, scroll_top, scroll_bottom
        return row, col, saved_row, saved_col, scroll_top, scroll_bottom

    @staticmethod
    def _strip_control_chars(line: str) -> str:
        return "".join(ch for ch in line if ch >= " " and ch != "\x7f")


class TMUXWrapper:
    """Drive a tmux session through text entry, key chords, and window capture."""

    def __init__(
        self,
        session: str,
        tmux_bin: str = "tmux",
        renderer: Optional[TMUXRenderer] = None,
    ) -> None:
        """Attach to ``session``, creating it if needed."""
        self.session = session
        self.tmux_bin = tmux_bin
        self.renderer = renderer or TMUXRenderer()
        self._default_size = (200, 40)
        self._prefix_pending = False
        self._owns_session = False
        self._ensure_session()
        self._afterimage = []

    def __del__(self) -> None:
        self._safe_delete()

    def type(self, type_str: str) -> None:
        """Send literal text to the active pane without pressing Enter."""
        if not type_str:
            return
        self._run_tmux(["send-keys", "-t", self._target(), "-l", type_str])

    def press(self, keys: list[tuple[Keys, ...]]) -> None:
        """Send key chords to tmux.

        Each chord is a tuple containing zero or more modifiers plus exactly
        one base key. To issue a tmux prefix binding, send ``Ctrl+B`` as one
        chord and the bound key as the next chord.
        """
        if not keys:
            return
        for chord in keys:
            if not chord:
                continue
            if self._is_prefix_chord(chord):
                self._prefix_pending = True
                continue
            if self._prefix_pending and self._handle_tmux_binding(chord):
                self._prefix_pending = False
                continue
            self._prefix_pending = False
            encoded = self._encode_chord(chord)
            self._run_tmux(["send-keys", "-t", self._target(), encoded])

    def snapshot(self) -> literal:
        """Capture the whole tmux window and reset the diff baseline."""
        content = self._attach_capture()
        self._afterimage = content
        return literal("\n".join(content))

    def glance(self) -> literal:
        """Return additions plus counted collapsed markers for unchanged regions."""
        afterimage = self._afterimage
        content = self._attach_capture()
        self._afterimage = content
        diff = self._glance_lines(afterimage, content)
        if not diff:
            return literal("[Nothing Changed]")
        return literal("\n".join(diff))

    def view(self) -> literal:
        """Return a contextual diff against the previous capture."""
        afterimage = self._afterimage
        content = self._attach_capture()
        self._afterimage = content
        diff = self._diff_lines(afterimage, content, include_context=True)
        return literal("\n".join(diff))

    def scroll_up(self, lines: int = 3) -> None:
        """Enter copy mode and scroll the viewport up by ``lines``."""
        repeat = self._normalize_scroll_lines(lines)
        if repeat == 0:
            return
        self._enter_copy_mode()
        if not self._try_copy_mode_action(["scroll-up"], repeat=repeat):
            self._run_tmux(["send-keys", "-t", self._target(), "PageUp"])

    def scroll_down(self, lines: int = 3) -> None:
        """Enter copy mode and scroll down; exit copy mode at the bottom."""
        repeat = self._normalize_scroll_lines(lines)
        if repeat == 0:
            return
        self._enter_copy_mode()
        if not self._try_copy_mode_action(["scroll-down"], repeat=repeat):
            self._run_tmux(["send-keys", "-t", self._target(), "PageDown"])
        if self._in_copy_mode() and self._scroll_position() == 0:
            self._try_copy_mode_action(["cancel"])

    def delete(self) -> None:
        """Delete the tmux session immediately."""
        try:
            self._run_tmux(["kill-session", "-t", self.session])
        except RuntimeError as exc:
            if "can't find session" in str(exc):
                return
            raise

    def _target(self) -> str:
        return self.session

    def _run_tmux(self, args: Iterable[str]) -> str:
        cmd = [self.tmux_bin, *args]
        try:
            completed = subprocess.run(
                cmd,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"tmux binary not found: {self.tmux_bin}") from exc
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or exc.stdout.strip()
            raise RuntimeError(f"tmux command failed: {message}") from exc
        return completed.stdout

    def _window_target(self) -> str:
        return self.session

    def _attach_capture(self) -> list[str]:
        master_fd, slave_fd = pty.openpty()
        try:
            width, height = self._window_size()
        except RuntimeError:
            width, height = self._default_size
        self._set_pty_size(slave_fd, width, height)
        try:
            self._run_tmux(["refresh-client", "-S", "-t", self._window_target()])
        except RuntimeError:
            pass
        env = os.environ.copy()
        env.setdefault("TERM", "xterm-256color")
        proc = subprocess.Popen(
            [self.tmux_bin, "attach", "-t", self._window_target()],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )
        os.close(slave_fd)

        output = b""
        deadline = time.time() + 1.5
        while time.time() < deadline:
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            if master_fd in readable:
                try:
                    chunk = os.read(master_fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk

        try:
            os.write(master_fd, b"\x02d")
        except OSError:
            pass

        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.terminate()

        os.close(master_fd)

        text = output.decode("utf-8", errors="ignore")
        return self.renderer.render(text, width, height)

    def _window_size(self) -> tuple[int, int]:
        target = self._window_target()
        output = self._run_tmux(["display-message", "-p", "-t", target, "#{window_width} #{window_height}"]).strip()
        width_str, height_str = output.split()
        return int(width_str), int(height_str) + 1

    def _client_size(self) -> Optional[tuple[int, int]]:
        try:
            output = self._run_tmux([
                "list-clients",
                "-t",
                self.session,
                "-F",
                "#{client_active} #{client_width} #{client_height}",
            ]).splitlines()
        except RuntimeError:
            return None
        if not output:
            return None
        active = None
        largest = None
        for line in output:
            parts = line.split()
            if len(parts) != 3:
                continue
            is_active, w, h = parts
            size = (int(w), int(h))
            if largest is None or (size[0] * size[1]) > (largest[0] * largest[1]):
                largest = size
            if is_active == "1":
                active = size
                break
        if active is not None:
            return active
        return largest

    @staticmethod
    def _set_pty_size(fd: int, width: int, height: int) -> None:
        winsize = struct.pack("HHHH", height, width, 0, 0)
        fcntl.ioctl(fd, 0x5414, winsize)

    @staticmethod
    @lru_cache(maxsize=1)
    def _tmux_version() -> tuple[int, int, int]:
        output = subprocess.run(
            ["tmux", "-V"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
        version = output.split()[-1]
        digits: list[int] = []
        for part in version.replace("a", "").replace("b", "").split("."):
            if part.isdigit():
                digits.append(int(part))
        while len(digits) < 3:
            digits.append(0)
        return tuple(digits[:3])

    def _in_copy_mode(self) -> bool:
        return self._run_tmux(
            ["display-message", "-p", "-t", self._target(), "#{pane_in_mode}"]
        ).strip() == "1"

    def _scroll_position(self) -> Optional[int]:
        value = self._run_tmux(
            ["display-message", "-p", "-t", self._target(), "#{scroll_position}"]
        ).strip()
        if not value:
            return None
        return int(value)

    @staticmethod
    def _diff_lines(
        before: list[str],
        after: list[str],
        include_context: bool,
    ) -> list[str]:
        diff = []
        for line in difflib.ndiff(before, after):
            if line.startswith(("- ", "? ")):
                continue
            if line.startswith("+ "):
                diff.append(f"!!{line[2:]}")
                continue
            if include_context:
                diff.append(line)
        return diff

    @staticmethod
    def _glance_lines(before: list[str], after: list[str]) -> list[str]:
        lines = []
        pending_context = 0
        saw_addition = False

        for line in difflib.ndiff(before, after):
            if line.startswith(("- ", "? ")):
                continue
            if line.startswith("+ "):
                if pending_context:
                    suffix = "line" if pending_context == 1 else "lines"
                    lines.append(f"...[{pending_context} unchanged {suffix}]")
                    pending_context = 0
                lines.append(f"!!{line[2:]}")
                saw_addition = True
                continue
            pending_context += 1

        if not saw_addition:
            return []
        if pending_context:
            suffix = "line" if pending_context == 1 else "lines"
            lines.append(f"...[{pending_context} unchanged {suffix}]")
        return lines

    def _enter_copy_mode(self) -> None:
        if self._in_copy_mode():
            return
        self._run_tmux(["copy-mode", "-t", self._target()])
        for _ in range(5):
            if self._in_copy_mode():
                return

    def _try_copy_mode_action(self, actions: list[str], repeat: int = 1) -> bool:
        for action in actions:
            cmd = ["send-keys", "-X"]
            if repeat != 1:
                cmd.extend(["-N", str(repeat)])
            cmd.extend(["-t", self._target(), action])
            try:
                self._run_tmux(cmd)
                return True
            except RuntimeError:
                continue
        return False

    @staticmethod
    def _normalize_scroll_lines(lines: int) -> int:
        if lines < 0:
            raise ValueError("lines must be >= 0")
        return lines

    def _ensure_session(self) -> None:
        try:
            self._run_tmux(["has-session", "-t", self.session])
            self._owns_session = False
        except RuntimeError:
            self._run_tmux(["new-session", "-d", "-s", self.session])
            self._owns_session = True

    def _safe_delete(self) -> None:
        try:
            if self._owns_session:
                self.delete()
        except Exception:
            return

    @staticmethod
    def _is_prefix_chord(chord: tuple[Keys, ...]) -> bool:
        return set(chord) == {Keys.Ctrl, Keys.B}

    def _handle_tmux_binding(self, chord: tuple[Keys, ...]) -> bool:
        mods, base = self._split_chord(chord)
        if base in (Keys.Up, Keys.Down, Keys.Left, Keys.Right) and not mods:
            direction = {
                Keys.Up: "U",
                Keys.Down: "D",
                Keys.Left: "L",
                Keys.Right: "R",
            }[base]
            self._run_tmux(["select-pane", f"-{direction}", "-t", self._target()])
            return True
        if base is Keys.PageUp and not mods:
            self._enter_copy_mode()
            if not self._try_copy_mode_action(["page-up", "scroll-up"]):
                self._run_tmux(["send-keys", "-t", self._target(), "PageUp"])
            return True
        if base is Keys.PageDown and not mods:
            self._enter_copy_mode()
            if not self._try_copy_mode_action(["page-down", "scroll-down"]):
                self._run_tmux(["send-keys", "-t", self._target(), "PageDown"])
            return True
        if base is Keys.Digit5 and not mods:
            self._run_tmux(["split-window", "-h", "-t", self._target()])
            return True
        if mods and any(mod in mods for mod in (Keys.Ctrl, Keys.Alt)):
            return False

        char = self._encode_character_key(mods, base)
        if char is None:
            return False

        action = self._PREFIX_BINDINGS.get(char)
        if action is None:
            return False

        cmd, *extra = action
        self._run_tmux([cmd, *extra, "-t", self._target()])
        return True
        return False

    @staticmethod
    def _split_chord(chord: tuple[Keys, ...]) -> tuple[list[Keys], Keys]:
        modifiers = {Keys.Ctrl, Keys.Alt, Keys.Shift}
        mods = [key for key in chord if key in modifiers]
        base_keys = [key for key in chord if key not in modifiers]
        if len(base_keys) != 1:
            raise ValueError(f"Chord must contain exactly one base key: {chord}")
        return mods, base_keys[0]

    @staticmethod
    def _encode_chord(chord: tuple[Keys, ...]) -> str:
        mods, base = TMUXWrapper._split_chord(chord)
        return TMUXWrapper._encode_key(mods, base)

    @staticmethod
    def _encode_key(mods: list[Keys], base: Keys) -> str:
        char = TMUXWrapper._encode_character_key(mods, base)
        if char is not None:
            return char

        return TMUXWrapper._encode_special_key(mods, base)

    @staticmethod
    def _encode_character_key(mods: list[Keys], base: Keys) -> Optional[str]:
        shifted = Keys.Shift in mods

        if base in TMUXWrapper._LETTER_KEYS:
            letter = base.value.lower()
            if shifted:
                letter = letter.upper()
            return TMUXWrapper._apply_modifiers(mods, letter)

        if base in TMUXWrapper._DIGIT_KEYS:
            unshifted, shifted_char = TMUXWrapper._DIGIT_KEYS[base]
            char = shifted_char if shifted else unshifted
            return TMUXWrapper._apply_modifiers(mods, char)

        if base in TMUXWrapper._PUNCT_KEYS:
            unshifted, shifted_char = TMUXWrapper._PUNCT_KEYS[base]
            char = shifted_char if shifted else unshifted
            return TMUXWrapper._apply_modifiers(mods, char)

        if base is Keys.Space:
            return TMUXWrapper._apply_modifiers(mods, " ")

        return None

    @staticmethod
    def _encode_special_key(mods: list[Keys], base: Keys) -> str:
        key_name = TMUXWrapper._SPECIAL_KEYS.get(base, base.value)
        return TMUXWrapper._apply_modifiers(mods, key_name, force_named=True)

    @staticmethod
    def _apply_modifiers(mods: list[Keys], key: str, force_named: bool = False) -> str:
        mod_prefix = []
        if Keys.Ctrl in mods:
            mod_prefix.append("C")
        if Keys.Alt in mods:
            mod_prefix.append("M")
        if Keys.Shift in mods and force_named:
            mod_prefix.append("S")

        if not mod_prefix:
            return key
        return f"{'-'.join(mod_prefix)}-{key}"

    _LETTER_KEYS = {
        Keys.A, Keys.B, Keys.C, Keys.D, Keys.E, Keys.F, Keys.G, Keys.H, Keys.I, Keys.J,
        Keys.K, Keys.L, Keys.M, Keys.N, Keys.O, Keys.P, Keys.Q, Keys.R, Keys.S, Keys.T,
        Keys.U, Keys.V, Keys.W, Keys.X, Keys.Y, Keys.Z,
    }

    _DIGIT_KEYS = {
        Keys.Digit0: ("0", ")"),
        Keys.Digit1: ("1", "!"),
        Keys.Digit2: ("2", "@"),
        Keys.Digit3: ("3", "#"),
        Keys.Digit4: ("4", "$"),
        Keys.Digit5: ("5", "%"),
        Keys.Digit6: ("6", "^"),
        Keys.Digit7: ("7", "&"),
        Keys.Digit8: ("8", "*"),
        Keys.Digit9: ("9", "("),
    }

    _PUNCT_KEYS = {
        Keys.Backtick: ("`", "~"),
        Keys.Minus: ("-", "_"),
        Keys.Equal: ("=", "+"),
        Keys.LeftBracket: ("[", "{"),
        Keys.RightBracket: ("]", "}"),
        Keys.Backslash: ("\\", "|"),
        Keys.Semicolon: (";", ":"),
        Keys.Quote: ("'", "\""),
        Keys.Comma: (",", "<"),
        Keys.Period: (".", ">"),
        Keys.Slash: ("/", "?"),
    }

    _SPECIAL_KEYS = {
        Keys.Enter: "Enter",
        Keys.Tab: "Tab",
        Keys.Escape: "Escape",
        Keys.Backspace: "BSpace",
        Keys.CapsLock: "CapsLock",
        Keys.Up: "Up",
        Keys.Down: "Down",
        Keys.Left: "Left",
        Keys.Right: "Right",
        Keys.Home: "Home",
        Keys.End: "End",
        Keys.PageUp: "PageUp",
        Keys.PageDown: "PageDown",
        Keys.Insert: "Insert",
        Keys.Delete: "Delete",
        Keys.F1: "F1",
        Keys.F2: "F2",
        Keys.F3: "F3",
        Keys.F4: "F4",
        Keys.F5: "F5",
        Keys.F6: "F6",
        Keys.F7: "F7",
        Keys.F8: "F8",
        Keys.F9: "F9",
        Keys.F10: "F10",
        Keys.F11: "F11",
        Keys.F12: "F12",
        Keys.PrintScreen: "PPrint",
        Keys.ScrollLock: "ScrollLock",
        Keys.Pause: "Pause",
    }

    _PREFIX_BINDINGS = {
        "\"": ("split-window", "-v"),
        "%": ("split-window", "-h"),
        "5": ("split-window", "-h"),
        "c": ("new-window",),
        "x": ("kill-pane",),
        "z": ("resize-pane", "-Z"),
        "o": ("select-pane", "-t", ":.+"),
        ";": ("last-pane",),
        "n": ("next-window",),
        "p": ("previous-window",),
        "l": ("last-window",),
        "0": ("select-window", "-t", ":0"),
        "1": ("select-window", "-t", ":1"),
        "2": ("select-window", "-t", ":2"),
        "3": ("select-window", "-t", ":3"),
        "4": ("select-window", "-t", ":4"),
        "5": ("select-window", "-t", ":5"),
        "6": ("select-window", "-t", ":6"),
        "7": ("select-window", "-t", ":7"),
        "8": ("select-window", "-t", ":8"),
        "9": ("select-window", "-t", ":9"),
    }


def _parse_cli_key(name: str) -> Keys:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Key name cannot be empty")

    aliases = {
        "esc": Keys.Escape,
        "return": Keys.Enter,
        "pgup": Keys.PageUp,
        "pageup": Keys.PageUp,
        "pgdn": Keys.PageDown,
        "pagedown": Keys.PageDown,
        "space": Keys.Space,
    }
    key = aliases.get(normalized.lower())
    if key is not None:
        return key

    for candidate in Keys:
        if normalized.lower() in (candidate.name.lower(), candidate.value.lower()):
            return candidate
    raise ValueError(f"Unknown key: {name}")


def _parse_cli_chord(chord: str) -> tuple[Keys, ...]:
    parts = [part for part in chord.replace("-", "+").split("+") if part]
    if not parts:
        raise ValueError("Chord cannot be empty")
    return tuple(_parse_cli_key(part) for part in parts)


class _TMUXWrapperCLI:
    """Command-line facade for a single tmux session.

    Default workflow: use `glance` before/after each action. Prefer
    `scroll_up` / `scroll_down` for paging instead of relying on `view`.
    Common `press` keys: `Enter`, `Up`, `Down`, `Left`, `Right`, `PageUp`,
    `PageDown`, `Ctrl+C`, `Ctrl+B Z`, `Ctrl+B Left`, `Ctrl+B Right`.
    """

    def __init__(self, session: str, tmux_bin: str = "tmux") -> None:
        self._tmux = TMUXWrapper(session=session, tmux_bin=tmux_bin)
        # CLI calls happen in separate processes, so keep the session alive
        # until the user explicitly runs `delete`.
        self._tmux._owns_session = False
        digest = hashlib.sha1(session.encode("utf-8")).hexdigest()
        self._state_path = Path(tempfile.gettempdir()) / "tmux_wrapper" / f"{digest}.json"

    def _load_afterimage(self) -> None:
        try:
            self._tmux._afterimage = json.loads(self._state_path.read_text())
        except FileNotFoundError:
            self._tmux._afterimage = []

    def _save_afterimage(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._tmux._afterimage))

    def snapshot(self) -> literal:
        """Capture the whole current window and reset the baseline."""
        rendered = self._tmux.snapshot()
        self._save_afterimage()
        return rendered

    def view(self) -> literal:
        """Show a contextual diff against the previous CLI capture."""
        self._load_afterimage()
        rendered = self._tmux.view()
        self._save_afterimage()
        return rendered

    def glance(self) -> literal:
        """Show only incremental additions against the previous CLI capture."""
        self._load_afterimage()
        rendered = self._tmux.glance()
        self._save_afterimage()
        return rendered

    def type(self, text: str) -> None:
        """Send literal text without pressing Enter."""
        self._tmux.type(text)

    def press(self, *chords: str) -> None:
        """Send key chords such as `Enter`, `Ctrl+C`, or `Ctrl+B Z`.

        Common keys: `Enter`, `Up`, `Down`, `Left`, `Right`, `PageUp`,
        `PageDown`, `Escape`, `Tab`, `Backspace`, `Ctrl+C`, `Ctrl+B Z`,
        `Ctrl+B Left`, `Ctrl+B Right`, `Ctrl+B Digit5`.
        """
        if not chords:
            raise ValueError("Provide at least one chord, e.g. `press Enter`")
        self._tmux.press([_parse_cli_chord(chord) for chord in chords])

    def scroll_up(self, lines: int = 3) -> None:
        """Enter copy mode and scroll up by the given number of lines."""
        self._tmux.scroll_up(lines)

    def scroll_down(self, lines: int = 3) -> None:
        """Enter copy mode and scroll down by the given number of lines."""
        self._tmux.scroll_down(lines)

    def delete(self) -> None:
        """Delete the tmux session and clear the saved CLI afterimage."""
        self._tmux.delete()
        self._state_path.unlink(missing_ok=True)


def main(argv: Optional[list[str]] = None) -> int:
    """Run the Fire-powered tmux CLI."""
    import fire

    args = list(os.sys.argv[1:] if argv is None else argv)
    if not args:
        print("Usage: tmux-c <session> <command> [args...]")
        print("Examples:")
        print("  tmux-c test snapshot")
        print('  tmux-c test type "ls"')
        print("  tmux-c test press Enter")
        print("  tmux-c test view")
        print("  tmux-c test glance")
        print("  tmux-c test press Ctrl+C")
        print("  tmux-c test press Ctrl+B Z")
        print("  tmux-c test scroll_up 5")
        print("Common press keys: Enter, Up, Down, Left, Right, PageUp, PageDown")
        return 1

    session, *command = args
    fire.Fire(_TMUXWrapperCLI(session), command=command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
