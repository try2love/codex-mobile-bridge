# Linux Desktop Support (Experimental)

v1.3.0 adds experimental Linux integration and native x64/ARM64 packages.
Native CI validates package installation, GUI startup and isolated gateway behavior.
Compatibility with a particular Codex desktop version still requires real-device IPC testing.

## Scope

| Target | Build baseline | Packages | Current validation |
| --- | --- | --- | --- |
| Linux x64 / amd64 | Ubuntu 22.04 x64 | amd64.deb, x86_64.AppImage | Native CI installation and isolated GUI checks |
| Linux ARM64 / aarch64 | Ubuntu 22.04 ARM64 | .deb, AppImage | Native CI installation and isolated GUI checks; hardware IPC testing pending |

ARM means ARM64 here, not 32-bit ARM. Windows ARM64 is outside this change.
Other Linux distributions may run the AppImage, but are not yet validated.
The desktop App itself must work on the target OS and expose compatible IPC;
packaging the bridge does not establish compatibility with every App version.

User-supplied diagnostics from an Ubuntu 22.04 x64 VM confirmed this layout:

```text
Installed package: chatgpt 26.930.31730
/usr/bin/chatgpt -> /usr/lib/chatgpt/codex-launcher
Bundled runtime: /usr/lib/chatgpt/resources/codex
codex:// handler: chatgpt.desktop
```

Runtime discovery resolves desktop launchers, checks their bundled resources,
then common installation directories and finally a PATH CLI. Nonstandard
installations can set the runtime path explicitly in Advanced settings or with
`--codex-bin`. Discovery does not launch the desktop or send model requests.
Linux uses `xdg-open` for desktop links and `ip -j -4 address show up` for IPv4
enumeration; the existing hostname fallback remains available if `ip` fails.

Preview 3 discovery resolves symlinked shell launchers to an executable sibling
GUI, including the Ubuntu `codex-launcher` / `ChatGPT` layout. It never executes
launch scripts during discovery, and ignores non-executable files and standalone
`codex` / `claude` CLI binaries. Claude's native Console auto-connection helper
currently supports macOS and Windows only; Linux reports that limitation.

## Session permission options

The main and side-chat pickers read runtime profiles, workspace/administrator
requirements and the desktop's Full access visibility setting each time they
open, and recheck before saving. Disabled choices explain the missing capability
or setting. Enable Full access in Codex desktop settings first if it is hidden,
then refresh the picker. Bridge never edits those desktop preferences.

Approve for me is not assumed available based on OS or the runtime's enum alone.
For native chats, Bridge needs evidence from the current chat, the desktop's
saved default selection, or explicit Codex configuration. If availability is
unconfirmed, choose the mode on the desktop first and reopen the picker. A side
chat uses its own runtime's feature availability. Both obey policy restrictions;
older runtimes that cannot report capabilities require changing permissions on
the desktop. Custom permission policies remain custom until explicitly changed.
Side chats display a permission change only after the runtime confirms it.

## Build on the Target Architecture

