"""Capturing this Deck's own screen and encoding it as MJPEG.

The screen capture is the hard part and the part most likely not to work. Three
methods, in order of preference, each with a different reason to be first:

1. **DRM kmsreadout.** Reads back what is actually being scanned out, so it gets
   whatever is really on the panel. Needs no writeback connector, which matters
   because amdgpu on the Deck does not expose one. It does need the DRM master
   for the CRTC, which gamescope holds in Game Mode -- so in Game Mode this is
   expected to fail rather than to return the wrong thing.
2. **X11 capture.** Works in desktop mode, sees only what X sees, and is blind to
   anything composited above it.
3. **Framebuffer.** Last resort. Often the wrong size and often stale under a
   compositor.

Encoding is ffmpeg rather than Pillow, because Pillow is not on a stock SteamOS
and shipping a wheel for this is more moving parts than a program the Deck very
probably already has. ffmpeg writes MJPEG to a pipe and we frame it up for V4L2.

No method here has ever been run.
"""

import shutil

SOI = b"\xff\xd8"  # start of image
EOI = b"\xff\xd9"  # end of image


class CaptureMethod:
    def __init__(self, name: str, description: str, works_in_game_mode: bool = False):
        self.name = name
        self.description = description
        self.works_in_game_mode = works_in_game_mode


METHODS = [
    CaptureMethod("kmsreadout", "DRM scanout readback; needs gamescope to let go of DRM", False),
    CaptureMethod("x11", "desktop mode window capture", False),
    CaptureMethod("framebuffer", "raw framebuffer; may be the wrong size", False),
]


def available_methods(ffmpeg: str | None = None) -> list:
    """Which methods could actually run here, best first."""
    have_ffmpeg = bool(ffmpeg or shutil.which("ffmpeg"))
    found = []
    if not have_ffmpeg:
        return found
    for method in METHODS:
        if method.name in ("kmsreadout", "x11", "framebuffer"):
            found.append(method)
    return found


def ffmpeg_command(method: str, card: int, width: int, height: int, fps: int,
                   display: str = ":0", quality: int = 6) -> list:
    """The ffmpeg invocation that captures the screen and emits MJPEG on stdout.

    ``-q:v`` is ffmpeg's MJPEG quality, 2 best to 31 worst. 6 is roughly the point
    where a screen full of text stops looking blotchy, and it matters here because
    every byte is going over USB.
    """
    if method == "kmsreadout":
        source = ["-f", "kmsgrab", "-i", f"/dev/dri/card{card}"]
    elif method == "x11":
        source = ["-f", "x11grab", "-video_size", f"{width}x{height}",
                  "-framerate", str(fps), "-i", f"{display}+0,0"]
    elif method == "framebuffer":
        # Read the framebuffer straight through, scaled to what the gadget
        # advertises since the two sizes rarely match.
        source = ["-f", "rawvid", "-pixel_format", "bgr0",
                  "-video_size", "1280x800", "-framerate", str(fps), "-i", "/dev/fb0"]
    else:
        raise ValueError(f"unknown capture method {method!r}")

    return ["ffmpeg", "-hide_banner", "-loglevel", "error",
            *source,
            "-vf", f"scale={width}:{height}",
            "-pix_fmt", "yuvj420p",
            "-q:v", str(quality),
            "-f", "mjpeg",
            "-"]


def choose_method(preferred: str | None = None, card: int | None = None,
                  in_game_mode: bool = False) -> str:
    """Pick a capture method, or explain why there is not one.

    Returning the reason matters: in Game Mode every method here is expected to
    fail, and saying "kmsreadout needs gamescope to let go of DRM" is the
    difference between a user who understands the problem and one who thinks the
    plugin is broken.
    """
    if preferred:
        return preferred
    if in_game_mode:
        # kmsreadout needs the DRM master, and gamescope owns it in Game Mode.
        # Rather than offer something that will fail, say so.
        raise RuntimeError(
            "Screen capture does not work in Game Mode: gamescope owns the DRM "
            "master, so there is no way to read the panel back. Switch to desktop "
            "mode, or run the game windowed.")
    return "kmsreadout" if card is not None else "framebuffer"


class MjpegSplitter:
    """Pull whole JPEG frames out of a stream.

    ffmpeg writes a bare concatenation of JPEGs with nothing between them, so the
    only way to find the boundaries is the markers: a frame starts at SOI and ends
    at the next EOI that is not inside the entropy-coded data.

    That last part is the subtlety. A byte sequence of ff d9 does appear inside
    compressed data -- it is only escaped when the encoder emits it as ff 00 -- so
    a naive split on EOI truncates real frames. Proper handling needs walking the
    entropy-coded segments and skipping their stuffed bytes and restart markers.
    """

    def __init__(self):
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list:
        """Return every complete frame now buffered, keeping any partial tail."""
        self._buffer += data
        frames = []
        while True:
            start = self._buffer.find(SOI)
            if start < 0:
                # No start marker: keep the last byte, it may be half of one.
                if len(self._buffer) > 1:
                    del self._buffer[:-1]
                return frames
            if start:
                del self._buffer[:start]
            end = self._end_of_frame()
            if end is None:
                return frames
            frames.append(bytes(self._buffer[:end]))
            del self._buffer[:end]

    def _end_of_frame(self) -> int | None:
        """Index just past the frame's EOI, skipping anything inside the data."""
        i = 2  # past the SOI we already know about
        n = len(self._buffer)
        while i + 1 < n:
            if self._buffer[i] != 0xFF:
                i += 1
                continue
            marker = self._buffer[i + 1]
            if marker == 0x00:
                i += 2          # stuffed byte inside entropy data
                continue
            if marker == 0xFF:
                i += 1          # fill byte
                continue
            if 0xD0 <= marker <= 0xD7:
                i += 2          # restart marker
                continue
            if marker == 0xD9:   # EOI: the end of the image
                return i + 2
            # Any other marker is a real segment header, which is followed by a
            # two byte length that has to be stepped over or its own bytes get
            # read as markers.
            if i + 4 <= n:
                length = (self._buffer[i + 2] << 8) | self._buffer[i + 3]
                i += 2 + length
                continue
            return None           # segment header is not complete yet
        return None


def guess_size(payload: bytes) -> tuple | None:
    """Read the dimensions out of a JPEG's SOF marker."""
    i = 2
    n = len(payload)
    while i + 9 < n:
        if payload[i] != 0xFF:
            i += 1
            continue
        marker = payload[i + 1]
        # SOF0..SOF15, skipping the non-frame ones (DHT=C4, JPG=C8, DAC=CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = (payload[i + 5] << 8) | payload[i + 6]
            width = (payload[i + 7] << 8) | payload[i + 8]
            return width, height
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = (payload[i + 2] << 8) | payload[i + 3]
        i += 2 + length
    return None