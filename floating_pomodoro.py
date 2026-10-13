#!/usr/bin/env python3
"""Floating Pomodoro: a tiny timer that stays on top of everything on X11,
including fullscreen windows (videos, games, presentations).

It uses an override-redirect (GTK "popup") window: the window manager does not
manage it, so it is never pushed below a fullscreen window. It also re-raises
itself every second in case another override-redirect window covers it.

Mouse controls:
    left click        start / pause (clicking also silences the end-of-phase flash)
    left drag         move the timer (position is remembered)
    scroll            while paused: add / remove one minute
    middle click      reset the current phase
    right click       menu (start/pause, reset, skip, quit)
"""

import argparse
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "floating-pomodoro"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOCK_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / f"floating-pomodoro-{os.getuid()}.lock"

SOUND_FILES = [
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
]

WORK, SHORT_BREAK, LONG_BREAK = "work", "short", "long"
PHASE_NAMES = {WORK: "Focus", SHORT_BREAK: "Short break", LONG_BREAK: "Long break"}
PHASE_COLORS = {
    WORK: (0.94, 0.36, 0.36),
    SHORT_BREAK: (0.40, 0.80, 0.50),
    LONG_BREAK: (0.36, 0.64, 0.96),
}


class Pomodoro:
    """Timer state, independent of the UI. Uses a monotonic deadline so it never drifts."""

    def __init__(self, work=25, short_break=5, long_break=15, long_every=4,
                 auto_start=False, clock=time.monotonic):
        self.minutes = {WORK: work, SHORT_BREAK: short_break, LONG_BREAK: long_break}
        self.long_every = max(1, long_every)
        self.auto_start = auto_start
        self.clock = clock
        self.completed = 0  # finished focus sessions
        self.phase = WORK
        self._set_phase(WORK, running=False)

    def _set_phase(self, phase, running):
        self.phase = phase
        self.total = self.minutes[phase] * 60
        self._remaining = float(self.total)
        self.running = False
        if running:
            self.start()

    def remaining(self):
        if self.running:
            return max(0.0, self._deadline - self.clock())
        return self._remaining

    def progress(self):
        return 1.0 - self.remaining() / self.total if self.total else 1.0

    def start(self):
        if not self.running:
            self._deadline = self.clock() + self._remaining
            self.running = True

    def pause(self):
        if self.running:
            self._remaining = self.remaining()
            self.running = False

    def toggle(self):
        self.pause() if self.running else self.start()

    def reset(self):
        self._set_phase(self.phase, running=False)

    def adjust(self, minutes):
        """Change the current phase length while paused (never below one minute)."""
        if self.running:
            return
        new_remaining = max(60.0, self._remaining + minutes * 60)
        self.total = max(60, int(self.total + (new_remaining - self._remaining)))
        self._remaining = min(new_remaining, float(self.total))

    def advance(self):
        if self.phase == WORK:
            self.completed += 1
            nxt = LONG_BREAK if self.completed % self.long_every == 0 else SHORT_BREAK
        else:
            nxt = WORK
        self._set_phase(nxt, running=self.auto_start)

    def tick(self):
        """Call regularly. Returns True when the current phase just ended."""
        if self.running and self.remaining() <= 0:
            self.advance()
            return True
        return False


def rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -1.5708, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
    cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
    cr.arc(x + r, y + r, r, 3.1416, 4.7124)
    cr.close_path()


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    except OSError as e:
        print(f"floating-pomodoro: could not save config: {e}", file=sys.stderr)


