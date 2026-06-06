# macOS / Windows "first launch" snippet for GitHub Release notes

Paste this into the description of each GitHub Release that attaches a built
`Heaviside.app` (or Windows build), so downloaders see it without having to open
the README. It mirrors the "Opening the app on macOS" section of the README.

---

### ⚠️ Opening the app the first time

**macOS:** This app is open-source and **not notarized by Apple**, so macOS may
say it is *"damaged"* or *cannot be checked for malicious software* on first
launch. It is not damaged — that is just how macOS treats un-notarized downloads.
To open it:

- **Right-click the app → Open**, then confirm. *(If that option is unavailable
  on your macOS version, use the Terminal command below.)*
- **Or** run, then open normally:
  ```sh
  xattr -dr com.apple.quarantine /Applications/Heaviside.app
  ```

**Windows:** If SmartScreen shows an "unknown publisher" prompt, choose
**More info → Run anyway**.

**Requires `pdflatex`** (TeX Live or MiKTeX, with the `circuitikz` package) on
your PATH for the live preview and PDF/EPS export. Editing and `.tex` export work
without it.
