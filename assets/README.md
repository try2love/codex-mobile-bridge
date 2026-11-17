# Application icons

`icon-light.svg` and `icon-dark.svg` are the editable color sources for the
terminal-and-phone mark. `tray-template.svg` is its monochrome macOS menu-bar
adaptation. Do not edit the exported PNG/ICO files by hand.

Run `npm ci` and then `npm run icons:desktop` to export the desktop, web, and
website assets. The command uses the project's Electron renderer in a hidden
window, writes both appearances at the existing sizes, creates multi-size
Windows ICOs, and generates the macOS template images. No gateway or user
account is opened. The website copy of `brand-icons.js` is also generated from
`web/shared/brand-icons.js`.

- Windows running window/taskbar and tray icons follow the Windows **system**
  appearance, which may differ from its app appearance. Desktop page branding
  follows the app appearance.
- macOS Dock follows the system appearance. The menu-bar template is tinted by
  macOS to match its background.
- Web branding, favicon, touch icon, and the manifest offered for a new PWA
  installation follow the page's existing Light / Dark / System setting. A
  resolved native WebView theme takes precedence over the browser media query.
- Static installer, executable, Finder/Explorer, and shortcut icons use the new
  light artwork. Existing pinned shortcuts and installed PWAs may retain a
  system-cached icon; runtime switching does not rewrite those registrations.

The two color sources share a `1024 × 1024` viewBox and have transparent outer
corners. PNGs preserve transparency. The Windows ICOs contain 16, 20, 24, 32,
40, 48, 64, 128, and 256 pixel representations.
