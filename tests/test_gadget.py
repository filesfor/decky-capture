"""Gadget layout and bandwidth tests.

The configfs tree cannot be created on anything but a Linux gadget stack, so the
layout is expressed as data by ``format_tree`` and checked here. That is the point
of building it that way: the shape is the part most likely to be subtly wrong, and
this way it is at least pinned rather than merely believed.
"""

import sys
import unittest

sys.path.insert(0, "py_modules")

from decky_capture import gadget

MODES = [(1280, 720, 30), (640, 480, 30)]


class TreeTest(unittest.TestCase):
    def entries(self):
        return dict(gadget.format_tree(MODES))

    def test_each_mode_is_advertised_under_mjpeg(self):
        # MJPEG only, deliberately: we cannot honour a YUYV selection, so
        # advertising one would be a trap rather than an option.
        paths = [p for p, _ in gadget.format_tree(MODES)]
        self.assertIn("streaming/mjpeg/mjpeg/720p/width", paths)
        self.assertIn("streaming/mjpeg/mjpeg/480p/height", paths)
        self.assertFalse([p for p in paths if "uncompressed" in p])

    def test_width_and_height_are_written_before_they_are_read(self):
        # Directories have to exist before the files inside them. The order
        # matters and nothing enforces it except this list.
        order = [p for p, _ in gadget.format_tree(MODES)]
        for size_dir in ("streaming/mjpeg/mjpeg/720p", "streaming/mjpeg/mjpeg/480p"):
            self.assertLess(order.index(size_dir), order.index(f"{size_dir}/width"))

    def test_mjpeg_gets_pad_and_maxpacket_but_yuyv_does_not(self):
        entries = self.entries()
        self.assertIn("streaming/mjpeg/mjpeg/720p/pad", entries)
        self.assertIn("streaming/mjpeg/mjpeg/720p/maxpacket", entries)
        # f_uvc does not take these for a raw format, and writing them is a
        # no-op at best.
        self.assertNotIn("streaming/uncompressed/yuyv/720p/pad", entries)

    def test_every_frame_size_is_linked_into_the_control_interface(self):
        # Without this the host enumerates a camera whose control requests fail,
        # which it reads as a camera it cannot use.
        links = {p: v[5:] for p, v in self.entries().items() if v.startswith("link:")}
        self.assertEqual(links["streaming/control/usecs-720p-link"],
                         "streaming/mjpeg/mjpeg/720p/control")
        self.assertEqual(links["streaming/control/usecs-480p-link"],
                         "streaming/mjpeg/mjpeg/480p/control")

    def test_a_control_link_name_is_never_written_twice(self):
        # Both format groups would otherwise claim usecs-720p-link and the second
        # would silently collide with the first.
        names = [p for p, v in gadget.format_tree(MODES) if v.startswith("link:")]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), len(MODES), "one control link per frame size")

    def test_no_modes_produces_no_tree(self):
        self.assertEqual(gadget.format_tree([]), [])


class BandwidthTest(unittest.TestCase):
    def test_1080p30_mjpeg_fits_usb2(self):
        self.assertTrue(gadget.fits_usb2(1920, 1080, 30))
        self.assertLess(gadget.bandwidth_mibps(1920, 1080, 30), 35.0)

    def test_1080p60_mjpeg_still_fits(self):
        self.assertTrue(gadget.fits_usb2(1920, 1080, 60))

    def test_cost_rises_with_pixels_and_rate(self):
        small = gadget.bandwidth_mibps(640, 480, 30)
        big = gadget.bandwidth_mibps(1920, 1080, 30)
        self.assertLess(small, big)
        self.assertLess(gadget.bandwidth_mibps(1920, 1080, 30),
                        gadget.bandwidth_mibps(1920, 1080, 60))

    def test_unusable_modes_are_filtered_out_rather_than_clamped(self):
        # Advertising a mode the encoder cannot sustain is the exact failure the
        # kernel documentation warns about: the host asks for it and we cannot
        # refuse.
        candidates = [(640, 480, 30), (7680, 4320, 60)]
        self.assertEqual(gadget.usable_modes(candidates), [(640, 480, 30)])


class IntervalTest(unittest.TestCase):
    def test_known_rates_map_to_nanoseconds(self):
        self.assertEqual(gadget.interval_ns(30), 33333333)
        self.assertEqual(gadget.interval_ns(60), 16666667)

    def test_an_unsupported_rate_declares_nothing(self):
        # Returning None rather than a guess: configfs would take a wrong
        # interval as a promise.
        self.assertIsNone(gadget.interval_ns(24))
        self.assertIsNone(gadget.interval_ns(120))

    def test_rates_are_one_second_over_the_count(self):
        self.assertAlmostEqual(gadget.interval_ns(30) / 1e9, 1 / 30, places=7)
        self.assertAlmostEqual(gadget.interval_ns(60) / 1e9, 1 / 60, places=7)


class DiscoveryTest(unittest.TestCase):
    def test_no_udc_directory_gives_none_rather_than_raising(self):
        self.assertIsNone(gadget.UvcGadget.find_udc("/nonexistent"))

    def test_no_video_nodes_gives_none(self):
        self.assertIsNone(gadget.UvcGadget.find_video_device(["/dev/video9"]))


if __name__ == "__main__":
    unittest.main()