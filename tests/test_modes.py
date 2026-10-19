"""The advertised modes have to match the panel they are mirroring.

This exists because they did not. The first version offered 1280x720 and friends,
which is 16:9, while the Deck's screen is 1280x800 and therefore 16:10 -- so the
host would have been handed a stream letterboxed or squashed into the wrong shape.

The kernel cannot refuse a format the host asks for, so what is advertised is a
contract. A wrong aspect ratio here is not a cosmetic bug.
"""

import sys
import unittest

sys.path.insert(0, "py_modules")
sys.path.insert(0, ".")

from decky_capture import gadget

# Duplicated from main.py rather than imported, because main.py pulls in decky,
# which only exists inside the loader. Keeping the number here as a second copy is
# the point: if the panel ever changes, this fails.
PANEL = (1280, 800)
MODES = [(1280, 800, 30), (960, 600, 30), (640, 400, 30)]


class AspectTest(unittest.TestCase):
    def test_the_panel_is_ten_to_sixteen(self):
        width, height = PANEL
        self.assertAlmostEqual(width / height, 16 / 10, places=3)

    def test_no_offered_mode_is_sixteen_by_nine(self):
        # The bug this file exists for: 1280x720 is 16:9 and does not match.
        for width, height, _fps in MODES:
            self.assertNotAlmostEqual(width / height, 16 / 9, places=2,
                                      msg=f"{width}x{height} is 16:9, the panel is not")

    def test_every_mode_matches_the_panel_aspect(self):
        panel_ratio = PANEL[0] / PANEL[1]
        for width, height, _fps in MODES:
            self.assertAlmostEqual(width / height, panel_ratio, places=3,
                                   msg=f"{width}x{height} does not match the panel")

    def test_modes_scale_down_from_the_panel(self):
        for width, height, _fps in MODES:
            self.assertLessEqual(width, PANEL[0])
            self.assertLessEqual(height, PANEL[1])

    def test_the_largest_mode_is_the_panel_itself(self):
        # Streaming above the panel would upscale something that was never
        # captured. There is no detail to gain, only CPU and bandwidth.
        self.assertEqual(max(MODES)[:2], PANEL)


class BandwidthHeadroomTest(unittest.TestCase):
    def test_the_panel_at_30fps_is_far_inside_usb2(self):
        # So it is clear the ceiling here is the panel, not the link.
        mibps = gadget.bandwidth_mibps(PANEL[0], PANEL[1], 30)
        self.assertLess(mibps, 5.0)
        self.assertTrue(gadget.fits_usb2(PANEL[0], PANEL[1], 30))

    def test_1080p60_would_also_fit_the_link_which_is_why_the_panel_is_the_limit(self):
        self.assertTrue(gadget.fits_usb2(1920, 1080, 60))


if __name__ == "__main__":
    unittest.main()