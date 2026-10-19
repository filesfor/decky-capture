import asyncio
import os
import sys

import decky

sys.path.insert(0, os.path.dirname(__file__))
from py_modules.decky_capture import capture, gadget, sink  # noqa: E402

# The Deck's panel is 1280x800, which is 16:10 -- not 16:9, and the modes below
# reflect that. The gadget, though, advertises the whole ladder rather than this
# list: the kernel cannot refuse a format the host asks for, so a narrow offer
# makes the host's negotiation a guess made here instead of one it makes itself.
# This list is only what the plugin suggests in its own UI.
PANEL_WIDTH, PANEL_HEIGHT = 1280, 800
DEFAULT_MODES = [(1280, 800, 30), (960, 600, 30), (640, 400, 30)]


class Plugin:
    """Turn the Deck into a UVC camera so a host sees its screen.

    The Deck emulates controllers already; this is the same idea one class up. A
    configfs gadget with a ``uvc.0`` function instead of a ``hid.0`` one makes the
    host enumerate an ordinary webcam, and frames written into the resulting V4L2
    output device come out the other end. Nothing has to be installed on the host,
    and no capture hardware is involved.

    A host with a capture-card app -- Meta's HDMI Link, for one -- will then show
    whatever this streams, over one cable, with nothing bought.
    """

    def __init__(self):
        self._gadget = None
        self._sink = None

    async def _main(self) -> None:
        decky.logger.info("decky-capture loaded")

    async def _unload(self) -> None:
        await self._stop()

    async def _uninstall(self) -> None:
        await self._stop()

    async def _stop(self) -> None:
        # Order matters. Drop the V4L2 writer before the gadget: the /dev/videoN
        # it holds belongs to the gadget, and tearing the gadget down first leaves
        # a writer pointing at a vanished device.
        if self._sink is not None:
            await self._sink.stop()
            self._sink = None
        if self._gadget is not None:
            self._gadget.teardown()
            self._gadget = None

    # --- called from the frontend -------------------------------------------------

    async def capabilities(self) -> dict:
        """What this Deck can actually do, so the UI never offers the impossible."""
        import shutil
        udc = gadget.UvcGadget.find_udc()
        cards = []
        try:
            cards = sorted(n for n in os.listdir("/sys/class/drm")
                           if n.startswith("card") and "-" not in n)
        except OSError:
            pass
        return {
            "ffmpeg": shutil.which("ffmpeg"),
            "udc": udc,
            "link": gadget.link_description(udc),
            "cards": cards,
            "modes": [list(m) for m in gadget.usable_modes(DEFAULT_MODES)],
            "advertised": [list(m) for m in gadget.modes_from_resolutions()],
            "can_capture": bool(shutil.which("ffmpeg")),
        }

    async def start(self, width: int, height: int, fps: int, quality: int = 6,
                    method: str | None = None) -> dict:
        await self._stop()
        try:
            chosen = capture.choose_method(method, card=0, in_game_mode=False)
            mode = (int(width), int(height), int(fps))
            if not gadget.fits_usb2(mode[0], mode[1], mode[2]):
                return {"running": False,
                        "error": f"{mode[0]}x{mode[1]} at {mode[2]} fps will not fit "
                                 "the USB link as MJPEG. Try a smaller mode."}

            before = gadget.UvcGadget.list_video_nodes()
            self._gadget = gadget.UvcGadget()
            # Advertise the full ladder, not just the chosen mode: the host is
            # better placed than we are to decide, and the kernel will not refuse
            # on our behalf if we guess wrong.
            device = self._gadget.create(gadget.modes_from_resolutions(fps=mode[2]),
                                         before=before)
            if not device:
                self._gadget.teardown()
                self._gadget = None
                return {"running": False,
                        "error": "The gadget started but no video device appeared. "
                                 "The kernel may not have the UVC gadget module."}

            self._sink = sink.FrameSink(device, card=0, method=chosen,
                                       width=mode[0], height=mode[1], fps=mode[2],
                                       quality=quality, log=decky.logger.info)
            self._sink.start()
            return {"running": True, "device": device, "method": chosen, "mode": list(mode)}
        except (gadget.GadgetError, capture.RuntimeError, OSError) as e:
            await self._stop()
            return {"running": False, "error": str(e)}

    async def stop(self) -> dict:
        await self._stop()
        return {"running": False}

    async def status(self) -> dict:
        if self._sink is None:
            return {"running": False, "frames": 0}
        return {
            "running": self._sink.running,
            "frames": self._sink.sent,
            "dropped": self._sink.dropped,
            "error": self._sink.error,
        }