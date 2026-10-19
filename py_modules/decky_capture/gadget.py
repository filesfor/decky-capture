"""Presenting the Deck as a UVC camera, so a host with a capture-card app sees it.

The same trick as emulating a controller, applied to video: a configfs gadget
with a ``uvc.0`` function instead of a ``hid.0`` one. The kernel then creates a
V4L2 device with **output** capability on this side, we write frames into it, and
the host enumerates an ordinary UVC camera without learning where the pixels came
from.

The kernel documentation is unusually blunt about the one thing that matters here:

    In the current implementation there is no way for the gadget to refuse a set
    format that the host instructs it to set, so it is important that this step is
    completed accurately to ensure the host never asks for a format that can't be
    provided.

So the declared formats are not documentation, they are the contract. Advertise
exactly what can be produced and nothing else.

MJPEG only. YUYV at 1080p is around 186 MB/s and does not fit USB 2.0 High
Speed at all; MJPEG at the same size is roughly 4 MB/s and does.

UNVERIFIED. This has never been run against a gadget stack, a UVC host, or
anything. See ``format_tree`` for the layout, which is the part most likely to be
wrong in detail even though the shape is right.
"""

import os

DEFAULT_CONFIGFS = "/sys/kernel/config/usb_gadget"

# configfs spells frame intervals in nanoseconds.
FRAME_INTERVALS_NS = {
    30: 33333333,
    60: 16666667,
}

# The resolution ladder advertised to the host.
#
# Full ladder on purpose. The kernel cannot refuse a format the host asks for, so
# advertising a wide choice hands the negotiation to the host, which is what real
# capture hardware does. Offering three hand-picked modes instead means guessing
# what the host wants, and the guess has no way to be corrected.
#
# The panel is 1280x800, so anything above that is an upscale rather than more
# detail. It is still advertised: a host may want a 16:9 frame with letterboxing
# handled on its side, and refusing to offer it would be the plugin deciding
# something the host is better placed to decide.
RESOLUTIONS = [
    (1920, 1080),
    (1600, 1200),
    (1280, 1024),
    (1280, 800),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (720, 576),
    (640, 480),
]

# MJPEG only, and deliberately so.
#
# YUYV is offered by real capture hardware and is what a lot of hosts prefer,
# because it needs no decompression. Advertising it here would be a trap: the host
# could select it, the kernel would accept it, and then this plugin would feed
# MJPEG frames into a pipe configured for YUYV, which is not a degraded picture
# but noise. Producing YUYV needs an MJPEG decoder, which a stock SteamOS may not
# have, so it is not advertised rather than advertised and then betrayed.
#
# YUYV 1080p is also about 186 MB/s, which only fits on a SuperSpeed link. See
# ``link_speed``.
FORMAT_ONLY_MJPEG = True

# The formats are declared as (group, format name, is_mjpeg):
#   streaming/mjpeg/mjpeg/<size>     for MJPEG
#   streaming/uncompressed/yuyv/<size>  for YUV 4:2:2
FORMAT_GROUPS = (
    (("mjpeg", "mjpeg", True),) if FORMAT_ONLY_MJPEG else
    (("mjpeg", "mjpeg", True), ("uncompressed", "yuyv", False))
)


def modes_from_resolutions(resolutions=None, fps: int = 30):
    """The (w, h, fps) list to advertise, from the resolution ladder."""
    return [(w, h, fps) for w, h in (resolutions or RESOLUTIONS)]


# Speeds as the kernel words them in /sys/class/udc/<udc>/speed.
SPEED_HIGH = 1920     # 480 Mbit/s, USB 2.0
SPEED_FULL = 480      # 12 Mbit/s, USB 1.1
SPEED_SUPER = 5000    # 5 Gbit/s, USB 3.0


