"""chrome-instance: run separate Google Chrome instances as their own desktop apps.

Chrome's built-in profiles all share one browser process, so on GNOME every
profile's windows group under one dock icon. This tool creates a launcher
(.desktop file) whose Chrome gets its own --user-data-dir and window class,
which makes it a fully separate Chrome: its own process, logins, and dock icon.
Nothing is copied from ~/.config/google-chrome; you sign in on first launch.

For an instance NAME it manages:

    ~/.config/chrome-instances/NAME/                  Chrome's --user-data-dir
    ~/.cache/chrome-instances/NAME/                   its disk cache (made by Chrome)
    ~/.local/share/applications/chrome-NAME.desktop   the launcher
    ~/.local/share/icons/chrome-NAME.<hash>.png       the launcher's icon

(following $XDG_CONFIG_HOME, $XDG_CACHE_HOME and $XDG_DATA_HOME when set).

The icon can be redrawn in the theme color picked inside that Chrome
(--recolor): the logo's red, green and yellow segments are repainted in the
same Material palette Chrome derives from that color for its own window frame.

Commands, chosen by options (see parse_args):

    create    (default)  write the launcher and icon; create the data dir
    --recolor            rewrite only the icon, from the instance's theme color
    --delete             remove the launcher, icon, data dir and cache, asking
                         before each
    -l, --list           show the standard Chrome and every instance, and
                         which one is the default browser (reads only)

See README.md for usage and background.
"""

import argparse
import hashlib
import io
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional

# Third-party dependencies, installed alongside the package by pip/pipx. When
# the file is run directly without them, say how to install instead of
# printing a traceback.
try:
    from materialyoucolor.hct import Hct
    from materialyoucolor.palettes.tonal_palette import TonalPalette
    from PIL import Image, ImageDraw, UnidentifiedImageError
except ImportError as err:
    sys.exit(f"chrome-instance: missing Python module '{err.name}'; "
             "install with `pipx install .` (see README)")

# Name used in messages: "chrome-instance" when run through the installed command.
PROG = os.path.basename(sys.argv[0])

# Where Google's Linux packages (.deb/.rpm) install Chrome and its logo.
# Overridable with the CHROME_BIN and CHROME_LOGO environment variables.
DEFAULT_CHROME_BIN = "/usr/bin/google-chrome-stable"
DEFAULT_CHROME_LOGO = "/opt/google/chrome/product_logo_256.png"

# The Chrome channels Google packages for Linux, which --list shows alongside
# the instances. Each channel's launcher is <name>.desktop, and it keeps its
# data in ~/.config/<name> and its cache in ~/.cache/<name>.
STANDARD_CHANNELS = ["google-chrome", "google-chrome-beta", "google-chrome-unstable",
                     "google-chrome-canary"]

# Allowed instance NAMEs. NAME ends up in file names, the window class and the
# profile folder name, so keep it to characters that are safe in all of them.
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

# The logo segments to recolor, counterclockwise from the top, as (chroma,
# tone) in the theme color's hue. These are Chrome's own default ("tonal
# spot") palette choices: its primary palette uses chroma 40 and its secondary
# chroma 16. Tone is lightness from 0 (black) to 100 (white), so the icon gets
# a dark top, a light bottom-left and a muted right segment.
SEGMENT_TONES = [
    (40, 30),  # top (red in the stock logo)
    (40, 80),  # bottom-left (green)
    (16, 50),  # right (yellow)
]

# The segments are drawn at this multiple of the logo's size and then scaled
# down, averaging SUPERSAMPLE x SUPERSAMPLE samples per pixel, which
# anti-aliases their edges.
SUPERSAMPLE = 4


def die(msg):
    """Print an error prefixed with the program name and exit with status 1."""
    sys.exit(f"{PROG}: {msg}")


def xdg_dir(var, default):
    """An XDG base directory: $var if set and non-empty, else ~/default."""
    return Path(os.environ.get(var) or Path.home() / default)


def instances_root():
    """The directory holding every instance's data dir.

    It lives under ~/.config like Chrome's own data dir: only then does Chrome
    put its disk cache under ~/.cache (same relative path) instead of inside
    the data dir, where backups would include it.
    """
    return xdg_dir("XDG_CONFIG_HOME", ".config") / "chrome-instances"


def chrome_running(data_dir):
    """True if a Chrome process is currently using data_dir.

    Chrome keeps a SingletonLock symlink in its data dir pointing at
    "<hostname>-<pid>". Checking that the pid is alive (signal 0 only tests,
    it sends nothing) skips a stale lock left behind by a crash. This works
    however that Chrome was started.
    """
    try:
        pid = int(os.readlink(data_dir / "SingletonLock").rsplit("-", 1)[1])
        if pid <= 0:  # os.kill would address a process group instead
            return False
        os.kill(pid, 0)
    except PermissionError:
        return True  # the process exists but belongs to someone else
    except (OSError, ValueError, IndexError):
        return False  # no lock, a malformed one, or a dead process
    return True


