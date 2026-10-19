"""Writing MJPEG frames into the gadget's V4L2 output device.

The UVC gadget shows up on this side as a video device with ``V4L2_CAP_VIDEO_OUTPUT``.
That means the direction is inverted from a camera: this is opened for writing,
configured with ``MJPG``, streaming started, and then frames go in with
``write()``. It is the same ioctl vocabulary as reading a capture device, which is
why the layout code in ``v4l2`` can be reused rather than reinvented.

The pixel format must be MJPEG rather than any raw one. The host is reading MJPEG
off the wire, so that is what has to be handed over; and YUYV at 1080p would be
about 186 MB/s, which USB 2.0 cannot carry at any quality.
"""

import ctypes
import os

# _IOC encoding, as videodev2.h builds it on a 64-bit Linux kernel. Checked as a
# rule in the tests rather than against literal hex: a plausible-looking wrong
# constant passes for verification while failing at runtime with EINVAL.
_IOC_WRITE = 1
_IOC_READ = 2
_IOC_READWRITE = 3  # _IOWR: the kernel writes the accepted values back
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = 8
_IOC_SIZESHIFT = 16
_IOC_DIRSHIFT = 30


def _ioc(direction: int, letter: str, number: int, size: int) -> int:
    return ((direction << _IOC_DIRSHIFT)
            | (ord(letter) << _IOC_TYPESHIFT)
            | (size << _IOC_SIZESHIFT)
            | (number << _IOC_NRSHIFT))


class V4l2Field(ctypes.Structure):
    """struct v4l2_field, nested inside v4l2_format's payload.

    Easy to miss and the struct is the wrong size without it, which changes the
    ioctl number and makes the call fail with EINVAL rather than returning junk.
    """
    _fields_ = [
        ("numerator", ctypes.c_uint32),
        ("denominator", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
    ]


class V4l2Format(ctypes.Structure):
    """struct v4l2_format: 4 + 4 + 44 payload + 4 reserved = 56."""
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("pix_width", ctypes.c_uint32),
        ("pix_height", ctypes.c_uint32),
        ("pix_pixelformat", ctypes.c_uint32),
        ("pix_field", V4l2Field),
        ("field", ctypes.c_uint32),
        ("bytesperline", ctypes.c_uint32),
        ("sizeimage", ctypes.c_uint32),
        ("colorspace", ctypes.c_uint32),
        ("priv", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


class V4l2StreamParm(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("captureparm_numerator", ctypes.c_uint32),
        ("captureparm_denominator", ctypes.c_uint32),
        ("capability", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
    ]


V4L2_BUF_TYPE_VIDEO_OUTPUT = 2
V4L2_PIX_FMT_MJPEG = 0x47504A4D  # 'MJPG'

# S_FMT and S_PARM are _IOWR: the kernel writes back what it accepted, which is
# how the format check below knows the gadget disagreed.
VIDIOC_S_FMT = _ioc(_IOC_READWRITE, "V", 4, ctypes.sizeof(V4l2Format))
VIDIOC_S_PARM = _ioc(_IOC_READWRITE, "V", 22, ctypes.sizeof(V4l2StreamParm))
VIDIOC_STREAMON = _ioc(_IOC_READWRITE, "V", 18, 4)


class V4L2Writer:
    """Frames in, UVC out."""

    def __init__(self, device: str, width: int, height: int):
        self.device = device
        self.width = width
        self.height = height
        self._fd: int | None = None

    def open(self) -> None:
        import fcntl

        self._fd = os.open(self.device, os.O_WRONLY | os.O_NONBLOCK)

        fmt = V4l2Format()
        fmt.type = V4L2_BUF_TYPE_VIDEO_OUTPUT
        fmt.pix_width = self.width
        fmt.pix_height = self.height
        fmt.pix_pixelformat = V4L2_PIX_FMT_MJPEG
        # The kernel writes back what it accepted. If it refused the size, the
        # values here change and we are streaming something other than what the
        # gadget advertised, which is the failure the UVC documentation warns
        # about. Check rather than assume.
        fcntl.ioctl(self._fd, VIDIOC_S_FMT, fmt)
        if fmt.pix_width != self.width or fmt.pix_height != self.height:
            raise OSError(f"the gadget wants {fmt.pix_width}x{fmt.pix_height}, "
                          f"not {self.width}x{self.height}")

        parm = V4l2StreamParm()
        parm.type = V4L2_BUF_TYPE_VIDEO_OUTPUT
        parm.captureparm_numerator = 1
        parm.captureparm_denominator = 30
        try:
            fcntl.ioctl(self._fd, VIDIOC_S_PARM, parm)
        except OSError:
            pass  # a fixed-interval gadget will refuse this and that is fine

        on = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_OUTPUT)
        fcntl.ioctl(self._fd, VIDIOC_STREAMON, on)

    def write(self, frame: bytes) -> None:
        if self._fd is None:
            raise OSError("writer is not open")
        written = os.write(self._fd, frame)
        if written != len(frame):
            # A short write means the frame is going out truncated. There is no
            # way to continue it, so let the caller count it as dropped rather
            # than emitting a corrupt JPEG.
            raise OSError(f"short write: {written} of {len(frame)}")

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None