# Third-Party Licenses

Heaviside itself is distributed under the MIT License (see the top-level
`LICENSE` file). The distributed application bundles the following third-party
components, whose licenses and obligations are reproduced or referenced here.

This notice is included **inside the distributed application** (the macOS
`.app` and the Windows/Linux `Heaviside/` folder) to satisfy the attribution and
notice requirements of the licenses below.

---

## Qt and PySide6 — GNU LGPL v3

This application uses **Qt** (https://www.qt.io/) via **PySide6** (Qt for
Python), both licensed under the **GNU Lesser General Public License, version 3
(LGPLv3)**.

- **Component:** PySide6 (Qt for Python) and the underlying Qt libraries
- **Version bundled:** PySide6 `6.11.1` (which wraps Qt `6.11.x`)
- **License:** LGPL v3 — full text in `LGPL-3.0.txt` in this folder. The LGPLv3
  incorporates the terms of the GPLv3 by reference; obtain the GPLv3 text from
  https://www.gnu.org/licenses/gpl-3.0.txt.
- **Copyright:** © The Qt Company Ltd and other contributors.

### Corresponding source (LGPLv3 §4 / §6)

The complete corresponding source code for the bundled PySide6 and Qt libraries
is available from their original distributors:

- **PySide6 source:** https://pypi.org/project/PySide6/6.11.1/#files
  (the source distribution for the exact version bundled), and the upstream
  repository at https://code.qt.io/cgit/pyside/pyside-setup.git/
- **Qt 6.11 source:** https://download.qt.io/official_releases/qt/6.11/
  and the upstream repository at https://code.qt.io/cgit/qt/qtbase.git/

If for any reason these become unavailable, the distributor of this application
will, on request, provide the corresponding source for the bundled versions for
a period of at least three years from the date of distribution.

### Replacing the bundled Qt (LGPLv3 relinking)

This application is distributed as a **directory bundle** (a macOS `.app`, or a
`Heaviside/` folder on Windows/Linux) in which the Qt/PySide6 shared libraries
are present as **separate, replaceable files** rather than statically linked into
a single executable. You may therefore substitute your own version of the
Qt/PySide6 libraries — for example a modified or rebuilt Qt of the same major
version — without rebuilding the application itself.

The libraries live at:

- **macOS:** `Heaviside.app/Contents/Frameworks/PySide6/Qt/lib/`
  (each Qt module is a `.framework`, e.g. `QtCore.framework`,
  `QtWidgets.framework`, `QtGui.framework`, `QtPdf.framework`), and the PySide6
  binding modules at `Heaviside.app/Contents/Resources/PySide6/`.
- **Windows:** `Heaviside\PySide6\` (the `Qt6*.dll` files).
- **Linux:** `Heaviside/PySide6/Qt/lib/` (the `libQt6*.so*` files) and
  `Heaviside/PySide6/`.

**To replace a Qt library:** swap the corresponding `.framework` / `.dll` /
`.so` with your own build of the same Qt version (so the binding ABI matches),
keeping the same file name and path.

**macOS code signing note.** The distributed `.app` is code-signed, and macOS
verifies that signature against the bundle's contents. Replacing a framework
invalidates the signature, so after swapping you must re-establish it one of two
ways:

1. **Re-sign the bundle yourself** (ad-hoc is sufficient):

   ```sh
   codesign --force --deep --sign - Heaviside.app
   ```

2. **Run it unsigned** — remove the quarantine attribute and launch:

   ```sh
   xattr -dr com.apple.quarantine Heaviside.app
   open Heaviside.app
   ```

   (Or, without Terminal: System Settings → Privacy & Security → **Open Anyway**.)

The bundle is **not** built with hardened-runtime *library validation*, so a
Qt build signed by you (or signed ad-hoc) is accepted after re-signing — the
relinking capability is preserved by design.

---

## qtawesome — MIT License

Icon-font toolkit used for toolbar and ribbon glyphs.

- **License:** MIT
- **Source:** https://github.com/spyder-ide/qtawesome

## pydantic — MIT License

Data-model validation library.

- **License:** MIT
- **Source:** https://github.com/pydantic/pydantic

## ziamath — MIT License

Pure-Python math typesetter. Powers the no-LaTeX on-canvas equation labels.

- **License:** MIT
- **Source:** https://github.com/cdelker/ziamath

## ziafont — MIT License

Pure-Python font rendering used by ziamath.

- **License:** MIT
- **Source:** https://github.com/cdelker/ziafont

---

## Bundled fonts

The distributed application redistributes the font files below. They are shipped
inside the bundle by their parent Python packages (ziamath, ziafont, qtawesome)
and are loaded at runtime. The full text of the SIL Open Font License 1.1 is in
`OFL-1.1.txt`, and the Apache License 2.0 is in `Apache-2.0.txt`, both in this
folder.

### Math/text fonts (via ziamath / ziafont)

- **STIX Two Math** — used to typeset on-canvas math labels when LaTeX is not
  installed. License: **SIL Open Font License 1.1** (see `OFL-1.1.txt`).
  Copyright © 2001–2021 by the STI Pub Companies, with Reserved Font Name
  "STIX Two". Source: https://github.com/stipub/stixfonts
- **DejaVu Sans** — used by ziafont for plain text in labels. License: the
  permissive **DejaVu Fonts License** (a Bitstream Vera derivative; the DejaVu
  additions are in the public domain). Copyright © 2003 Bitstream, Inc.; DejaVu
  changes © Tavmjong Bah and contributors. Full terms:
  https://dejavu-fonts.github.io/License.html

### Icon fonts (via qtawesome)

qtawesome registers its full set of bundled icon fonts at startup, so the
application redistributes all of them:

- **Font Awesome 5 & 6 Free** (the set Heaviside actually draws toolbar/ribbon
  glyphs from). Fonts: **SIL OFL 1.1** (see `OFL-1.1.txt`); icons:
  **CC BY 4.0** (https://creativecommons.org/licenses/by/4.0/); code: MIT.
  Copyright © Fonticons, Inc. License summary: https://fontawesome.com/license/free
- **Material Design Icons** — **SIL OFL 1.1** (see `OFL-1.1.txt`).
  Copyright © Pictogrammers. Source: https://github.com/Templarian/MaterialDesign
- **Elusive Icons** — **SIL OFL 1.1** (see `OFL-1.1.txt`).
  Copyright © Aristeides Stathopoulos. Source: https://github.com/dovy/elusive-icons
- **Phosphor Icons** — **MIT License**. Copyright © Phosphor Icons.
  Source: https://github.com/phosphor-icons/web
- **Remix Icon** — **Apache License 2.0** (see `Apache-2.0.txt`).
  Copyright © Remix Design. Source: https://github.com/Remix-Design/RemixIcon
- **Codicon** (VS Code icons) — **CC BY 4.0**
  (https://creativecommons.org/licenses/by/4.0/); code: MIT.
  Copyright © Microsoft Corporation. Source: https://github.com/microsoft/vscode-codicons
