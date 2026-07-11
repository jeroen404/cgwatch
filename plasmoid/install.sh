#!/bin/bash
# Install the cgwatch plasmoid for the current user.
#
#   ./install.sh              copy install (package files only)
#   ./install.sh --link       dev mode: symlink the repo as the package
#   ./install.sh --uninstall  remove the installed package / symlink
#
# Do not mix with kpackagetool6 -i/-u while the --link symlink is in place.
set -euo pipefail

PLUGIN_ID="eu.404.cgwatch"
INSTALL_DIR="$HOME/.local/share/plasma/plasmoids/$PLUGIN_ID"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}"

# Icon. The widget picker needs it BEFORE the widget ever runs, so install it
# eagerly here (no notifyrc step -- cgwatchd owns desktop notifications).
install_assets() {
  install -Dm644 "$SCRIPT_DIR/contents/icons/cgwatch.svg" \
    "$DATA_DIR/icons/hicolor/scalable/apps/cgwatch.svg"
  # nudge icon caches so an already-running plasmashell can resolve the new
  # icon: bump dir mtimes, and refresh (or drop) a stale GTK cache that would
  # otherwise hide it
  touch "$DATA_DIR/icons/hicolor" "$DATA_DIR/icons/hicolor/scalable/apps" 2>/dev/null || true
  if [ -f "$DATA_DIR/icons/hicolor/icon-theme.cache" ]; then
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
      gtk-update-icon-cache -q "$DATA_DIR/icons/hicolor" || true
    else
      rm -f "$DATA_DIR/icons/hicolor/icon-theme.cache"
    fi
  fi
}

case "${1:-}" in
  --link)
    if [ -e "$INSTALL_DIR" ] && [ ! -L "$INSTALL_DIR" ]; then
      rm -rf "$INSTALL_DIR"
    fi
    mkdir -p "$(dirname "$INSTALL_DIR")"
    ln -sfnT "$SCRIPT_DIR" "$INSTALL_DIR"
    install_assets
    echo "Symlinked $INSTALL_DIR -> $SCRIPT_DIR"
    ;;
  --uninstall)
    rm -rf "$INSTALL_DIR"
    # artifact install.sh installs into the hicolor theme
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/cgwatch.svg"
    echo "Removed $INSTALL_DIR (and the installed icon)"
    ;;
  "")
    if [ -L "$INSTALL_DIR" ]; then
      echo "Dev symlink in place; refusing to copy over it (use --uninstall first)." >&2
      exit 1
    fi
    FRESH=1
    [ -e "$INSTALL_DIR" ] && FRESH=0
    mkdir -p "$INSTALL_DIR"
    rm -rf "$INSTALL_DIR/contents"
    cp -r "$SCRIPT_DIR/contents" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/metadata.json" "$INSTALL_DIR/"
    install_assets
    echo "Installed to $INSTALL_DIR"
    if [ "$FRESH" = 1 ]; then
      # a brand-new plasmoid is picked up live — no restart needed
      echo "Add it via panel right-click -> Add Widgets... -> CGWatch"
    else
      # plasmashell's QML component cache is process-wide: updated code only
      # runs after a restart (re-adding the widget is not enough)
      echo "Update installed. Restart Plasma to load the new code:"
      echo "  systemctl --user restart plasma-plasmashell.service"
    fi
    ;;
  *)
    echo "usage: $0 [--link|--uninstall]" >&2
    exit 2
    ;;
esac

if [ "${1:-}" = "--link" ]; then
  echo
  echo "NOTE: a symlinked (--link) install does NOT appear in 'Add Widgets' --"
  echo "KPackage skips symlinks when enumerating plasmoids. --link only"
  echo "live-updates a widget that is ALREADY on your panel. To add it the"
  echo "first time, run './install.sh' (copy install), add it from the widget"
  echo "list, THEN re-run --link for live editing."
  echo
  echo "Dev mode: after each edit, restart Plasma to load the new code:"
  echo "  systemctl --user restart plasma-plasmashell.service"
fi