def link_speed(udc: str | None = None) -> int | None:
    """What the UDC negotiated, in Mbit/s.

    Worth knowing and worth reporting: MJPEG fits on any of these, but the answer
    decides whether YUYV at 1080p could ever be viable, which is about 186 MB/s
    and needs SuperSpeed. The Deck's USB-C is USB 3.2 Gen 2, so the hardware is
    capable; whether the gadget stack brings the UDC up at SuperSpeed is a
    different question, and this is how you find out.
    """
    import os
    try:
        name = os.path.basename(udc or "")
        for directory in os.listdir("/sys/class/udc"):
            if udc is None or directory == name:
                path = f"/sys/class/udc/{directory}/speed"
                with open(path) as handle:
                    return int(handle.read().strip())
    except (OSError, ValueError):
        return None
    return None


def link_description(udc: str | None = None) -> str:
    speed = link_speed(udc)
    if speed is None:
        return "unknown"
    return {SPEED_SUPER: "SuperSpeed", SPEED_HIGH: "High (USB 2.0)", SPEED_FULL: "Full (USB 1.1)"}.get(
        speed, f"{speed} Mbit/s")


class GadgetError(Exception):
    """The gadget could not be created or bound."""


def _write(path: str, value) -> None:
    with open(path, "w") as handle:
        handle.write(str(value))


def _mkdir(path: str) -> None:
    try:
        os.mkdir(path)
    except FileExistsError:
        pass


def _rmdir(path: str) -> None:
    try:
        os.rmdir(path)
    except OSError:
        pass


def _symlink(source: str, target: str) -> None:
    try:
        os.symlink(source, target)
    except FileExistsError:
        pass


def format_tree(modes: list) -> list:
    """Every path the gadget needs, in the order configfs wants them made.

    Returns a list of ``(path, kind)`` where kind is a directory, a file with a
    value, or a symlink, so the whole thing can be created without touching a
    real configfs and checked in a test.
    """
    steps = []
    for width, height, _fps in modes:
        for index, (group, name, is_mjpeg) in enumerate(FORMAT_GROUPS):
            size_dir = f"streaming/{group}/{name}/{height}p"
            steps.append((size_dir, "dir"))
            steps.append((f"{size_dir}/width", f"file:{width}"))
            steps.append((f"{size_dir}/height", f"file:{height}"))
            if is_mjpeg:
                # f_uvc wants pad and maxpacket for MJPEG, and refuses to stream
                # without them.
                steps.append((f"{size_dir}/pad", "file:0"))
                steps.append((f"{size_dir}/maxpacket", "file:0"))
            if index == 0:
                # The control interface is a per-frame-size symlink fanned into a
                # single directory. Link it from the first format group only: both
                # groups would write the same usecs-<height>p-link name and the
                # second would silently collide with the first.
                steps.append((f"streaming/control/usecs-{height}p-link",
                              f"link:{size_dir}/control"))
    return steps


