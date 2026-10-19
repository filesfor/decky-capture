"""The feed: ffmpeg captures the screen, this splits the JPEGs, V4L2 sends them.

Three stages joined by two pipes, each running where it is happiest:

    ffmpeg (capture + encode)  ->  stdout  ->  this  ->  /dev/videoN

ffmpeg does the pixel work in C with a SIMD JPEG encoder, which matters: doing
this in Python on a Deck's CPU would not hold 30 fps at 720p. Python is left with
framing and copying, which is cheap, and the whole thing runs on one thread so
backpressure is obvious rather than mysterious.
"""

import asyncio
import os
import subprocess

from . import capture


class FrameSink:
    """Writes encoded frames into the gadget's V4L2 output device."""

    def __init__(self, device: str, card: int, method: str, width: int, height: int,
                 fps: int, quality: int = 6, log=None):
        self.device = device
        self.width = width
        self.height = height
        self.fps = fps
        self.method = method
        self.card = card
        self.quality = quality
        self.sent = 0
        self.dropped = 0
        self.error: str | None = None
        self._log = log or (lambda m: None)
        self._process: subprocess.Popen | None = None
        self._task = None
        self._output = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def command(self) -> list:
        return capture.ffmpeg_command(self.method, self.card, self.width, self.height,
                                      self.fps, quality=self.quality)

    def start(self) -> None:
        """Run ffmpeg and the feed loop on a background task."""
        self._process = subprocess.Popen(
            self.command(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )
        # Non-blocking so the reader cannot sit on a frame while the writer stalls.
        os.set_blocking(self._process.stdout.fileno(), False)

        from .v4l2out import V4L2Writer
        self._output = V4L2Writer(self.device, self.width, self.height)

        self._task = asyncio.get_event_loop().create_task(self._run())

    async def _run(self) -> None:
        splitter = capture.MjpegSplitter()
        loop = asyncio.get_event_loop()
        try:
            self._output.open()
        except OSError as e:
            self.error = f"Could not open {self.device}: {e}"
            self._log(self.error)
            return

        try:
            while self.running:
                try:
                    chunk = await loop.run_in_executor(None, self._read_chunk)
                except Exception as e:  # noqa: BLE001 - the pipe can fail in many ways
                    self.error = f"capture stopped: {e}"
                    self._log(self.error)
                    return
                if not chunk:
                    break
                for frame in splitter.feed(chunk):
                    self._write(frame)
        finally:
            self._stop_process()

    def _read_chunk(self, size: int = 65536) -> bytes:
        return self._process.stdout.read(size) or b""

    def _write(self, frame: bytes) -> None:
        """One frame into the gadget. A short write is dropped, not retried.

        Retrying would mean queueing, and a queued frame is stale by the time it
        goes out. The host will drop it anyway; better to lose it now and stay in
        step with what is on the screen.
        """
        try:
            self._output.write(frame)
            self.sent += 1
        except OSError as e:
            self.dropped += 1
            if self.dropped % 30 == 1:
                self._log(f"write failed ({e}); dropped {self.dropped}")

    def _stop_process(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except Exception:  # noqa: BLE001
                self._process.kill()
        if self._process.stderr is not None:
            try:
                message = self._process.stderr.read().decode("utf-8", "replace").strip()
                if message:
                    self._log("ffmpeg: " + message[-2000:])
            except OSError:
                pass

    async def stop(self) -> None:
        self._stop_process()
        if self._output is not None:
            try:
                self._output.close()
            except OSError:
                pass
            self._output = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
