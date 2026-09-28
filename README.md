# chrome-instance

> [!WARNING]
> This project was developed with the assistance of AI. Review all code before
> using it. `chrome-instance` creates and overwrites launcher and icon files in
> your home directory, and `-d` deletes an instance's Chrome data directory —
> its logins, history and settings — after asking for confirmation.

Run separate Google Chrome instances as their own desktop apps. Each instance
gets its own launcher (`.desktop` file), data directory, process, logins, and
dock icon — optionally colored to match that Chrome's theme — without copying
`~/.config/google-chrome`.

Chrome's profiles share one instance; this gives each its own.

Not affiliated with or endorsed by Google.

## Contents

- [Why](#why)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Usage](#usage)
- [What it creates](#what-it-creates)
- [Web apps (PWAs)](#web-apps-pwas)
- [Icons](#icons)
- [Listing](#listing)
- [Deleting](#deleting)
- [Menu refresh](#menu-refresh)
- [Upgrade and uninstall](#upgrade-and-uninstall)
- [Development](#development)
- [TODO](#todo)
- [License](#license)

## Why

Chrome's built-in profiles (`--profile-directory=...`) all share one data
directory and one browser process. On GNOME/dash-to-dock, every window from
that process shares a single window class, so all profiles group under the same
dock icon — whichever profile launched first "wins" the icon.

Giving each launcher its own `--user-data-dir` makes it a completely separate
Chrome instance. Combined with a unique `--class` / `StartupWMClass`, the dock
treats each one as a different app. Web apps installed from a launcher get
their own dock icons too (see [Web apps](#web-apps-pwas)).

A new data directory starts empty: Chrome runs its first-launch setup and you
sign in. Nothing is copied from your main Chrome config. Each data directory can
still hold multiple Chrome profiles of its own if you add them from inside that
launcher's Chrome.

## Requirements

- Linux with a freedesktop.org desktop. Built for GNOME on Wayland (tested with
  the stock dock and Dash to Panel); the launchers are standard `.desktop`
  files and work on other desktops too. See [Web apps](#web-apps-pwas) for the
  one X11 limitation.
- Google Chrome from Google's `.deb`/`.rpm` package, which installs
  `/usr/bin/google-chrome-stable` and the logo
  `/opt/google/chrome/product_logo_256.png`. For a different location, set
  `CHROME_BIN` and `CHROME_LOGO` (see [Usage](#usage)).
- Python 3.9 or later, which every current major distribution ships.
- [pipx] (recommended) or Python's `venv` module, to install the package and
  its two Python dependencies in their own environment, separate from the
  system's Python packages:
  - [materialyoucolor] — Google's Material color library, which Chrome also
    uses to derive its colors from a theme color
  - [Pillow] — image loading and drawing
- Optional: `update-desktop-database` (package `desktop-file-utils`, installed
  on most desktops), to refresh the "Open With" menus.

[pipx]: https://pipx.pypa.io/
[materialyoucolor]: https://pypi.org/project/materialyoucolor/
[Pillow]: https://pypi.org/project/pillow/

## Install

### With pipx (recommended)

1. Install pipx from your distribution:

   | Distribution | Command |
   |---|---|
   | Debian, Ubuntu, Mint, Pop!_OS | `sudo apt install pipx` |
   | Fedora | `sudo dnf install pipx` |
   | Arch, Manjaro | `sudo pacman -S python-pipx` |
   | openSUSE | `sudo zypper install python313-pipx` (match the number to `python3 --version`; `zypper search pipx` lists them) |
   | AlmaLinux, Rocky | `sudo dnf install epel-release && sudo dnf install pipx` |
   | RHEL | [enable EPEL](https://docs.fedoraproject.org/en-US/epel/), then `sudo dnf install pipx` |

2. Make sure `~/.local/bin`, where pipx puts commands, is on your `PATH`
   (this changes your shell's startup file only if needed; open a new
   terminal afterwards):

   ```bash
   pipx ensurepath
   ```

3. Install chrome-instance, straight from GitHub:

   ```bash
   pipx install git+https://github.com/matthewlenz/chrome-instance.git
   ```

   or from a local clone:

   ```bash
   git clone https://github.com/matthewlenz/chrome-instance.git
   cd chrome-instance
   pipx install .
   ```

4. Check that it works:

   ```bash
   chrome-instance --help
   ```

On RHEL 9 and its rebuilds, whose `python3` is 3.9, install Python 3.11 too
(`sudo dnf install python3.11`) and add `--python python3.11` to the
`pipx install` command, so the dependencies install from prebuilt packages
instead of being compiled.

### Without pipx

pipx only automates the following: a virtual environment for the package and
a link to its command. To do it by hand (on Debian and Ubuntu, first
`sudo apt install python3-venv`):

```bash
git clone https://github.com/matthewlenz/chrome-instance.git
cd chrome-instance
python3 -m venv ~/.local/share/chrome-instance/venv
~/.local/share/chrome-instance/venv/bin/pip install .
mkdir -p ~/.local/bin
ln -s ~/.local/share/chrome-instance/venv/bin/chrome-instance ~/.local/bin/
```

`~/.local/bin` must be on your `PATH`; most distributions add it
automatically once the directory exists (log out and back in).

## Quick start

```bash
chrome-instance -m "Work" work       # 1. create the launcher "Chrome - Work"
```

2. Open **Chrome - Work** from the app menu and sign in. It's a brand-new
   Chrome: nothing is copied from your main one.
3. Optional, to tell instances apart in the dock: in that Chrome, open
   ⋮ → **Customize Chrome** and pick a color, then run

   ```bash
   chrome-instance --recolor work   # icon now matches that color
   ```

Repeat with another NAME for each separate Chrome you want.

## Usage

```
chrome-instance [options] NAME
```

`NAME` is a short id (letters, digits, `-`, `_`). Options may come before or
after it.

| Option | Description |
|---|---|
| `-m`, `--menu TEXT` | Menu name becomes `Chrome - TEXT` (default: NAME) |
| `-i`, `--icon FILE` | Use your own image as the icon. Default: the Chrome logo, or with `-f`, the logo in the instance's theme color if it has one (see [Icons](#icons)) |
| `-r`, `--recolor` | Only redraw NAME's icon in the theme color picked in that Chrome (see [Icons](#icons)) |
| `-f`, `--force` | Recreate an existing launcher and icon; the data directory is kept. Pass `-m`/`-i` again if you used them, or the menu name resets to NAME and the icon to the theme color (or stock logo). |
| `-d`, `--delete` | Remove NAME's launcher, icon, data directory and cache, confirming each (see [Deleting](#deleting)) |
| `-l`, `--list` | List the standard Chrome and all instances, with their profiles and web apps, and show which is the default browser (see [Listing](#listing)); takes no NAME |
| `-h`, `--help` | Show help |

`-r`, `-d` and `-l` can't be combined with other options.

| Environment | Purpose | Default |
|---|---|---|
| `CHROME_BIN` | Chrome program the launcher runs | `/usr/bin/google-chrome-stable` |
| `CHROME_LOGO` | Chrome logo, used as the default icon and for `--recolor` | `/opt/google/chrome/product_logo_256.png` |

For example, this makes a launcher for Chrome Beta, installed alongside the
stable version by Google's `google-chrome-beta` package:

```bash
CHROME_BIN=/usr/bin/google-chrome-beta \
CHROME_LOGO=/opt/google/chrome-beta/product_logo_256.png chrome-instance -m Beta beta
```

`CHROME_BIN` is written into the launcher, so set it again when recreating
one with `-f`; `CHROME_LOGO` is only read when drawing the icon, so set it
for `--recolor` too.

Options are checked, and the icon is prepared, before anything is written, so
a typo or unreadable image leaves nothing behind.

Exit status: 0 on success, 1 on an error (such as a missing Chrome or an
existing launcher without `-f`), 2 for invalid options, 130 if interrupted
with Ctrl-C.

### Examples

```bash
chrome-instance work                              # "Chrome - work", stock Chrome icon
chrome-instance -m "acme.com" acme                # "Chrome - acme.com"
chrome-instance --recolor acme                    # icon in acme's Chrome theme color
chrome-instance acme -f -i ~/Pictures/acme.png    # custom icon
chrome-instance -d acme                           # remove it (asks for each item)
chrome-instance -l                                # show all Chromes and the default browser
```

After creating one, open it from the app menu and sign in to Chrome.

## What it creates

For `NAME=work`:

| Path | Purpose |
|---|---|
| `~/.config/chrome-instances/work/` | Chrome `--user-data-dir` (starts empty apart from a one-line `Local State`) |
| `~/.cache/chrome-instances/work/` | Chrome's disk cache for it, created by Chrome |
| `~/.local/share/applications/chrome-work.desktop` | The launcher |
| `~/.local/share/icons/chrome-work.<hash>.png` | The launcher icon, named after a hash of the image (see [Menu refresh](#menu-refresh)) |

Paths follow `$XDG_CONFIG_HOME`, `$XDG_CACHE_HOME` and `$XDG_DATA_HOME` if set.

The data directory sits under `~/.config`, next to Chrome's own
`~/.config/google-chrome`, for a practical reason: only when the data directory
is under `~/.config` does Chrome put its disk cache (`Cache`, `Code Cache`)
under `~/.cache`. Anywhere else, the cache — easily a gigabyte or more per
instance — stays inside the data directory, where backups include it and cache
cleaners don't touch it.

If the data directory already exists — for example you deleted a launcher with
`-d` but kept its data — running `chrome-instance NAME` again reuses it with
all its logins; only the launcher and icon are recreated.

The generated launcher:

```ini
[Desktop Entry]
Version=1.0
Type=Application
Name=Chrome - work
GenericName=Web Browser
Comment=Google Chrome (work profile)
Exec=env CHROME_DESKTOP=chrome-work.desktop /usr/bin/google-chrome-stable --user-data-dir=/home/USER/.config/chrome-instances/work --class=chrome-work %U
Icon=/home/USER/.local/share/icons/chrome-work.1a2b3c4d.png
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
StartupWMClass=chrome-work
MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;
Actions=new-window;new-private-window;

[Desktop Action new-window]
Name=New Window
Exec=... same command ...

[Desktop Action new-private-window]
Name=New Incognito Window
Exec=... same command ... --incognito
```

Key points:

- `--user-data-dir` — the separate data directory; this is what makes it an
  independent Chrome process.
- `--class` + `StartupWMClass` — must match; this is how GNOME/the dock ties
  windows to this launcher and its icon.
- `CHROME_DESKTOP` — meant to tell Chrome which launcher started it, so "Make
  Chrome your default browser" registers `chrome-work.desktop` rather than the
  main `google-chrome.desktop`. Not verified yet; see [TODO](#todo).
- No `--profile-directory` — Chrome opens the last-used profile in the data
  directory, so adding more profiles inside a launcher works naturally.
- Right-click actions for a new window and a new incognito window.
- Arguments are quoted and escaped as the
  [Desktop Entry Specification](https://specifications.freedesktop.org/desktop-entry-spec/latest/)
  requires, only when needed, so home directories containing spaces, `$`,
  `%`, quotes or backslashes work.

## Web apps (PWAs)

Web apps installed from a launcher get their own `.desktop` files from Chrome,
with the right `--user-data-dir` already in them. Chrome names each one after
the app and the *profile folder* inside the data directory:

```
chrome-<app-id>-<profile folder>.desktop
```

On Wayland, the app window's `app_id` uses the same name, and that is what
GNOME uses to choose its dock icon.

Chrome would name the first profile folder `Default` in every data directory.
The same app installed from two launchers (Outlook, Gmail — anything whose URL
is the same for every account) would then get the same file name and the same
`app_id`: the second install overwrites the first launcher, and both windows
stack under one dock icon.

To prevent that, a new data directory is created with a one-line
`Local State` telling Chrome to name its first profile folder after NAME
instead of `Default`. Outlook installed from `work` and from `acme` becomes
`chrome-<id>-work.desktop` and `chrome-<id>-acme.desktop`, each with its own
dock icon. Existing data directories are never modified.

Limits:

- **X11 sessions:** app windows are matched by `StartupWMClass=crx_<app-id>`,
  which doesn't include the profile, so the same app from two launchers still
  shares a dock icon.
- **Extra profiles inside a launcher** get Chrome's usual names (`Profile 1`,
  `Profile 2`, …), which can repeat across launchers. Install a shared web app
  from each launcher's first profile to keep them apart.
- Apps whose URL differs per account (e.g. each Slack workspace) get different
  app ids and never collided in the first place.

## Icons

A new launcher starts with the stock Chrome logo. To tell launchers apart,
give each Chrome its own theme color, then match the icon to it:

1. In that launcher's Chrome, open ⋮ → **Customize Chrome** → pick a color.
2. Run `chrome-instance --recolor NAME`.

The logo's red, green and yellow segments are redrawn in three tones of the
theme — the same Material palette Chrome derives from that color for its own
window frame (dark on top, light bottom-left, muted right). The blue center and
white ring stay as they are, so it still reads as Chrome. `--recolor` only
replaces the icon file; the launcher, menu name and data are untouched. Run it
again whenever you change the color.

- The theme is read from the profile Chrome last used in that data directory.
- Grayscale themes give a gray icon. Theme extensions and "Use GTK/Qt" have no
  single theme color, so `--recolor` reports that and leaves the icon alone.
- Only Chrome's default color style is reproduced exactly; with another style
  `--recolor` still draws the icon but prints a note.
- Chrome saves a color change to disk within a few seconds; if `--recolor`
  still sees the old color, run it again.
- The dash/dock shows the new icon right away; the overview's app grid and
  the app menu may keep showing the old one for a while (see
  [Menu refresh](#menu-refresh)).

`--force` on an existing data directory also uses its theme color when it has
one; otherwise it uses the stock logo.

`--icon` accepts any image format Pillow reads — PNG, JPEG, GIF, WebP, BMP,
ICO and more, but not SVG (convert an SVG to PNG first). The image is saved
as PNG; for an `.ico` the largest size is used, for an animation the first
frame.

## Listing

`chrome-instance --list` (or `-l`) shows every Chrome on the system: the
standard Google Chrome (and its beta, dev and canary channels, if installed or
used), then each instance. For example:

```
Default browser: Chrome - Work (chrome-work.desktop)

google-chrome: Google Chrome  [standard]  [running]
  Launcher:  /usr/share/applications/google-chrome.desktop
  Chrome:    /usr/bin/google-chrome-stable
  Data dir:  ~/.config/google-chrome (1.4G)
  Cache:     ~/.cache/google-chrome (1.4G)
  Profiles:  Default: Personal <me@gmail.com>, default theme
  Web apps:  Gmail

work: Chrome - Work  [default browser]
  Launcher:  ~/.local/share/applications/chrome-work.desktop
  Chrome:    /usr/bin/google-chrome-stable
  Data dir:  ~/.config/chrome-instances/work (1.2G)
  Cache:     ~/.cache/chrome-instances/work (73M)
  Profiles:  work: Work <me@work.example>, theme #4CAF50
             Profile 1: Side project, grayscale theme, last used
  Web apps:  Microsoft Teams, Outlook
```

For each one it shows:

- **Tags:** `[standard]` for Google's own Chrome, `[default browser]`, and
  `[running]` if a Chrome is using its data directory right now.
- **Launcher** and the **Chrome** program it starts. An instance whose
  launcher was deleted but whose data remains says how to recreate it.
- **Data dir** and **Cache**, with their size on disk.
- **Profiles** inside the data directory, from Chrome's `Local State`: the
  name, the signed-in Google account, and the theme (with a color swatch
  when printing to a terminal; set `NO_COLOR` to turn it off). "last used"
  marks the profile Chrome opens, which `--recolor` reads, when there are
  several. A new instance shows "none yet" until it's first launched.
- **Web apps** installed from it (see [Web apps](#web-apps-pwas)).

The default browser comes from `xdg-settings get default-web-browser`, which
asks the desktop the same way apps do, falling back to
`xdg-mime query default x-scheme-handler/https`. If it's not one of the
listed Chromes, the top line still names it (e.g. Firefox).

`--list` only reads files; it changes nothing.

## Deleting

`chrome-instance -d NAME` prompts separately for the launcher, the icon, the
data directory, and its cache in `~/.cache` (showing sizes). Only `y`/`yes`
deletes; anything else, including just pressing Enter, keeps the item. It
refuses to run while Chrome is using that data directory (detected through
Chrome's own `SingletonLock` in the data directory, so it works however Chrome
was started).

Web apps installed from that instance keep their own launchers
(`~/.local/share/applications/chrome-<app-id>-NAME.desktop`); uninstall them
from inside that Chrome first, or remove those files afterwards.

## Menu refresh

GNOME Shell watches `~/.local/share/applications` and picks up new or removed
launchers automatically. After creating or deleting, `chrome-instance` also
runs `update-desktop-database` (when installed) to refresh the MIME cache so
each launcher appears correctly under "Open With" and in default-browser
settings.

Icons are harder: GNOME Shell caches icon images by file name, and icons
already on screen (the dash, the overview's app grid) don't redraw when their
file changes. An icon rewritten in place took around five minutes to show up
in testing, and only newly added dash entries showed it sooner.

So `chrome-instance` never rewrites an icon in place. Each icon is named after
a hash of the image (`chrome-NAME.1a2b3c4d.png`); a new image (`--recolor`,
`-f`, `--icon`) gets a new file, the launcher's `Icon=` is pointed at it, and
the old file is deleted. GNOME sees the launcher change and reloads the app.
In testing, Dash to Dock then showed the new icon immediately, but the
overview's app grid and the app menu kept the old one for a while (they
catch up eventually, and at the latest after logging out and back in). Icons
from older versions (`chrome-NAME.png`) are replaced the same way the next
time they're written.

## Upgrade and uninstall

Upgrade to the latest version:

- With pipx: `pipx reinstall chrome-instance`. It reinstalls from wherever
  you installed from: GitHub fetches the latest version; for a clone, run
  `git pull` in it first.
- Without pipx: in the clone, run
  `git pull && ~/.local/share/chrome-instance/venv/bin/pip install --upgrade .`

Existing launchers keep working across upgrades; recreate one with `-f` to
pick up changes to the generated launcher.

To uninstall, first delete each instance you no longer want with
`chrome-instance -d NAME`, then remove the tool the way you installed it:

```bash
pipx uninstall chrome-instance                                       # with pipx
rm -r ~/.local/share/chrome-instance ~/.local/bin/chrome-instance    # without pipx
```

Removing the tool leaves existing instances working; they're ordinary
launchers and Chrome data directories.

## Development

The whole tool is one module, `chrome_instance.py`; `pyproject.toml` declares
its dependencies and the `chrome-instance` command. To work on it, install it
in editable mode in a virtual environment inside the clone, so changes take
effect without reinstalling:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/chrome-instance --help
```

To try changes without touching your real launchers and Chrome data, point
the XDG directories somewhere temporary:

```bash
export XDG_CONFIG_HOME=/tmp/ci-test/config XDG_DATA_HOME=/tmp/ci-test/data XDG_CACHE_HOME=/tmp/ci-test/cache
.venv/bin/chrome-instance -m Test test
```

## TODO

### "Make Chrome your default browser" from an instance

**Problem:** Chrome registers itself as the default browser by the name of its
own launcher, which it assumes is `google-chrome.desktop` (it runs roughly
`xdg-settings set default-web-browser google-chrome.desktop`). Clicking "Make
default" inside an instance would then make the *main* Chrome the default, so
links from other apps open in the main data directory instead of the instance.
The same name is used for Chrome's "is it the default?" check, so an instance
may also keep showing the "Chrome isn't your default browser" prompt.

**Current attempt (untested):** launchers set
`CHROME_DESKTOP=chrome-NAME.desktop` in `Exec=`, which Chromium reads to learn
its launcher name. Launching with it works (the window class and app_id are
unaffected), but it's unconfirmed that this Chrome build honors it for the
default-browser setting — the string wasn't found in the binary, which is
inconclusive either way.

**To verify:**

1. Note the current default: `xdg-settings get default-web-browser`, to
   restore afterwards.
2. Create a test instance, open it, go to `chrome://settings/defaultBrowser`,
   and click "Make default".
3. Check `chrome-instance --list` (or `xdg-settings get default-web-browser`):
   `chrome-NAME.desktop` as the default browser means
   `CHROME_DESKTOP` works; `google-chrome.desktop` means it's ignored.
4. Restore the original default and delete the test instance.

**Fallback if it's ignored:**

- Add a `--make-default NAME` option that runs
  `xdg-settings set default-web-browser chrome-NAME.desktop` directly (GNOME
  Settings → Default Apps also works, since the launchers declare the browser
  MIME types).
- Consider adding `--no-default-browser-check` to the instances' `Exec=` so
  they don't keep prompting.
- Update the "Key points" section above either way.

## License

[MIT](LICENSE): use, modify and share it however you like, as long as the
copyright notice stays with copies of the code. It comes with no warranty.
