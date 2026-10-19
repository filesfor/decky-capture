"""Tests for the resolution ladder and the link-speed report.

The ladder exists so the host negotiates rather than the plugin guessing, which
only works if the whole thing is actually advertised. And MJPEG-only is a safety
decision, not a simplification, so it is pinned too.
"""

import sys
import unittest

sys.path.insert(0, "py_modules")

from decky_capture import gadget


class LadderTest(unittest.TestCase):
    def test_the_full_ladder_is_advertised_not_a_handful(self):
        # Three hand-picked modes make the host's negotiation a guess made here.
        # A ladder hands the decision back to the host, which is what real capture
        # hardware does.
        modes = gadget.modes_from_resolutions()
        self.assertGreaterEqual(len(modes), 8)
        self.assertIn((1920, 1080, 30), modes)
        self.assertIn((640, 480, 30), modes)

    def test_the_ladder_includes_the_panel_native_size(self):
        self.assertIn((1280, 800, 30), gadget.modes_from_resolutions())

    def test_the_frame_rate_is_applied_to_every_resolution(self):
        modes = gadget.modes_from_resolutions(fps=60)
        self.assertTrue(all(fps == 60 for _, _, fps in modes))

    def test_the_ladder_is_in_descending_order_so_the_host_prefers_the_best(self):
        # configfs enumerates in creation order and hosts tend to take the first.
        widths = [w for w, _h, _f in gadget.modes_from_resolutions()]
        self.assertEqual(widths, sorted(widths, reverse=True))


class MjpegOnlyTest(unittest.TestCase):
    def test_yuyv_is_not_advertised(self):
        # If the host picked YUYV the kernel would accept it and we would feed it
        # MJPEG, which is not a worse picture but noise.
        groups = [name for _group, name, _mjpeg in gadget.FORMAT_GROUPS]
        self.assertEqual(groups, ["mjpeg"])
        self.assertNotIn("uncompressed", [g for g, _n, _m in gadget.FORMAT_GROUPS])

    def test_the_tree_has_no_yuyv_directory(self):
        paths = [p for p, _ in gadget.format_tree(gadget.modes_from_resolutions())]
        self.assertFalse([p for p in paths if "yuyv" in p])

    def test_every_advertised_size_gets_a_control_link(self):
        # One link per frame size, no duplicates, or the control interface ends up
        # with a name collision.
        names = [p for p, v in gadget.format_tree(gadget.modes_from_resolutions())
                 if v.startswith("link:")]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(names), len(gadget.RESOLUTIONS))

    def test_mjpeg_still_gets_pad_and_maxpacket(self):
        entries = dict(gadget.format_tree([(1280, 800, 30)]))
        self.assertIn("streaming/mjpeg/mjpeg/800p/pad", entries)
        self.assertIn("streaming/mjpeg/mjpeg/800p/maxpacket", entries)


class LinkSpeedTest(unittest.TestCase):
    def test_no_udc_gives_unknown_rather_than_raising(self):
        self.assertIsNone(gadget.link_speed("/dev/nonexistent"))
        self.assertEqual(gadget.link_description("/dev/nonexistent"), "unknown")

    def test_the_named_speeds_are_the_kernel_wording(self):
        self.assertEqual(gadget.SPEED_SUPER, 5000)
        self.assertEqual(gadget.SPEED_HIGH, 1920)
        self.assertEqual(gadget.SPEED_FULL, 480)

    def test_super_speed_is_what_would_unlock_yuyv(self):
        # Context for the comment in the module: this is the number to look for
        # before believing YUYV 1080p is possible.
        yuyv_1080p_mibps = 1920 * 1080 * 2 * 30 / (1024 * 1024)
        self.assertGreater(yuyv_1080p_mibps, 100)
        self.assertLess(yuyv_1080p_mibps, gadget.SPEED_SUPER / 8 * 1.4)


if __name__ == "__main__":
    unittest.main()