class Instance:
    """The files and names belonging to one Chrome instance, NAME.

    Only computes paths; nothing is read or created until a command runs.
    """

    def __init__(self, name):
        self.name = name
        data_home = xdg_dir("XDG_DATA_HOME", ".local/share")
        self.profile_dir = instances_root() / name
        self.cache_dir = xdg_dir("XDG_CACHE_HOME", ".cache") / "chrome-instances" / name
        self.desktop_file = data_home / "applications" / f"chrome-{name}.desktop"
        self.icons_dir = data_home / "icons"
        # Chrome's --class sets its windows' WM class (and Wayland app_id).
        # The launcher's StartupWMClass must match it for the dock to show
        # this launcher's icon on those windows.
        self.wm_class = f"chrome-{name}"

    def is_fresh(self):
        """True if the data dir doesn't exist yet or is empty, i.e. Chrome
        has never run with it."""
        return not self.profile_dir.is_dir() or not any(self.profile_dir.iterdir())

    def chrome_running(self):
        """True if a Chrome process is currently using the data dir."""
        return chrome_running(self.profile_dir)

    def icon_path(self, icon):
        """Where to store icon (PNG bytes): chrome-NAME.<hash>.png, named
        after a hash of the image itself.

        GNOME Shell caches icon images by file name and doesn't redraw icons
        already on screen when their file changes, so an icon rewritten in
        place can stay stale for minutes. A changed image therefore gets a new
        file name, and the launcher's Icon= is pointed at it: GNOME watches
        the launchers and reloads an app when its launcher changes, so the
        dash shows the new icon at once (the overview's app grid may still
        lag). NAME can't contain ".", so the names never overlap between
        instances.
        """
        return self.icons_dir / f"chrome-{self.name}.{hashlib.sha256(icon).hexdigest()[:8]}.png"

    def icon_files(self):
        """All of this instance's icon files that exist: chrome-NAME.<hash>.png,
        and chrome-NAME.png from versions before icons were named by hash."""
        pattern = re.compile(rf"chrome-{re.escape(self.name)}(\.[0-9a-f]{{8}})?\.png")
        return sorted(p for p in self.icons_dir.glob(f"chrome-{self.name}.*png")
                      if pattern.fullmatch(p.name))


def write_file(path, data):
    """Write bytes to path atomically: write a temporary file next to it,
    then rename it over path, so a failed write never leaves half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def png_bytes(image):
    """Encode a Pillow image as PNG file contents."""
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def install_icon(inst, icon):
    """Write icon (PNG bytes) to the instance's icons dir under its
    content-based name (see Instance.icon_path) and return that path. The
    launcher still needs to point at it; remove_old_icons then cleans up."""
    path = inst.icon_path(icon)
    inst.icons_dir.mkdir(parents=True, exist_ok=True)
    write_file(path, icon)
    return path


def remove_old_icons(inst, keep):
    """Delete the instance's icon files other than keep, the one its
    launcher now uses."""
    for path in inst.icon_files():
        if path != keep:
            path.unlink(missing_ok=True)


def set_launcher_icon(desktop_file, icon_path):
    """Point an existing launcher's Icon= line at icon_path, leaving the rest
    of the file as it is."""
    text = desktop_file.read_text()
    line = f"Icon={entry_string(str(icon_path))}"
    text, count = re.subn(r"^Icon=.*$", lambda _: line, text, count=1, flags=re.MULTILINE)
    if not count:
        text = text.replace("[Desktop Entry]\n", f"[Desktop Entry]\n{line}\n", 1)
    write_file(desktop_file, text.encode())


def update_desktop_database(inst):
    """Refresh the MIME cache of the launcher's directory, so the launcher is
    offered under "Open With" and in default-browser settings. Optional: skipped
    when desktop-file-utils isn't installed."""
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(inst.desktop_file.parent)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- Theme icon -------------------------------------------------------------------

class NoThemeColor(Exception):
    """The instance's Chrome has no single theme color to draw the icon in."""


def load_json(path):
    """Parse a JSON file, or return None if it's missing or unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Theme(NamedTuple):
    """A Chrome profile's theme, as read by profile_theme."""

    # "color" (a color picked in Customize Chrome), "grayscale", "default"
    # (nothing picked), "system" (Use GTK/Qt), "extension" (a theme
    # extension), or "missing" (no Preferences file: never launched)
    kind: str
    seed: Optional[int] = None  # the picked color as 0xRRGGBB, for "color"
    default_style: bool = True  # False if a non-default color style is set


