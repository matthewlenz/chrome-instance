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
    ~/.local/share/icons/chrome-NAME.png              the launcher's icon

(following $XDG_CONFIG_HOME, $XDG_CACHE_HOME and $XDG_DATA_HOME when set).

The icon can be redrawn in the theme color picked inside that Chrome
(--recolor): the logo's red, green and yellow segments are repainted in the
same Material palette Chrome derives from that color for its own window frame.

Commands, chosen by options (see parse_args):

    create    (default)  write the launcher and icon; create the data dir
    --recolor            rewrite only the icon, from the instance's theme color
    --delete             remove the launcher, icon, data dir and cache, asking
                         before each

See README.md for usage and background.
"""

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

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


class Instance:
    """The files and names belonging to one Chrome instance, NAME.

    Only computes paths; nothing is read or created until a command runs.
    """

    def __init__(self, name):
        self.name = name
        data_home = xdg_dir("XDG_DATA_HOME", ".local/share")
        # The data dir lives under ~/.config like Chrome's own: only then does
        # Chrome put its disk cache under ~/.cache (same relative path) instead
        # of inside it, where backups would include it.
        self.profile_dir = xdg_dir("XDG_CONFIG_HOME", ".config") / "chrome-instances" / name
        self.cache_dir = xdg_dir("XDG_CACHE_HOME", ".cache") / "chrome-instances" / name
        self.desktop_file = data_home / "applications" / f"chrome-{name}.desktop"
        self.icon_file = data_home / "icons" / f"chrome-{name}.png"
        # Chrome's --class sets its windows' WM class (and Wayland app_id).
        # The launcher's StartupWMClass must match it for the dock to show
        # this launcher's icon on those windows.
        self.wm_class = f"chrome-{name}"

    def is_fresh(self):
        """True if the data dir doesn't exist yet or is empty, i.e. Chrome
        has never run with it."""
        return not self.profile_dir.is_dir() or not any(self.profile_dir.iterdir())

    def chrome_running(self):
        """True if a Chrome process is currently using the data dir.

        Chrome keeps a SingletonLock symlink in its data dir pointing at
        "<hostname>-<pid>". Checking that the pid is alive (signal 0 only
        tests, it sends nothing) skips a stale lock left behind by a crash.
        This works however that Chrome was started.
        """
        try:
            pid = int(os.readlink(self.profile_dir / "SingletonLock").rsplit("-", 1)[1])
            if pid <= 0:  # os.kill would address a process group instead
                return False
            os.kill(pid, 0)
        except PermissionError:
            return True  # the process exists but belongs to someone else
        except (OSError, ValueError, IndexError):
            return False  # no lock, a malformed one, or a dead process
        return True


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


def refresh_icon_cache(inst):
    """Make GNOME Shell reload the instance's icon after it was rewritten.

    GNOME Shell caches icons by path and doesn't watch the icon files, so an
    icon rewritten in place would stay stale until logout. It does rescan icon
    directories whose modification time changed, which also drops the cached
    icons, so bump the directory's.
    """
    os.utime(inst.icon_file.parent)


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


def read_theme(data_dir):
    """Find the theme color of the profile Chrome last used in data_dir.

    Returns (seed, profile): seed is the theme color as a 0xRRGGBB int, or
    None for a grayscale theme; profile is the profile folder's name. Raises
    NoThemeColor when the profile has no usable color.

    Chrome records the last used profile in "Local State" and each profile's
    theme in its "Preferences" file (both JSON):

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
    state = load_json(data_dir / "Local State") or {}
    profile = state.get("profile", {}).get("last_used") or "Default"
    prefs = load_json(data_dir / profile / "Preferences")
    if prefs is None:
        raise NoThemeColor(f"no Chrome profile in {data_dir} yet; launch it and pick a color first")
    theme = prefs.get("browser", {}).get("theme", {})
    ext_theme = prefs.get("extensions", {}).get("theme", {})
    if ext_theme.get("system_theme", 0):
        raise NoThemeColor(f'profile "{profile}" uses the GTK/Qt system theme, which has no theme color')
    theme_id = ext_theme.get("id", "")
    if theme_id == "autogenerated_theme_id":
        seed = prefs.get("autogenerated", {}).get("theme", {}).get("color")
    elif theme_id in ("", "user_color_theme_id"):
        seed = theme.get("user_color")
    else:
        raise NoThemeColor(f'profile "{profile}" uses a theme extension, which has no theme color')
    if theme.get("is_grayscale", False):
        return None, profile
    if seed is None:
        raise NoThemeColor(f'profile "{profile}" has no theme color; pick one in Customize Chrome > Color')
    if theme.get("color_variant", 1) not in (0, 1):
        # Other styles derive their palettes differently; SEGMENT_TONES
        # follows the default one.
        print("note: only Chrome's default color style is reproduced exactly", file=sys.stderr)
    return seed & 0xFFFFFF, profile  # drop the alpha byte (and the sign)


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


def desktop_entry(inst, label, chrome_bin):
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
Icon={entry_string(str(inst.icon_file))}
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