class TimerWindow(Gtk.Window):
    def __init__(self, timer, opts, cfg):
        super().__init__(type=Gtk.WindowType.POPUP)  # override-redirect
        self.timer, self.opts, self.cfg = timer, opts, cfg
        self.width, self.height = int(118 * opts.scale), int(40 * opts.scale)
        self.press = None
        self.menu = None
        self.flashing = False
        self.flash_on = False

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.translucent = bool(visual and screen.is_composited())
        if self.translucent:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_size_request(self.width, self.height)
        self.resize(self.width, self.height)
        self.set_keep_above(True)

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                        | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.SCROLL_MASK)
        self.connect("draw", self.on_draw)
        self.connect("button-press-event", self.on_press)
        self.connect("button-release-event", self.on_release)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("scroll-event", self.on_scroll)

        self.move(*self.initial_position())
        self.show_all()
        GLib.timeout_add(250, self.on_tick)
        GLib.timeout_add(1000, self.keep_on_top)

    # --- placement -------------------------------------------------------
    def monitor_geometry(self):
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        return mon.get_geometry()

    def initial_position(self):
        geo = self.monitor_geometry()
        default = (geo.x + geo.width - self.width - 24, geo.y + 48)
        x, y = self.cfg.get("x", default[0]), self.cfg.get("y", default[1])
        # Keep it visible if the screen layout changed since last time.
        display = Gdk.Display.get_default()
        monitors = [display.get_monitor(i).get_geometry() for i in range(display.get_n_monitors())]
        if not any(m.x <= x <= m.x + m.width - self.width and m.y <= y <= m.y + m.height - self.height
                   for m in monitors):
            x, y = default
        return int(x), int(y)

    def keep_on_top(self):
        win = self.get_window()
        if win and self.press is None and not (self.menu and self.menu.get_visible()):
            win.raise_()
        return True

    # --- timer -----------------------------------------------------------
    def on_tick(self):
        if self.timer.tick():
            self.phase_finished()
        if self.flashing:
            self.flash_on = not self.flash_on
        self.queue_draw()
        return True

    def phase_finished(self):
        self.flashing = not self.timer.auto_start
        name = PHASE_NAMES[self.timer.phase]
        if not self.opts.mute:
            self.play_sound()
        if shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", "Pomodoro", "Pomodoro",
                              f"Time's up. Next: {name}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def play_sound(self):
        sound = next((s for s in SOUND_FILES if os.path.exists(s)), None)
        for player in ("pw-play", "paplay", "ogg123"):
            if sound and shutil.which(player):
                cmd = [player, "-q", sound] if player == "ogg123" else [player, sound]
                break
        else:
            if not shutil.which("canberra-gtk-play"):
                Gdk.beep()
                return
            cmd = ["canberra-gtk-play", "-i", "complete"]
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # --- drawing ---------------------------------------------------------
    def on_draw(self, _widget, cr):
        w, h = self.get_allocated_width(), self.get_allocated_height()
        s = self.opts.scale
        color = PHASE_COLORS[self.timer.phase]
        flash = self.flashing and self.flash_on

        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        alpha = self.opts.opacity if self.translucent else 1.0
        rounded_rect(cr, 0, 0, w, h, (h / 2.6) if self.translucent else 0)
        if flash:
            cr.set_source_rgba(*color, alpha)
        else:
            cr.set_source_rgba(0.09, 0.09, 0.11, alpha)
        cr.fill()

        # phase dot: filled while running, ring while paused
        cx, cy, r = 17 * s, h / 2 - 2 * s, 5 * s
        cr.arc(cx, cy, r, 0, 6.2832)
        cr.set_source_rgb(*((0.09, 0.09, 0.11) if flash else color))
        if self.timer.running:
            cr.fill()
        else:
            cr.set_line_width(1.8 * s)
            cr.stroke()

        # time
        secs = int(self.timer.remaining() + 0.999)
        text = f"{secs // 60:02d}:{secs % 60:02d}"
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f"Monospace Bold {int(15 * s)}"))
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        tx = 30 * s + (w - 30 * s - 10 * s - tw) / 2
        cr.move_to(tx, (h - th) / 2 - 2 * s)
        if flash:
            cr.set_source_rgb(0.09, 0.09, 0.11)
        else:
            cr.set_source_rgba(1, 1, 1, 0.95 if self.timer.running else 0.55)
        PangoCairo.show_layout(cr, layout)

        # progress bar
        pad, bar_h = 12 * s, 2.5 * s
        bar_w = w - 2 * pad
        cr.set_source_rgba(1, 1, 1, 0.12)
        cr.rectangle(pad, h - 7 * s, bar_w, bar_h)
        cr.fill()
        cr.set_source_rgb(*((0.09, 0.09, 0.11) if flash else color))
        cr.rectangle(pad, h - 7 * s, bar_w * min(1.0, self.timer.progress()), bar_h)
        cr.fill()
        return True

    # --- input -----------------------------------------------------------
    def on_press(self, _w, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:
            return True  # ignore double/triple-click synthesized events
        if ev.button == 1:
            x, y = self.get_position()
            self.press = (ev.x_root, ev.y_root, x, y, False)
        elif ev.button == 2:
            self.timer.reset()
            self.flashing = False
        elif ev.button == 3:
            self.show_menu(ev)
        self.queue_draw()
        return True

    def on_motion(self, _w, ev):
        if self.press:
            px, py, wx, wy, dragged = self.press
            dx, dy = ev.x_root - px, ev.y_root - py
            if dragged or abs(dx) > 4 or abs(dy) > 4:
                self.press = (px, py, wx, wy, True)
                self.move(int(wx + dx), int(wy + dy))
        return True

    def on_release(self, _w, ev):
        if ev.button == 1 and self.press:
            dragged = self.press[4]
            self.press = None
            if dragged:
                x, y = self.get_position()
                self.cfg.update(x=x, y=y)
                save_config(self.cfg)
            else:
                if self.flashing:
                    self.flashing = False
                else:
                    self.timer.toggle()
            self.queue_draw()
        return True

    def on_scroll(self, _w, ev):
        delta = 0
        if ev.direction == Gdk.ScrollDirection.UP:
            delta = 1
        elif ev.direction == Gdk.ScrollDirection.DOWN:
            delta = -1
        elif ev.direction == Gdk.ScrollDirection.SMOOTH:
            delta = -1 if ev.delta_y > 0 else 1 if ev.delta_y < 0 else 0
        if delta:
            self.timer.adjust(delta)
            self.queue_draw()
        return True

    def show_menu(self, ev):
        menu = Gtk.Menu()

        def item(label, cb):
            mi = Gtk.MenuItem(label=label)
            mi.connect("activate", lambda *_: (cb(), self.queue_draw()))
            menu.append(mi)

        def stop_flash_and(fn):
            def run():
                self.flashing = False
                fn()
            return run

        item("Pause" if self.timer.running else "Start", stop_flash_and(self.timer.toggle))
        item("Reset", stop_flash_and(self.timer.reset))
        item("Skip to next", stop_flash_and(self.timer.advance))
        menu.append(Gtk.SeparatorMenuItem())
        info = Gtk.MenuItem(label=f"{PHASE_NAMES[self.timer.phase]} · {self.timer.completed} done")
        info.set_sensitive(False)
        menu.append(info)
        menu.append(Gtk.SeparatorMenuItem())
        item("Quit", Gtk.main_quit)
        menu.attach_to_widget(self, None)
        menu.show_all()
        menu.popup_at_pointer(ev)
        self.menu = menu


def single_instance():
    """Return an open lock file, or None if another instance already holds it."""
    fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def parse_args(cfg, argv=None):
    p = argparse.ArgumentParser(description="Minimal floating Pomodoro timer that stays above fullscreen windows (X11).")
    p.add_argument("-w", "--work", type=int, default=cfg.get("work", 25), help="focus minutes (default 25)")
    p.add_argument("-s", "--short-break", type=int, default=cfg.get("short_break", 5), help="short break minutes (default 5)")
    p.add_argument("-l", "--long-break", type=int, default=cfg.get("long_break", 15), help="long break minutes (default 15)")
    p.add_argument("--long-every", type=int, default=cfg.get("long_every", 4), help="long break after this many focus sessions (default 4)")
    p.add_argument("--auto-start", action="store_true", default=cfg.get("auto_start", False), help="start the next phase automatically")
    p.add_argument("--scale", type=float, default=cfg.get("scale", 1.0), help="size multiplier (default 1.0)")
    p.add_argument("--opacity", type=float, default=cfg.get("opacity", 0.85), help="background opacity 0-1 (default 0.85)")
    p.add_argument("--mute", action="store_true", default=cfg.get("mute", False), help="no sound when a phase ends")
    p.add_argument("--start", action="store_true", help="start counting immediately")
    return p.parse_args(argv)


def main():
    cfg = load_config()
    opts = parse_args(cfg)

    if os.environ.get("XDG_SESSION_TYPE") == "wayland":
        print("floating-pomodoro: warning: Wayland session detected; staying above fullscreen "
              "windows only works on X11 (pick 'GNOME on Xorg' at login).", file=sys.stderr)

    lock = single_instance()
    if lock is None:
        print("floating-pomodoro is already running.", file=sys.stderr)
        return 1

    timer = Pomodoro(opts.work, opts.short_break, opts.long_break, opts.long_every, opts.auto_start)
    if opts.start:
        timer.start()
    TimerWindow(timer, opts, cfg)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, Gtk.main_quit)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