def profile_theme(data_dir, profile):
    """Read the theme of one profile folder in a Chrome data dir.

    Chrome keeps each profile's theme in its "Preferences" file (JSON):

        extensions.theme.id             which kind of theme is active: "" or
                                        "user_color_theme_id" for a color
                                        picked in Customize Chrome, else an
                                        installed theme extension's id
        extensions.theme.system_theme   non-zero for "Use GTK"/"Use Qt"
        browser.theme.user_color        the picked color, as a signed ARGB int
        browser.theme.is_grayscale      the "Grayscale" choice
        browser.theme.color_variant     the color style (tonal spot, neutral,
                                        vibrant, ...); 0/1 are the default
        autogenerated.theme.color       the color, for older Chrome versions
                                        that generated a theme from it
    """
    prefs = load_json(data_dir / profile / "Preferences")
    if prefs is None:
        return Theme("missing")
    theme = prefs.get("browser", {}).get("theme", {})
    ext_theme = prefs.get("extensions", {}).get("theme", {})
    if ext_theme.get("system_theme", 0):
        return Theme("system")
    theme_id = ext_theme.get("id", "")
    if theme_id == "autogenerated_theme_id":
        seed = prefs.get("autogenerated", {}).get("theme", {}).get("color")
    elif theme_id in ("", "user_color_theme_id"):
        seed = theme.get("user_color")
    else:
        return Theme("extension")
    if theme.get("is_grayscale", False):
        return Theme("grayscale")
    if seed is None:
        return Theme("default")
    return Theme("color", seed & 0xFFFFFF,  # drop the alpha byte (and the sign)
                 theme.get("color_variant", 1) in (0, 1))


def read_theme(data_dir):
    """Find the theme color of the profile Chrome last used in data_dir, the
    one an instance's icon is drawn from.

    Returns (seed, profile): seed is the theme color as a 0xRRGGBB int, or
    None for a grayscale theme; profile is the profile folder's name. Raises
    NoThemeColor when the profile has no usable color.
    """
    # Chrome records the last used profile folder in "Local State" (JSON).
    state = load_json(data_dir / "Local State") or {}
    profile = state.get("profile", {}).get("last_used") or "Default"
    theme = profile_theme(data_dir, profile)
    if theme.kind == "missing":
        raise NoThemeColor(f"no Chrome profile in {data_dir} yet; launch it and pick a color first")
    if theme.kind == "system":
        raise NoThemeColor(f'profile "{profile}" uses the GTK/Qt system theme, which has no theme color')
    if theme.kind == "extension":
        raise NoThemeColor(f'profile "{profile}" uses a theme extension, which has no theme color')
    if theme.kind == "default":
        raise NoThemeColor(f'profile "{profile}" has no theme color; pick one in Customize Chrome > Color')
    if not theme.default_style:
        # Other styles derive their palettes differently; SEGMENT_TONES
        # follows the default one.
        print("note: only Chrome's default color style is reproduced exactly", file=sys.stderr)
    return theme.seed, profile


def segment_colors(seed):
    """The RGB color for each of SEGMENT_TONES, derived from the theme color
    seed (0xRRGGBB, or None for grayscale).

    Like Chrome, this keeps only the seed's hue and builds Material "tonal
    palettes" in that hue at a fixed chroma (colorfulness), then takes each
    segment's tone (lightness) from them. Grayscale uses chroma 0 throughout.
    """
    hue = 0 if seed is None else Hct.from_int(0xFF000000 | seed).hue
    colors = []
    for chroma, tone in SEGMENT_TONES:
        argb = TonalPalette.from_hue_and_chroma(hue, 0 if seed is None else chroma).tone(tone)
        colors.append(((argb >> 16) & 255, (argb >> 8) & 255, argb & 255))
    return colors


def segment_polygons(size):
    """The logo's three outer segments as polygons, in SEGMENT_TONES order,
    for a logo size pixels wide.

    In the Chrome logo, the white ring's outer radius is a quarter of the
    logo's width. Each boundary between two segments is a straight ray that
    starts on the ring (at 90, 210 and 330 degrees, counterclockwise from the
    right) and runs clockwise along the ring's tangent there, out past the
    logo's edge. A segment spans from one ray to the next, so its polygon is:
    the center, the start and far end of one ray, then the far end and start
    of the next. The part near the center ends up hidden under the ring, and
    the far ends reach twice the logo's size away so the straight edge
    between them clears the logo's round outline. The caller keeps only the
    logo's own opaque area, which trims everything else.
    """
    center, ring, reach = size / 2, size / 4, 2 * size

    def point(x, y):
        """Math coordinates around the center (y up) to image coordinates
        (y down, origin top left)."""
        return center + x, center - y

    rays = []  # (start, far end) of each boundary ray, in image coordinates
    for angle in (90, 210, 330):
        a = math.radians(angle)
        start = (ring * math.cos(a), ring * math.sin(a))
        direction = (math.sin(a), -math.cos(a))  # the tangent, turning clockwise
        rays.append((point(*start),
                     point(start[0] + reach * direction[0], start[1] + reach * direction[1])))
    return [[point(0, 0), *rays[i], *reversed(rays[(i + 1) % 3])] for i in range(3)]


