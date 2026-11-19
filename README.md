# Basic Terminal Emulator

A lightweight terminal emulator built with Python and PyQt6, using a pseudo-terminal (`pty`) for shell I/O and a custom screen buffer for ANSI/CSI escape sequences and 256-color / RGB styling.

## Features

- Pseudo-terminal (`pty`) process spawning, non-blocking reads via `QSocketNotifier`, and dynamic window resize (`TIOCSWINSZ`)
- ANSI/VT escape sequence handling including CSI cursor movement, erase operations, and SGR text attributes (bold, underline, 16/256 colors, and RGB)
- Scrollback buffer support and keyboard/clipboard shortcuts (`Ctrl+Shift+C`, `Ctrl+Shift+V`, control keys, arrows, and navigation keys)
- Built-in smoke test mode via `--smoke`

## Requirements

- Python 3
- PyQt6

## Usage

bash
python3 terminal.py


Run a quick non-interactive smoke check:

bash
python3 terminal.py --smoke

