#!/bin/sh
# Install floating-pomodoro for the current user.
#   ./install.sh              install command + app menu entry
#   ./install.sh --autostart  also start it automatically at login
#   ./install.sh --uninstall  remove everything
set -e
cd "$(dirname "$0")"
BIN="$HOME/.local/bin/floating-pomodoro"
APP="$HOME/.local/share/applications/floating-pomodoro.desktop"
AUTO="$HOME/.config/autostart/floating-pomodoro.desktop"

if [ "$1" = "--uninstall" ]; then
    rm -f "$BIN" "$APP" "$AUTO"
    echo "Removed floating-pomodoro (settings in ~/.config/floating-pomodoro kept)."
    exit 0
fi

if ! python3 -c "import gi, cairo; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" 2>/dev/null; then
    echo "Missing dependencies. Install them with:"
    echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0"
    exit 1
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$APP")"
install -m 755 floating_pomodoro.py "$BIN"
sed "s|^Exec=.*|Exec=$BIN|" floating-pomodoro.desktop > "$APP"
echo "Installed $BIN"

if [ "$1" = "--autostart" ]; then
    mkdir -p "$(dirname "$AUTO")"
    cp "$APP" "$AUTO"
    echo "Enabled autostart at login."
fi