def theme_icon(inst, logo_path):
    """Draw the instance's icon in its Chrome theme color.

    The logo's red, green and yellow segments are repainted in the theme's
    palette (see segment_colors); the blue center and white ring are kept.
    Returns (PNG bytes, description of the theme used). Raises NoThemeColor
    when the instance has no theme color, or ValueError for an unusable logo.
    """
    seed, profile = read_theme(inst.profile_dir)
    logo = open_image(logo_path)
    size = logo.width
    if logo.height != size:
        raise ValueError(f"{logo_path} isn't square ({logo.width}x{logo.height})")

    # Draw the segments and the ring at SUPERSAMPLE x size, then shrink, which
    # anti-aliases the edges. The same ring is also drawn into a mask of where
    # to keep the logo's own pixels: the blue center and the white ring.
    big = size * SUPERSAMPLE
    segments = Image.new("RGB", (big, big))
    draw = ImageDraw.Draw(segments)
    for polygon, color in zip(segment_polygons(big), segment_colors(seed)):
        draw.polygon(polygon, fill=color)
    # Pillow's ellipse box includes its right and bottom pixels, hence the - 1.
    ring_box = [big / 4, big / 4, big * 3 / 4 - 1, big * 3 / 4 - 1]
    ring = Image.new("L", (big, big))
    ImageDraw.Draw(ring).ellipse(ring_box, fill=255)
    draw.ellipse(ring_box, fill=(255, 255, 255))

    # reduce() averages each SUPERSAMPLE x SUPERSAMPLE block. Pixels on the
    # ring's edge get the white/segment blend from the drawing; only pixels
    # entirely inside the ring keep the logo's own colors.
    segments = segments.reduce(SUPERSAMPLE)
    inside_ring = ring.reduce(SUPERSAMPLE).point(lambda v: 255 if v == 255 else 0)
    icon = Image.composite(logo, segments.convert("RGBA"), inside_ring)
    icon.putalpha(logo.getchannel("A"))  # the logo's round outline and its edge

    what = "grayscale theme" if seed is None else f"theme #{seed:06X}"
    return png_bytes(icon), f'{what} from profile "{profile}"'


def open_image(path):
    """Load an image file as RGBA, in any format Pillow reads. For an .ico
    that's its largest size, for an animation its first frame. Raises
    ValueError if the file can't be read as an image (e.g. SVG)."""
    try:
        with Image.open(path) as image:
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as err:
        raise ValueError(f"can't read {path} as an image ({err})") from None


# --- Desktop entry ----------------------------------------------------------------
#
# Launcher files follow the freedesktop.org Desktop Entry Specification:
# https://specifications.freedesktop.org/desktop-entry-spec/latest/

# Characters that force an Exec= argument to be quoted.
EXEC_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")


def exec_arg(arg):
    """Quote one Exec= argument as the Desktop Entry spec requires.

    An argument containing a reserved character is wrapped in double quotes,
    with ", `, $ and \\ inside it escaped by a backslash. A literal % is
    written %% in any argument, since %U etc. are field codes.
    """
    if any(c in EXEC_RESERVED for c in arg):
        arg = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return arg.replace("%", "%%")


def entry_string(s):
    """Escape a value by the spec's general rules for string values.

    For Exec= this applies after exec_arg's quoting (readers undo it first),
    so a backslash in a quoted argument ends up as four backslashes.
    """
    return (s.replace("\\", "\\\\").replace("\n", "\\n")
            .replace("\t", "\\t").replace("\r", "\\r"))


def desktop_entry(inst, label, chrome_bin, icon_path):
    """The launcher file's contents for the instance, shown in menus as
    "Chrome - label".

    Every Exec= line starts the instance's Chrome with its own data dir and
    window class. There's no --profile-directory: Chrome opens the profile
    last used in that data dir, so extra profiles added inside it just work.
    """
    # CHROME_DESKTOP tells Chrome which launcher it was started from, so
    # "Make default browser" registers this launcher instead of
    # google-chrome.desktop (not verified yet; see the README's TODO).
    cmd = entry_string(" ".join(exec_arg(a) for a in [
        "env", f"CHROME_DESKTOP={inst.desktop_file.name}", chrome_bin,
        f"--user-data-dir={inst.profile_dir}", f"--class={inst.wm_class}"]))
    label = entry_string(label)
    # %U passes the URLs of links opened with this launcher (e.g. once it's
    # the default browser); the MimeType= line offers it for web pages and
    # http(s) links. The two actions are the dock's right-click menu entries.
    return f"""\
[Desktop Entry]
Version=1.0
Type=Application
Name=Chrome - {label}
GenericName=Web Browser
Comment=Google Chrome ({label} profile)
Exec={cmd} %U
Icon={entry_string(str(icon_path))}
Terminal=false
Categories=Network;WebBrowser;
StartupNotify=true
StartupWMClass={inst.wm_class}
MimeType=text/html;text/xml;application/xhtml+xml;x-scheme-handler/http;x-scheme-handler/https;
Actions=new-window;new-private-window;

[Desktop Action new-window]
Name=New Window
Exec={cmd}

[Desktop Action new-private-window]
Name=New Incognito Window
Exec={cmd} --incognito
"""


