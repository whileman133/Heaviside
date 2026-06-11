# dmgbuild settings for the macOS "drag to Applications" disk image.
#
# Consumed by scripts/make_dmg.py:
#
#     dmgbuild -s packaging/dmg_settings.py "Heaviside" dist/Heaviside-macos-arm64.dmg
#
# The app path and background image are passed in via environment variables so
# the same settings file serves the local build and the release workflow. The
# coordinates here MUST match scripts/make_dmg_background.py (which draws the
# arrow/title to line up with these two icon slots).

import os.path

# -- Inputs ------------------------------------------------------------------
_app_path = os.environ.get("DMG_APP_PATH", "dist/Heaviside.app")
_appname = os.path.basename(_app_path)  # e.g. "Heaviside.app"
_background = os.environ.get("DMG_BACKGROUND", "assets/dmg-background.png")
_volume_icon = os.environ.get("DMG_VOLUME_ICON", "assets/icon.icns")

# -- Disk image --------------------------------------------------------------
format = "UDZO"            # zlib-compressed, read-only — the standard for release
files = [_app_path]
symlinks = {"Applications": "/Applications"}

if os.path.exists(_volume_icon):
    icon = _volume_icon    # branded mounted-volume icon

# -- Window / icon layout (Finder icon view) --------------------------------
background = _background
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
window_rect = ((300, 150), (660, 420))
icon_size = 120
text_size = 13
icon_locations = {
    _appname: (170, 235),
    "Applications": (490, 235),
}
