"""Struct sizes and ioctl encoding for the V4L2 output side.

Same reasoning as the capture-side tests: a struct in the wrong shape does not
raise, it changes the ioctl number, and the call then fails with EINVAL on a
device that is actually fine. So the sizes are pinned arithmetically.
"""

import ctypes
import sys
import unittest

sys.path.insert(0, "py_modules")

from decky_capture import v4l2out as v


class LayoutTest(unittest.TestCase):
    def test_v4l2_field_is_12_bytes(self):
        self.assertEqual(ctypes.sizeof(v.V4l2Field), 12)

    def test_v4l2_format_is_56_bytes(self):
        # type + flags = 8, payload = width, height, pixelformat (12) + pix (12)
        # + field, bytesperline, sizeimage, colorspace, priv (20) = 44, then
        # reserved[4]. 8 + 44 + 4 = 56.
        self.assertEqual(ctypes.sizeof(v.V4l2Format), 56)

    def test_v4l2_streamparm_is_20_bytes(self):
        # type (4) + the 8 byte union + capability (4) + reserved[4] (4)
        self.assertEqual(ctypes.sizeof(v.V4l2StreamParm), 20)

    def test_mjpeg_is_the_fourcc_in_little_endian(self):
        self.assertEqual(v.V4L2_PIX_FMT_MJPEG, 0x47504A4D)
        self.assertEqual(bytes([(v.V4L2_PIX_FMT_MJPEG >> s) & 0xFF for s in (0, 8, 16, 24)]),
                         b"MJPG")


class IoctlTest(unittest.TestCase):
    def test_field_positions(self):
        number = v._ioc(v._IOC_READWRITE, "V", 0xAB, 0x1234)
        self.assertEqual(number & 0xFF, 0xAB)
        self.assertEqual((number >> 8) & 0xFF, ord("V"))
        self.assertEqual((number >> 16) & 0x3FFF, 0x1234)
        self.assertEqual((number >> 30) & 0x3, v._IOC_READWRITE)

    def test_s_fmt_is_readwrite_because_the_kernel_answers_with_what_it_accepted(self):
        # This is what the format check in V4L2Writer.open relies on.
        self.assertEqual((v.VIDIOC_S_FMT >> 30) & 0x3, v._IOC_READWRITE)
        self.assertEqual(v.VIDIOC_S_FMT & 0xFF, 4)

    def test_streamon_carries_an_int_so_its_size_is_four(self):
        self.assertEqual((v.VIDIOC_STREAMON >> 16) & 0x3FFF, 4)
        self.assertEqual(v.VIDIOC_STREAMON & 0xFF, 18)

    def test_the_numbers_differ_from_each_other(self):
        numbers = [v.VIDIOC_S_FMT, v.VIDIOC_S_PARM, v.VIDIOC_STREAMON]
        self.assertEqual(len(numbers), len(set(numbers)))


if __name__ == "__main__":
    unittest.main()