# --- Reading launchers ------------------------------------------------------------

def applications_dirs():
    """The directories launchers are installed in, highest priority first:
    ~/.local/share/applications, then those of $XDG_DATA_DIRS (by default
    /usr/local/share and /usr/share), per the XDG Base Directory spec."""
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [xdg_dir("XDG_DATA_HOME", ".local/share") / "applications",
            *(Path(d) / "applications" for d in data_dirs.split(":") if d)]


def find_launcher(desktop_id):
    """The launcher file for a desktop file id such as "google-chrome.desktop",
    as the desktop would pick it (a user's copy overrides the system's), or
    None if it isn't installed."""
    for folder in applications_dirs():
        if (folder / desktop_id).is_file():
            return folder / desktop_id
    return None


def read_desktop_entry(path):
    """The keys of a launcher's [Desktop Entry] group, with the spec's string
    escapes (\\s, \\n, \\t, \\r, \\\\) undone. Empty if unreadable."""
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r"}
    entry, in_group = {}, False
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return entry
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            in_group = line == "[Desktop Entry]"
        elif in_group and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            value = re.sub(r"\\(.)", lambda m: escapes.get(m.group(1), m.group(1)), value.strip())
            entry.setdefault(key.strip(), value)
    return entry


def exec_argv(entry):
    """A launcher's Exec= command as a list of arguments, without field codes
    like %U. The spec's quoting rules are a subset of the shell's, so shlex
    parses them. Empty if the line is missing or malformed."""
    try:
        args = shlex.split(entry.get("Exec", ""))
    except ValueError:
        return []
    return [a.replace("%%", "%") for a in args if not re.fullmatch(r"%[A-Za-z]", a)]


def arg_value(argv, option):
    """The value of an "--option=value" argument in argv, or None."""
    prefix = f"{option}="
    return next((a[len(prefix):] for a in argv if a.startswith(prefix)), None)


def chrome_command(argv):
    """The program an Exec= command runs, skipping a leading
    `env VAR=value ...` like the one in this tool's launchers."""
    for arg in argv:
        if arg != "env" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", arg):
            return arg
    return None


# --- Listing ----------------------------------------------------------------------

class Browser(NamedTuple):
    """One Chrome shown by --list: a standard channel or an instance."""

    id: str                  # the instance's NAME, or the channel's name
    label: str               # the launcher's menu name
    launcher: Optional[Path]  # None if the launcher is missing
    data_dir: Path
    cache_dir: Path
    standard: bool           # a standard Chrome channel rather than an instance


def instance_names():
    """The NAMEs of all instances: every data dir under instances_root(), plus
    any launcher this tool wrote whose data dir has since been deleted."""
    names = set()
    if instances_root().is_dir():
        names.update(p.name for p in instances_root().iterdir()
                     if p.is_dir() and NAME_PATTERN.fullmatch(p.name))
    # Chrome's web app launchers also start with "chrome-" (chrome-<app
    # id>-<profile>.desktop), so only count launchers whose command uses the
    # data dir this tool gives that NAME.
    folder = xdg_dir("XDG_DATA_HOME", ".local/share") / "applications"
    for path in folder.glob("chrome-*.desktop"):
        name = path.name[len("chrome-"):-len(".desktop")]
        if NAME_PATTERN.fullmatch(name):
            data_dir = arg_value(exec_argv(read_desktop_entry(path)), "--user-data-dir")
            if data_dir == str(Instance(name).profile_dir):
                names.add(name)
    return names


def find_browsers():
    """Every Chrome to list: the standard channels that are installed or have
    data, then the instances by NAME."""
    config = xdg_dir("XDG_CONFIG_HOME", ".config")
    cache = xdg_dir("XDG_CACHE_HOME", ".cache")
    browsers = []
    for channel in STANDARD_CHANNELS:
        launcher = find_launcher(f"{channel}.desktop")
        if launcher or (config / channel).is_dir():
            label = read_desktop_entry(launcher).get("Name", channel) if launcher else channel
            browsers.append(Browser(channel, label, launcher, config / channel, cache / channel, True))
    for name in sorted(instance_names()):
        inst = Instance(name)
        launcher = inst.desktop_file if inst.desktop_file.is_file() else None
        label = read_desktop_entry(launcher).get("Name", name) if launcher else name
        browsers.append(Browser(name, label, launcher, inst.profile_dir, inst.cache_dir, False))
    return browsers


def find_web_apps():
    """Web app launchers Chrome created, as {data dir: [app names]}.

    Chrome writes them to ~/.local/share/applications with --app-id in their
    command. An instance's apps name its --user-data-dir. A standard
    Chrome's apps don't, so they're matched by the Chrome command they run,
    whose name is also its data dir's (/opt/google/chrome-beta/
    google-chrome-beta uses ~/.config/google-chrome-beta).
    """
    config = xdg_dir("XDG_CONFIG_HOME", ".config")
    apps = {}
    folder = xdg_dir("XDG_DATA_HOME", ".local/share") / "applications"
    for path in sorted(folder.glob("*.desktop")):
        entry = read_desktop_entry(path)
        argv = exec_argv(entry)
        if not arg_value(argv, "--app-id"):
            continue
        data_dir = arg_value(argv, "--user-data-dir")
        if data_dir is None:
            command = chrome_command(argv)
            if not command:
                continue
            data_dir = config / Path(command).name.removesuffix("-stable")
        apps.setdefault(Path(data_dir), []).append(entry.get("Name", path.stem))
    return apps