class UvcGadget:
    """A configfs UVC gadget: create it, then feed the /dev/videoN it produces."""

    def __init__(self, name: str = "deck_capture",
                 configfs: str = DEFAULT_CONFIGFS,
                 device: str = "Steam Deck Screen",
                 udc: str | None = None):
        self.name = name
        self.root = os.path.join(configfs, name)
        self.device = device
        self.udc = udc
        self.video_device: str | None = None
        self.function = os.path.join(self.root, "functions/uvc.0")

    def create(self, modes: list, before: list | None = None) -> str | None:
        """Build and bind the gadget. Returns the /dev/videoN to write frames to."""
        if not modes:
            raise GadgetError("a UVC gadget with no formats will not enumerate")

        self.teardown()
        _mkdir(self.root)
        for path, value in (
            ("idVendor", "0x28de"),
            ("idProduct", "0x1402"),
            ("bcdDevice", "0x0100"),
            ("bcdUSB", "0x0200"),
            # A composite device whose interface class says camera, so the host
            # does not treat it as something to mount.
            ("bDeviceClass", "0xEF"),
            ("bDeviceSubClass", "0x02"),
            ("bDeviceProtocol", "0x01"),
            ("bMaxPower", "250"),
            ("iManufacturer", "Valve"),
            ("iProduct", self.device),
            ("iSerialNumber", "0001"),
        ):
            _write(os.path.join(self.root, path), value)

        _mkdir(self.function)
        for group, _name, _mjpeg in FORMAT_GROUPS:
            _mkdir(os.path.join(self.function, "streaming", group))
        _mkdir(os.path.join(self.function, "streaming/control"))

        for path, kind in format_tree(modes):
            full = os.path.join(self.function, path)
            if kind == "dir":
                _mkdir(full)
            elif kind.startswith("link:"):
                _symlink(kind[5:], full)
            else:
                _mkdir(os.path.dirname(full))
                _write(full, kind[5:])

        configuration = os.path.join(self.root, "configs/c.1")
        _mkdir(configuration)
        _mkdir(os.path.join(configuration, "strings/0x409"))
        _write(os.path.join(configuration, "strings/0x409/configuration"), self.device)
        _mkdir(os.path.join(configuration, "strings/0x410"))
        _write(os.path.join(configuration, "strings/0x410/configuration"), self.device)
        _symlink(self.function, os.path.join(configuration, "uvc.0"))

        # Binding last: the UDC cannot be claimed until there is something to
        # claim it with.
        udc = self.udc or self.find_udc()
        if not udc:
            self.teardown()
            raise GadgetError("no UDC found; is dwc3 available as a USB device controller?")
        _write(os.path.join(self.root, "UDC"), udc)

        self.video_device = self.find_video_device(before)
        return self.video_device

    @staticmethod
    def find_udc(configfs_udc: str = "/sys/class/udc") -> str | None:
        try:
            names = sorted(os.listdir(configfs_udc))
        except OSError:
            return None
        return f"/dev/{names[0]}" if names else None

    @staticmethod
    def list_video_nodes() -> list:
        try:
            return sorted(f"/dev/{n}" for n in os.listdir("/dev") if n.startswith("video"))
        except OSError:
            return []

    @classmethod
    def find_video_device(cls, before: list | None = None) -> str | None:
        """The new /dev/videoN the kernel made for this gadget.

        By difference, because a Deck already has video devices and there is no
        other handle on ours.
        """
        before = before if before is not None else cls.list_video_nodes()
        after = cls.list_video_nodes()
        new = [n for n in after if n not in before]
        return new[0] if new else None

    def teardown(self) -> None:
        """Remove it completely. Leaving it behind holds the UDC busy."""
        if not os.path.isdir(self.root):
            return
        try:
            _write(os.path.join(self.root, "UDC"), "")
        except OSError:
            pass
        paths = [self.root]
        for base, directories, _files in os.walk(self.root, topdown=False):
            paths.extend(os.path.join(base, d) for d in directories)
        for path in paths:
            _rmdir(path)
        _rmdir(self.root)


def bandwidth_mibps(width: int, height: int, fps: float, quality: float = 0.5) -> float:
    """Rough MJPEG bitrate, used only to refuse modes that obviously will not fit."""
    return width * height * 0.12 * quality * fps / (1024 * 1024)


def fits_usb2(width: int, height: int, fps: float, quality: float = 0.5) -> bool:
    """USB 2.0 High Speed in practice. 480 on paper is not 480 on a cable."""
    return bandwidth_mibps(width, height, fps, quality) < 35.0


def usable_modes(candidates: list, quality: float = 0.5) -> list:
    """Filter a list of ``(w, h, fps)`` down to what the link can carry.

    Filtering rather than clamping, because advertising a mode the encoder cannot
    sustain is exactly the failure the kernel warns about.
    """
    return [m for m in candidates if fits_usb2(m[0], m[1], m[2], quality)]


def interval_ns(fps: int) -> int | None:
    """configfs frame interval for a rate, or None if we cannot declare it."""
    return FRAME_INTERVALS_NS.get(int(fps))