# --- Commands ---------------------------------------------------------------------

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
    for target in (inst.desktop_file, inst.icon_file, inst.profile_dir, inst.cache_dir):
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
    color. The launcher, label and data dir are left alone."""
    if not os.path.lexists(inst.desktop_file):
        die(f"no launcher for '{inst.name}' (create it first)")
    if not os.path.isfile(logo):
        die(f"Chrome logo not found at {logo} (set CHROME_LOGO)")
    try:
        icon, desc = theme_icon(inst, logo)
    except (NoThemeColor, ValueError) as err:
        die(err)
    inst.icon_file.parent.mkdir(parents=True, exist_ok=True)
    write_file(inst.icon_file, icon)
    refresh_icon_cache(inst)
    print(f"Icon:      {inst.icon_file} ({desc})")
    print("The dock picks it up within a few seconds (or when you open the overview).")


def create(inst, label, icon_src, force, chrome_bin, logo):
    """The default command: write the instance's launcher and icon, creating
    its data dir if needed. An existing data dir is reused as-is, logins and
    all. Without force, refuses to overwrite an existing launcher or icon.
    """
    # Check everything, and build the icon, before creating anything, so a bad
    # option or unreadable image leaves no debris.
    if not force:
        for f in (inst.desktop_file, inst.icon_file):
            if os.path.lexists(f):
                die(f"{f} already exists (use --force to overwrite)")
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

    for d in (inst.profile_dir, inst.desktop_file.parent, inst.icon_file.parent):
        d.mkdir(parents=True, exist_ok=True)

    # A brand-new data dir gets its profile folder named NAME instead of
    # "Default". Chrome names web app launchers and their Wayland app_id
    # chrome-<app-id>-<profile folder>, so this keeps the same app installed
    # from two launchers from overwriting each other's launcher or sharing a
    # dock icon. Chrome fills in the rest of Local State on first launch.
    if fresh:
        local_state = json.dumps({"profile": {"last_used": inst.name}}, separators=(",", ":"))
        write_file(inst.profile_dir / "Local State", (local_state + "\n").encode())

    write_file(inst.icon_file, icon)
    write_file(inst.desktop_file, desktop_entry(inst, label, chrome_bin).encode())
    refresh_icon_cache(inst)
    update_desktop_database(inst)

    print(f"Launcher:  {inst.desktop_file}")
    print(f"Icon:      {inst.icon_file} ({icon_desc})")
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
        prog=PROG, usage="%(prog)s [options] NAME",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Create a Chrome launcher with its own data directory, so it runs as a\n"
                    "separate Chrome with its own dock icon.",
        epilog="environment:\n"
               f"  CHROME_BIN        Chrome executable (default: {DEFAULT_CHROME_BIN})\n"
               f"  CHROME_LOGO       logo to recolor (default: {DEFAULT_CHROME_LOGO})")
    parser.add_argument("name", metavar="NAME",
                        help='short id, e.g. "work" -> chrome-work.desktop; '
                             "data dir: ~/.config/chrome-instances/NAME")
    parser.add_argument("-l", "--label", metavar="TEXT",
                        help='menu label suffix (default: NAME) -> "Chrome - TEXT"')
    parser.add_argument("-i", "--icon", metavar="FILE",
                        help="use this image as the icon instead of the Chrome logo")
    parser.add_argument("-r", "--recolor", action="store_true",
                        help="only redraw NAME's icon in the theme color picked in that "
                             "Chrome (Customize Chrome > Color)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="overwrite an existing launcher/icon (data dir is kept)")
    parser.add_argument("-d", "--delete", action="store_true",
                        help="remove NAME's launcher, icon and data dir, confirming each")
    args = parser.parse_args()

    # NAME ends up in file names, the window class and the profile folder
    # name, so keep it to characters that are safe in all of them.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.name):
        parser.error("NAME may only contain letters, digits, - and _")
    if args.delete and (args.label or args.icon or args.recolor or args.force):
        parser.error("--delete can't be combined with other options")
    if args.recolor and (args.label or args.icon or args.force):
        parser.error("--recolor only changes the icon; use --force to change the label or icon file")
    # A newline in the label would start a new line (key) in the launcher.
    if args.label is not None and (not args.label or any(ord(c) < 32 or ord(c) == 127 for c in args.label)):
        parser.error("--label must be non-empty and can't contain control characters")
    return args


def run():
    """Parse the command line and run the chosen command."""
    args = parse_args()
    inst = Instance(args.name)
    chrome_bin = os.environ.get("CHROME_BIN") or DEFAULT_CHROME_BIN
    logo = os.environ.get("CHROME_LOGO") or DEFAULT_CHROME_LOGO

    if args.delete:
        delete(inst)
    elif args.recolor:
        recolor(inst, logo)
    else:
        create(inst, args.label or args.name, args.icon, args.force, chrome_bin, logo)


def main():
    """Entry point of the chrome-instance command (see pyproject.toml)."""
    try:
        run()
    except KeyboardInterrupt:  # Ctrl-C, e.g. at a --delete prompt
        print(file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