class Profile(NamedTuple):
    """A Chrome profile inside a data dir, from its Local State."""

    folder: str      # e.g. "Default" or "Profile 1"
    name: str        # the name shown in Chrome's profile menu
    account: str     # the signed-in Google account's email, or ""
    last_used: bool  # the profile Chrome opens (and --recolor reads)


def chrome_profiles(data_dir):
    """The profiles in a data dir, in Chrome's menu order. Empty until
    Chrome has been launched with it.

    Chrome lists them in "Local State" (JSON): profile.info_cache maps each
    profile folder to its details, profile.profiles_order gives the menu
    order and profile.last_used the profile it opens.
    """
    info = (load_json(data_dir / "Local State") or {}).get("profile", {})
    cache = info.get("info_cache", {})
    order = [p for p in info.get("profiles_order", []) if p in cache]
    order += [p for p in cache if p not in order]
    return [Profile(p, cache[p].get("name", p), cache[p].get("user_name", ""),
                    p == info.get("last_used")) for p in order]


def default_browser():
    """The desktop file id of the default web browser (for example
    "google-chrome.desktop"), or None if it can't be determined.

    xdg-settings asks the desktop environment the way desktop apps do;
    xdg-mime, which reads the mimeapps.list files, is the fallback.
    """
    for cmd in (["xdg-settings", "get", "default-web-browser"],
                ["xdg-mime", "query", "default", "x-scheme-handler/https"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out.endswith(".desktop"):
            return out
    return None


def home_relative(path):
    """path as a string, with the home directory shortened to ~."""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + "/") else text


def color_swatch(seed):
    """A colored square showing the theme color, when printing to a terminal
    that allows color (see https://no-color.org); otherwise nothing."""
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return ""
    r, g, b = (seed >> 16) & 255, (seed >> 8) & 255, seed & 255
    return f" \x1b[38;2;{r};{g};{b}m■\x1b[0m"


def describe_theme(theme):
    """A short description of a profile's theme for --list, or None."""
    if theme.kind == "color":
        return f"theme #{theme.seed:06X}{color_swatch(theme.seed)}"
    return {"grayscale": "grayscale theme", "default": "default theme",
            "system": "GTK/Qt theme", "extension": "theme extension"}.get(theme.kind)


def print_browser(browser, default_id, web_apps):
    """Print one --list entry: a heading line, then indented details."""
    def row(key, value):
        """An aligned "Key: value" line; an empty key continues the previous row."""
        print(f"  {key + ':' if key else '':<11}{value}")

    tags = ["standard"] if browser.standard else []
    if browser.launcher and browser.launcher.name == default_id:
        tags.append("default browser")
    if chrome_running(browser.data_dir):
        tags.append("running")
    print(f"{browser.id}: {browser.label}" + "".join(f"  [{t}]" for t in tags))

    if browser.launcher:
        row("Launcher", home_relative(browser.launcher))
        command = chrome_command(exec_argv(read_desktop_entry(browser.launcher)))
        if command:
            row("Chrome", command)
    elif browser.standard:
        row("Launcher", "not installed")
    else:
        row("Launcher", f"missing; recreate it with: {PROG} {browser.id}")

    if not browser.data_dir.is_dir():
        row("Data dir", f"{home_relative(browser.data_dir)} (not created yet)")
        return
    row("Data dir", f"{home_relative(browser.data_dir)} ({disk_usage(browser.data_dir)})")
    if browser.cache_dir.is_dir():
        row("Cache", f"{home_relative(browser.cache_dir)} ({disk_usage(browser.cache_dir)})")

    profiles = chrome_profiles(browser.data_dir)
    if not profiles:
        row("Profiles", "none yet (launch it and sign in)")
    for i, profile in enumerate(profiles):
        details = [f"{profile.folder}: {profile.name}"
                   + (f" <{profile.account}>" if profile.account else "")]
        theme = describe_theme(profile_theme(browser.data_dir, profile.folder))
        if theme:
            details.append(theme)
        if profile.last_used and len(profiles) > 1:
            details.append("last used")
        row("Profiles" if i == 0 else "", ", ".join(details))

    apps = sorted(web_apps.get(browser.data_dir, []), key=str.lower)
    if apps:
        row("Web apps", ", ".join(apps))


# --- Commands ---------------------------------------------------------------------

def list_browsers():
    """--list: show the standard Chrome channels and every instance, with
    their launchers, data, profiles, web apps and which is the default
    browser. Only reads; changes nothing."""
    default_id = default_browser()
    if default_id:
        launcher = find_launcher(default_id)
        name = read_desktop_entry(launcher).get("Name") if launcher else None
        print(f"Default browser: {name} ({default_id})" if name else f"Default browser: {default_id}")
    else:
        print("Default browser: unknown (neither xdg-settings nor xdg-mime reported one)")

    browsers = find_browsers()
    web_apps = find_web_apps()
    for browser in browsers:
        print()
        print_browser(browser, default_id, web_apps)
    if not any(not b.standard for b in browsers):
        print()
        print(f"No instances yet; create one with: {PROG} NAME")


def confirm(prompt):
    """Ask a yes/no question on the terminal. Only "y" or "yes" (any case)
    counts as yes; Enter, anything else, or end of input means no."""
    try:
        reply = input(f"{prompt} [y/N] ")
    except EOFError:
        print()
        reply = ""
    return reply.strip().lower() in ("y", "yes")


def disk_usage(path):
    """Human-readable size on disk of a file or directory tree, like
    `du -sh`: 4.0K, 12M, 1.3G. Counts allocated blocks, not file lengths,
    and doesn't follow symlinks."""
    total = os.lstat(path).st_blocks * 512
    for root, dirs, files in os.walk(path):
        for entry in dirs + files:
            try:
                total += os.lstat(os.path.join(root, entry)).st_blocks * 512
            except OSError:
                pass  # removed while walking, or unreadable
    size, unit = float(total), ""
    for unit in ("", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            break
        size /= 1024
    # Like du, round up, and show one decimal for values below 10.
    if unit and size < 10:
        return f"{math.ceil(size * 10) / 10:.1f}{unit}"
    return f"{math.ceil(size)}{unit}"


def delete(inst):
    """--delete: remove the instance's launcher, icon, data dir and cache,
    asking before each one. Refuses while its Chrome is running, since
    Chrome would keep writing to the data dir.

    Web app launchers Chrome created from this instance aren't touched; the
    README explains how to remove them.
    """
    if inst.chrome_running():
        die(f"Chrome is running with {inst.profile_dir}; close it first.")
    found = False
    for target in (inst.desktop_file, *inst.icon_files(), inst.profile_dir, inst.cache_dir):
        if not os.path.lexists(target):
            continue
        found = True
        # A symlink is removed itself, never the directory it points to.
        is_dir = target.is_dir() and not target.is_symlink()
        if target == inst.cache_dir:
            desc = f"cache {target} ({disk_usage(target)})"
        elif is_dir:
            desc = f"data directory {target} ({disk_usage(target)}; logins, history, profiles)"
        else:
            desc = str(target)
        if confirm(f"Delete {desc}?"):
            if is_dir:
                shutil.rmtree(target)
            else:
                target.unlink()
            print("  deleted")
        else:
            print("  kept")
    if not found:
        die(f"Nothing found for '{inst.name}'.")
    update_desktop_database(inst)


def recolor(inst, logo):
    """--recolor: redraw only the instance's icon in its current theme
    color. The launcher, menu name and data dir are left alone."""
    if not os.path.lexists(inst.desktop_file):
        die(f"no launcher for '{inst.name}' (create it first)")
    if not os.path.isfile(logo):
        die(f"Chrome logo not found at {logo} (set CHROME_LOGO)")
    try:
        icon, desc = theme_icon(inst, logo)
    except (NoThemeColor, ValueError) as err:
        die(err)
    icon_path = install_icon(inst, icon)
    set_launcher_icon(inst.desktop_file, icon_path)
    remove_old_icons(inst, icon_path)
    print(f"Icon:      {icon_path} ({desc})")


def create(inst, label, icon_src, force, chrome_bin, logo):
    """The default command: write the instance's launcher and icon, creating
    its data dir if needed. An existing data dir is reused as-is, logins and
    all. Without force, refuses to overwrite an existing launcher.
    """
    # Check everything, and build the icon, before creating anything, so a bad
    # option or unreadable image leaves no debris.
    if not force:
        if os.path.lexists(inst.desktop_file):
            die(f"{inst.desktop_file} already exists (use --force to overwrite)")
    if not (os.path.isfile(chrome_bin) and os.access(chrome_bin, os.X_OK)):
        die(f"Chrome not found at {chrome_bin} (set CHROME_BIN)")
    if icon_src:
        if not os.path.isfile(icon_src):
            die(f"icon not found: {icon_src}")
    elif not os.path.isfile(logo):
        die(f"Chrome logo not found at {logo} (set CHROME_LOGO or use --icon)")

    fresh = inst.is_fresh()

    # Icon: an explicit file converted to PNG, else the profile's theme color
    # if an existing data dir has one, else the stock logo.
    icon = None
    if icon_src:
        try:
            icon = png_bytes(open_image(icon_src))
        except ValueError as err:
            die(err)
        icon_desc = f"from {icon_src}"
    elif not fresh:
        try:
            icon, icon_desc = theme_icon(inst, logo)
        except (NoThemeColor, ValueError):
            pass  # no theme color yet: fall back to the stock logo
    if icon is None:
        icon, icon_desc = Path(logo).read_bytes(), "stock Chrome logo"

    for d in (inst.profile_dir, inst.desktop_file.parent):
        d.mkdir(parents=True, exist_ok=True)

    # A brand-new data dir gets its profile folder named NAME instead of
    # "Default". Chrome names web app launchers and their Wayland app_id
    # chrome-<app-id>-<profile folder>, so this keeps the same app installed
    # from two launchers from overwriting each other's launcher or sharing a
    # dock icon. Chrome fills in the rest of Local State on first launch.
    if fresh:
        local_state = json.dumps({"profile": {"last_used": inst.name}}, separators=(",", ":"))
        write_file(inst.profile_dir / "Local State", (local_state + "\n").encode())

    icon_path = install_icon(inst, icon)
    write_file(inst.desktop_file, desktop_entry(inst, label, chrome_bin, icon_path).encode())
    remove_old_icons(inst, icon_path)
    update_desktop_database(inst)

    print(f"Launcher:  {inst.desktop_file}")
    print(f"Icon:      {icon_path} ({icon_desc})")
    if fresh:
        print(f'Data dir:  {inst.profile_dir} (new; profile folder "{inst.name}")')
        print("Launch it from your app menu and sign in. To match the icon to a theme")
        print("color, pick one in Customize Chrome > Color, then run:")
        print(f"  {PROG} --recolor {inst.name}")
    else:
        print(f"Data dir:  {inst.profile_dir} (existing; reused as-is)")


def parse_args():
    """Parse and validate the command line. Invalid usage exits with status 2
    and a message, before anything is read or written."""
    parser = argparse.ArgumentParser(
        prog=PROG, usage="%(prog)s [options] NAME\n       %(prog)s --list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Create a Chrome launcher with its own data directory, so it runs as a\n"
                    "separate Chrome with its own dock icon.",
        epilog="environment:\n"
               f"  CHROME_BIN        Chrome executable (default: {DEFAULT_CHROME_BIN})\n"
               "  CHROME_LOGO       Chrome logo, used as the default icon and for --recolor\n"
               f"                    (default: {DEFAULT_CHROME_LOGO})")
    parser.add_argument("name", metavar="NAME", nargs="?",
                        help='short id, e.g. "work" -> chrome-work.desktop; '
                             "data dir: ~/.config/chrome-instances/NAME")
    parser.add_argument("-m", "--menu", metavar="TEXT",
                        help='menu name suffix (default: NAME) -> "Chrome - TEXT"')
    parser.add_argument("-i", "--icon", metavar="FILE",
                        help="use this image as the icon (default: the Chrome logo, or "
                             "with -f, the logo in the instance's theme color if it has one)")
    parser.add_argument("-r", "--recolor", action="store_true",
                        help="only redraw NAME's icon in the theme color picked in that "
                             "Chrome (Customize Chrome > Color)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="overwrite an existing launcher and icon (data dir is kept)")
    parser.add_argument("-d", "--delete", action="store_true",
                        help="remove NAME's launcher, icon and data dir, confirming each")
    parser.add_argument("-l", "--list", action="store_true",
                        help="list the standard Chrome and all instances, with their "
                             "profiles, web apps, and which is the default browser")
    args = parser.parse_args()

    if args.list:
        if args.name or args.menu or args.icon or args.recolor or args.force or args.delete:
            parser.error("--list takes no NAME or other options")
        return args
    if args.name is None:
        parser.error("the following arguments are required: NAME")
    if not NAME_PATTERN.fullmatch(args.name):
        parser.error("NAME may only contain letters, digits, - and _")
    if args.delete and (args.menu or args.icon or args.recolor or args.force):
        parser.error("--delete can't be combined with other options")
    if args.recolor and (args.menu or args.icon or args.force):
        parser.error("--recolor only changes the icon; use --force to change the menu name or icon file")
    # A newline in the menu name would start a new line (key) in the launcher.
    if args.menu is not None and (not args.menu or any(ord(c) < 32 or ord(c) == 127 for c in args.menu)):
        parser.error("--menu must be non-empty and can't contain control characters")
    return args


def run():
    """Parse the command line and run the chosen command."""
    args = parse_args()
    if args.list:
        list_browsers()
        return
    inst = Instance(args.name)
    chrome_bin = os.environ.get("CHROME_BIN") or DEFAULT_CHROME_BIN
    logo = os.environ.get("CHROME_LOGO") or DEFAULT_CHROME_LOGO

    if args.delete:
        delete(inst)
    elif args.recolor:
        recolor(inst, logo)
    else:
        create(inst, args.menu or args.name, args.icon, args.force, chrome_bin, logo)


def main():
    """Entry point of the chrome-instance command (see pyproject.toml)."""
    try:
        run()
    except KeyboardInterrupt:  # Ctrl-C, e.g. at a --delete prompt
        print(file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
