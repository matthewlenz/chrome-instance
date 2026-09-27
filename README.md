# chrome-instance

> [!WARNING]
> This project was developed with the assistance of AI. Review all code before
> using it. The script creates and overwrites launcher and icon files in your
> home directory, and `-d` deletes an instance's Chrome data directory — its
> logins, history and settings — after asking for confirmation.

Run separate Google Chrome instances as their own desktop apps. Each instance
gets its own launcher (`.desktop` file), data directory, process, logins, and
dock icon — optionally colored to match that Chrome's theme — without copying
`~/.config/google-chrome`.

Chrome's profiles share one instance; this gives each its own.

Not affiliated with or endorsed by Google.

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

## Install

```bash
install -m755 chrome-instance ~/.local/bin/
```

`~/.local/bin` must be on your `PATH`.

Requirements:

- Google Chrome at `/usr/bin/google-chrome-stable` (logo taken from
  `/opt/google/chrome/product_logo_256.png`); override with `CHROME_BIN` and
  `CHROME_LOGO`
- For theme-colored icons (`--recolor`): `python3` and ImageMagick
  (`convert` or `magick`). Not needed otherwise; ImageMagick is also used, when
  present, to convert `--icon` images to PNG.
- `update-desktop-database` (from `desktop-file-utils`), optional

Built for GNOME on Wayland (tested with the stock dock and Dash to Panel). The
launchers are standard `.desktop` files and work on other desktops too; see
[Web apps](#web-apps-pwas) for the one X11 limitation.

## Quick start

```bash
chrome-instance -l "Work" work       # 1. create the launcher "Chrome - Work"
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
| `-l`, `--label TEXT` | Menu name becomes `Chrome - TEXT` (default: NAME) |
| `-i`, `--icon FILE` | Use your own image as the icon instead of the Chrome logo |
| `-r`, `--recolor` | Only redraw NAME's icon in the theme color picked in that Chrome (see [Icons](#icons)) |
| `-f`, `--force` | Recreate an existing launcher and icon; the data directory is kept. Pass `-l`/`-i` again if you used them, or the label resets to NAME and the icon to the theme color (or stock logo). |
| `-d`, `--delete` | Remove NAME's launcher, icon, data directory and cache, confirming each |
| `-h`, `--help` | Show help |

| Environment | Default |
|---|---|
| `CHROME_BIN` | `/usr/bin/google-chrome-stable` |
| `CHROME_LOGO` | `/opt/google/chrome/product_logo_256.png` |

All options are checked before anything is written, so a typo leaves nothing
behind.

### Examples

```bash
chrome-instance work                              # "Chrome - work", stock Chrome icon
chrome-instance -l "acme.com" acme                # "Chrome - acme.com"
chrome-instance --recolor acme                    # icon in acme's Chrome theme color
chrome-instance acme -f -i ~/Pictures/acme.png    # custom icon
chrome-instance -d acme                           # remove it (asks for each item)
```

After creating one, open it from the app menu and sign in to Chrome.

## What it creates

For `NAME=work`:

| Path | Purpose |
|---|---|
| `~/.config/chrome-instances/work/` | Chrome `--user-data-dir` (starts empty apart from a one-line `Local State`) |
| `~/.cache/chrome-instances/work/` | Chrome's disk cache for it, created by Chrome |
| `~/.local/share/applications/chrome-work.desktop` | The launcher |
| `~/.local/share/icons/chrome-work.png` | The launcher icon |

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
Exec=env CHROME_DESKTOP=chrome-work.desktop /usr/bin/google-chrome-stable --user-data-dir="/home/USER/.config/chrome-instances/work" --class="chrome-work" %U
Icon=/home/USER/.local/share/icons/chrome-work.png
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
replaces the icon file; the launcher, label and data are untouched. Run it
again whenever you change the color.

- The theme is read from the profile Chrome last used in that data directory.
- Grayscale themes give a gray icon. Theme extensions and "Use GTK/Qt" have no
  single theme color, so `--recolor` reports that and leaves the icon alone.
- Chrome saves a color change to disk within a few seconds; if `--recolor`
  still sees the old color, run it again.
- The dock shows the new icon within a few seconds, or as soon as you open
  the overview (see [Menu refresh](#menu-refresh)).

`--force` on an existing data directory also uses its theme color when it has
one; otherwise it uses the stock logo.

`--icon` images are converted to PNG when ImageMagick is available (the first
frame for multi-image formats like `.ico`), otherwise copied as-is.

The script prefers the system `convert` over `magick`, because a `magick`
earlier in `PATH` may be a container wrapper (apx/distrobox) that can't see
`/opt/google/chrome`.

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
launchers automatically. After creating or deleting, the script also runs
`update-desktop-database` to refresh the MIME cache so each launcher appears
correctly under "Open With" and in default-browser settings.

Icons are harder: GNOME Shell caches them by path and doesn't watch the icon
files, so an icon rewritten in place (`--recolor`, `-f`) would stay stale until
you log out. It does notice when an icon directory's modification time changes,
and then reloads its icons, so after writing an icon the script touches
`~/.local/share/icons`. The dock (including Dash to Panel) updates within a
few seconds, or as soon as the overview is opened.

## Uninstall

Delete each instance you no longer want with `chrome-instance -d NAME`, then:

```bash
rm ~/.local/bin/chrome-instance
```

Removing the script leaves existing instances working; they're ordinary
launchers and Chrome data directories.


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
3. Check `xdg-settings get default-web-browser`: `chrome-NAME.desktop` means
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
