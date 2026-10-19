# Deck as Capture Card

Turn a Steam Deck into a USB camera, so another machine sees the Deck's screen
over one cable. Nothing to buy, and nothing to install on the other machine.

**This is experimental and has never been run.** Read "Honest status" below before
expecting anything from it.

## Why this is possible

The plugin already teaches the Deck to be a game controller on a PC by presenting
a USB gadget. This is the same idea one class up: a configfs gadget with a
`uvc.0` function instead of a `hid.0` one. Linux creates a V4L2 device with
**output** capability on this side, frames written into it appear on the host as
an ordinary UVC webcam, and the host never learns where the pixels came from.

```
  Deck screen ──DRM──▶ ffmpeg ──MJPEG──▶ /dev/videoN ──USB 2.0──▶ host
                                                                   │
                                                     appears as a camera
```

A host with a capture-card app — Meta Quest's HDMI Link, for one — will then show
whatever this streams. That app is normally useless without a $15 capture card,
because a capture card is the only thing it enumerates. This supplies one in
software, over the cable you already have.

## Why the modes stop at 1280x800

The Deck's panel is **1280x800, which is 16:10 — not 16:9**. The offered modes are
that aspect ratio and fractions of it: 1280x800, 960x600, 640x400.

It is worth being precise about the ceiling, because it is not the one people
assume. **Bandwidth is not the limit.** 1080p60 works out at about 7 MiB/s, well
inside USB 2.0 High Speed's practical ~35 MiB/s. The limit is that the thing
being streamed is a 1280x800 panel: going to 1080p would upscale it and invent
detail that was never captured, costing CPU and bandwidth for nothing.

The first version of this offered 1280x720, which is 16:9, and would have handed
the host a letterboxed or squashed version of the screen. `tests/test_modes.py`
exists to stop that coming back.

## Why MJPEG

YUYV at 1080p is about 186 MB/s, which USB 2.0 High Speed cannot carry at any
quality. MJPEG at the same size is roughly 4 MB/s, so 1080p30 fits with room to
spare. That is the whole reason this can work on a USB 2.0 gadget at all.

## Honest status

**Verified:** 43 unit tests pass. They cover the configfs layout as data, the
bandwidth budget, the ffmpeg command construction, the V4L2 struct sizes and ioctl
encoding, and MJPEG frame splitting.

**Not verified:** that any of this works. No gadget has been created, no frame
has been captured, no host has enumerated anything.

Three specific unknowns, in the order you will hit them:

1. **Screen capture in Game Mode will not work.** `kmsreadout` needs the DRM
   master, and gamescope owns it in Game Mode. Exit to desktop mode first. The
   plugin says this rather than offering something that will fail quietly.
2. **Does your Quest accept a bare UVC webcam?** Meta's documentation says
   "UVC and UAC-compatible capture card", which is not quite the same thing. The
   gadget has no HDMI identity and no EDID. This may simply be refused.
3. **Does the Steam Deck kernel ship the UVC gadget module?** It is
   `g_webcam`/`CONFIG_USB_CONFIGFS_F_UVC`. Nothing has checked.

The tests found real bugs while being written: a `usecs-<height>p-link` name
written twice by two format groups, silently colliding; a `v4l2_format` missing
its nested `v4l2_field` and trailing reserved bytes, which made the struct 40
bytes instead of 56 and the ioctl number wrong; and that same ioctl needing
`_IOWR` rather than `_IOR`, since the kernel writes back the format it accepted.

## Install

Decky Loader → **Developer → Install Plugin from ZIP File** → `decky-capture.zip`.

## Use

1. **Exit to desktop mode.** Game Mode will not work.
2. Plug USB-C into the Deck, USB-C into the host.
3. Start streaming. The host should enumerate a new camera.

## How it works

| | |
| --- | --- |
| `gadget.py` | Builds the configfs UVC gadget: formats, control links, bind to UDC |
| `capture.py` | Picks a capture method, builds the ffmpeg command, splits the MJPEG stream |
| `v4l2out.py` | Writes frames into the gadget's V4L2 output device |
| `sink.py` | Runs ffmpeg and the feed loop, with backpressure |
| `main.py`, `src/index.tsx` | Decky surface and UI |

ffmpeg does the pixel work in C with a SIMD JPEG encoder, which matters — this
would not hold 30 fps at 1280x800 in Python on a Deck's CPU. Python is left with
framing and copying.

The kernel documentation is blunt about the one thing that matters here:

> there is no way for the gadget to refuse a set format that the host instructs
> it to set, so it is important that this step is completed accurately to ensure
> the host never asks for a format that can't be provided

So the advertised formats are a contract, not documentation. `gadget.format_tree`
builds the tree as data and the tests pin it, precisely because the shape is the
part most likely to be wrong in detail.

## Limitations

- **Desktop mode only**, for the DRM master reason above.
- **1280x800 at 30 fps, matching the panel.** Higher is not more detail; see above.
- **A second or two of latency** through encode, USB and the host's own decode.
- **Needs ffmpeg** on the Deck, which a stock SteamOS may not have.
- **Nothing has been tested.** Treat the first run as an experiment.
