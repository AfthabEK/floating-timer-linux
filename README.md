# Floating Pomodoro

A tiny Pomodoro timer for Linux (X11) that stays **on top of everything, including fullscreen windows** such as videos, games and slides.

```
 ● 24:58
 ━━━━━━──────
```

## Install (Debian / Ubuntu)

```sh
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0
git clone https://github.com/AfthabEK/floating-timer-linux.git
cd floating-timer-linux
./install.sh --autostart     # leave out --autostart if you don't want it at login
floating-pomodoro &
```

You can also run it without installing: `./floating_pomodoro.py`.
To uninstall, run `./install.sh --uninstall`.

## Controls

| Action | Effect |
|---|---|
| Left click | Start / pause (also stops the "time's up" flash) |
| Left drag | Move the timer (the position is remembered) |
| Scroll (while paused) | Add or remove one minute |
| Middle click | Reset the current phase |
| Right click | Menu: start/pause, reset, skip, quit |

Colors: **red** = focus, **green** = short break, **blue** = long break. A filled dot means the timer is running; a ring means it is paused. When a phase ends, the timer flashes, plays a sound and sends a desktop notification. The next phase then waits for your click (use `--auto-start` to skip the wait).

## Options

```
floating-pomodoro [-w MIN] [-s MIN] [-l MIN] [--long-every N]
                  [--auto-start] [--scale X] [--opacity 0-1] [--mute] [--start]
```

Defaults: 25 min focus, 5 min short break, 15 min long break after every 4 focus sessions.
To make settings permanent, put the same keys in `~/.config/floating-pomodoro/config.json`, for example:

```json
{ "work": 50, "short_break": 10, "scale": 1.3, "opacity": 0.9, "auto_start": true }
```

## How it stays above fullscreen windows

A normal "always on top" window still goes *under* a focused fullscreen window, because window managers put fullscreen windows in a higher layer. This timer uses an **override-redirect** window (GTK `POPUP`): the window manager never manages it, so nothing restacks it. It also re-raises itself every second in case another override-redirect window covers it.

This only works on **X11**. On GNOME, check with `echo $XDG_SESSION_TYPE`. If it says `wayland`, choose **"GNOME on Xorg"** from the gear icon on the login screen.

## Development

```sh
PYTHONPATH=. python3 -m unittest tests/test_pomodoro.py
```