Use Ubuntu 22.04 or a compatible build environment, Node.js 24, Python 3.9+
(CI uses 3.13), npm, and the project's pinned desktop requirements. Build the
Python runtime and Electron app on the same OS and architecture. An ARM64
Electron shell cannot run an x64 Python runtime.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-desktop.txt
npm ci
python scripts/build-desktop.py
```

On an x64 host:

```sh
npm run build:linux -- --x64
```

On an ARM64 host:

```sh
npm run build:linux -- --arm64
```

The build hook rejects missing or mismatched gateway metadata. Outputs go to
`dist/desktop/Codex-Mobile-Bridge-<version>-Linux-<arch>.deb` and `.AppImage`.
For x64, electron-builder uses `amd64` in .deb filenames and `x86_64` in
AppImage filenames. Both ARM64 package formats use `arm64`.
The existing release workflow collects both architectures and includes these
files in `SHA256SUMS.txt`. Linux is deliberately excluded from the macOS/Windows
signed in-app update manifest until a Linux update/recovery strategy is tested.

To test the local build without installing it over an existing package:

```sh
./start-linux.sh
```

This opens the compiled desktop with separate preferences and login credentials
in `.local/linux-preview`; its controller log is `desktop.log` in that directory.
The directory persists across restarts. Set `CMB_DATA_DIR` to choose another
location. Start/stop the HTTP gateway using the desktop controls. Its default
port is 8787; change it in Network settings if another gateway uses that port.
The original Codex/ChatGPT data remains at its existing location.

The terminal tests use zsh when installed and otherwise exercise Bash with an
isolated startup file. A minimal Ubuntu desktop does not need zsh for testing.

## Install and Launch

After obtaining a verified package for your architecture, compare its SHA-256
with the release checksums. Run the bridge as the same ordinary desktop user
that runs ChatGPT/Codex, never with `sudo`.

For a .deb, replace the example filename with the actual downloaded package:

```sh
sudo apt install ./Codex-Mobile-Bridge-VERSION-Linux-amd64.deb
codex-mobile-bridge
```

The .deb declares its desktop libraries, `xdg-utils`, and `iproute2`. For
AppImage, grant execute permission to that file and run it as your user. If
the environment cannot mount AppImages, try its `--appimage-extract-and-run`
option or prefer the .deb. Do not disable Chromium's sandbox or run the GUI
as root to work around startup failures.

Keep the original desktop App running. The bridge retains the shared Chinese
and English interface and stores preferences under Electron's user data
directory, not under `/opt`. Closing the Linux control window leaves the
gateway running. Reopen the App to manage it; click Stop before closing if you
want the gateway stopped. Linux does not depend on a GNOME tray extension.

In-app updates are disabled for Linux. Stop the gateway and exit the controller
before installing a newer .deb or replacing the AppImage. Keep the user data
directory. Do not use the macOS/Windows directory-swap updater for Linux.

## Source Mode and Desktop IPC

Source mode needs Python 3.9+ but not npm or Electron:

```sh
python3 run.py --lan --codex-bin /usr/lib/chatgpt/resources/codex
```

Run this in the logged-in desktop session. A headless SSH session may lack the
display/session bus needed by `xdg-open`. The default Unix socket is
`${CODEX_HOME:-$HOME/.codex}/ipc/ipc.sock`; set `--ipc-path` or the Advanced
setting if the installed App uses another endpoint. Verify the socket instead
of assuming its location:

```sh
ls -l "${CODEX_HOME:-$HOME/.codex}/ipc/"
xdg-mime query default x-scheme-handler/codex
/usr/lib/chatgpt/resources/codex --version
```

Do not expose the desktop Unix socket over TCP. The HTTP gateway provides its
own authentication; LAN and tunnel options do not disable login protection.

## VMware and Phone Access

`127.0.0.1` always means the device opening the URL, not the VM. Select a
reachable VM interface in Network settings and save before starting the
gateway. Keep authentication enabled.

- Bridged networking can give the VM a LAN address reachable by the phone.
- VMware NAT commonly gives the VM a private VMnet address reachable by the
  host but not directly by other LAN devices. It may need explicitly configured
  forwarding, bridged networking, or a tunnel.
- The desktop can install the official `cloudflared-linux-amd64` or
  `cloudflared-linux-arm64` binary. HTTPS source validation and SHA-256 checking
  occur before execution; the binary stays in the gateway's private data area.
- Check Ubuntu and host firewall rules for the chosen path. Do not disable
  firewalls or expose ports automatically. A reachable interface address alone
  does not prove that a phone can connect.

## Validation

The Linux jobs in `.github/workflows/desktop.yml` use independent native x64
and ARM64 runners on Ubuntu 22.04. They build both runtimes, install the .deb,
verify architecture/version metadata, launch the real packaged GUI under a
session bus and Xvfb, and exercise authentication, notifications, QR login,
TLS, language persistence, and closing/reopening the controller. The AppImage
payload is extracted and launched separately; this does not verify a real
desktop's FUSE mounting or Wayland behavior. Screenshots are retained as CI
artifacts. These jobs passed on the Ubuntu integration branch; each release reruns the same gates.

For a local installed package, use Node.js 24 in the source checkout:

```sh
node scripts/smoke-linux.cjs /usr/bin/codex-mobile-bridge
```

The smoke check uses a private temporary data directory and a nonexistent
desktop socket, so it never opens or sends messages to real chats. It does not
prove compatibility with a particular desktop App's internal IPC.

Before promoting Linux support, validate actual IPC connection, read-only
session listing, desktop links, and an explicitly approved test conversation
on the Ubuntu VM and on ARM64. Also check native Wayland/XWayland, restart,
package upgrades, and phone access using the chosen VMware networking mode.
