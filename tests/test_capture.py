"""Screen capture and MJPEG framing tests.

The framing is the part worth testing properly. Splitting a JPEG stream on the
first ff d9 looks right and is not: that byte pair occurs inside compressed data,
and a naive split truncates real frames into the middle of a scan. The decoder
here has to walk segments and skip stuffed bytes, and these tests pin that with a
frame that deliberately contains the trap.
"""

import io
import sys
import unittest

sys.path.insert(0, "py_modules")

from decky_capture import capture


def make_jpeg(width=64, height=48, quality=90) -> bytes:
    from PIL import Image
    image = Image.new("RGB", (width, height), (40, 120, 200))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


class FfmpegCommandTest(unittest.TestCase):
    def test_every_method_produces_mjpeg_on_stdout(self):
        for method in ("kmsreadout", "x11", "framebuffer"):
            command = capture.ffmpeg_command(method, 0, 1280, 720, 30)
            self.assertEqual(command[0], "ffmpeg")
            self.assertEqual(command[-3:], ["-f", "mjpeg", "-"], method)
            self.assertIn("scale=1280:720", command, method)

    def test_kmsreadout_reads_the_drm_card(self):
        command = capture.ffmpeg_command("kmsreadout", 1, 1280, 720, 30)
        self.assertIn("-f", command)
        self.assertIn("kmsgrab", command)
        self.assertIn("/dev/dri/card1", command)

    def test_x11grab_is_told_where_and_how_big(self):
        command = capture.ffmpeg_command("x11", 0, 1280, 720, 30, display=":1")
        self.assertIn("x11grab", command)
        self.assertIn("-video_size", command)
        self.assertIn(":1+0,0", command)

    def test_an_unknown_method_is_refused_rather_than_defaulted(self):
        with self.assertRaises(ValueError):
            capture.ffmpeg_command("magic", 0, 1280, 720, 30)

    def test_quality_is_clamped_into_the_range_ffmpeg_understands(self):
        # 2 is best, 31 is worst; anything outside is a silent reinterpretation.
        good = capture.ffmpeg_command("kmsreadout", 0, 640, 480, 30, quality=6)
        self.assertEqual(good[good.index("-q:v") + 1], "6")


class ChooseMethodTest(unittest.TestCase):
    def test_game_mode_explains_itself_rather_than_failing_later(self):
        with self.assertRaises(RuntimeError) as caught:
            capture.choose_method(in_game_mode=True)
        self.assertIn("gamescope", str(caught.exception))

    def test_desktop_mode_prefers_drm(self):
        self.assertEqual(capture.choose_method(in_game_mode=False, card=0), "kmsreadout")

    def test_an_explicit_choice_is_respected_even_in_game_mode(self):
        # The user may know better than the heuristic.
        self.assertEqual(capture.choose_method("framebuffer", in_game_mode=True),
                         "framebuffer")

    def test_without_a_card_it_falls_back_to_the_framebuffer(self):
        self.assertEqual(capture.choose_method(in_game_mode=False, card=None),
                         "framebuffer")


class SplitterTest(unittest.TestCase):
    def test_one_frame(self):
        splitter = capture.MjpegSplitter()
        self.assertEqual(len(splitter.feed(make_jpeg())), 1)

    def test_several_frames_in_one_read(self):
        frames = [make_jpeg(64 + i, 48) for i in range(5)]
        splitter = capture.MjpegSplitter()
        self.assertEqual(len(splitter.feed(b"".join(frames))), 5)

    def test_a_frame_arriving_in_pieces_yields_once_and_only_once(self):
        payload = make_jpeg(320, 240)
        splitter = capture.MjpegSplitter()
        results = []
        for i in range(0, len(payload), 37):
            results.extend(splitter.feed(payload[i:i + 37]))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0], payload)

    def test_a_frame_containing_ff_d9_in_its_data_is_not_truncated(self):
        # ff d9 turns up inside entropy coded data and is only escaped when the
        # encoder emits it as ff 00. Splitting on it blindly cuts the frame in
        # half and the host gets a corrupt JPEG.
        payload = make_jpeg(200, 200, quality=95)
        splitter = capture.MjpegSplitter()
        frames = splitter.feed(payload)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], payload, "frame was cut short")

    def test_leading_garbage_is_discarded(self):
        splitter = capture.MjpegSplitter()
        frames = splitter.feed(b"\x00\x01\x02" + make_jpeg())
        self.assertEqual(len(frames), 1)
        self.assertTrue(frames[0].startswith(b"\xff\xd8"))

    def test_a_stream_of_real_jpegs_survives_a_low_quality_split(self):
        # Low quality produces more stuffed bytes, so the walk is under more
        # pressure than at high quality.
        frames = [make_jpeg(160, 120, quality=10) for _ in range(4)]
        splitter = capture.MjpegSplitter()
        got = splitter.feed(b"".join(frames))
        self.assertEqual(got, frames)

    def test_an_incomplete_trailing_frame_is_kept_not_emitted(self):
        payload = make_jpeg() + b"\xff\xd8" + b"\x11" * 40
        splitter = capture.MjpegSplitter()
        frames = splitter.feed(payload)
        self.assertEqual(len(frames), 1, "should emit only the complete frame")

    def test_garbage_that_never_becomes_a_jpeg_does_not_loop(self):
        splitter = capture.MjpegSplitter()
        self.assertEqual(splitter.feed(b"\xff\xd8" + b"\x11" * 40), [])
        self.assertEqual(splitter.feed(b"\x22" * 100), [])


class SizeTest(unittest.TestCase):
    def test_dimensions_are_read_out_of_the_frame(self):
        self.assertEqual(capture.guess_size(make_jpeg(320, 240)), (320, 240))

    def test_a_truncated_frame_gives_none_rather_than_a_wrong_number(self):
        self.assertIsNone(capture.guess_size(make_jpeg()[:40]))

    def test_something_that_is_not_a_jpeg_gives_none(self):
        self.assertIsNone(capture.guess_size(b"not a jpeg"))


if __name__ == "__main__":
    unittest.main()