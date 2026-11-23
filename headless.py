"""Capture the English preview through the opening battle with Beetle NeoPop."""

import argparse
import ctypes as c
import struct
import tempfile
import zlib
from pathlib import Path


class GameInfo(c.Structure):
    _fields_ = [
        ("path", c.c_char_p), ("data", c.c_void_p),
        ("size", c.c_size_t), ("meta", c.c_char_p),
    ]


class Emulator:
    def __init__(self, core, rom, save_dir):
        self.core = c.CDLL(str(core.resolve()))
        self.buttons = 0
        self.frame = None
        self.pixel_format = 0
        self.directory = c.create_string_buffer(str(save_dir).encode())

        ptr, uint, size = c.c_void_p, c.c_uint, c.c_size_t
        callbacks = (
            ("environment", c.CFUNCTYPE(c.c_bool, uint, ptr)(self.environment)),
            ("video_refresh", c.CFUNCTYPE(None, ptr, uint, uint, size)(self.video)),
            ("audio_sample", c.CFUNCTYPE(None, c.c_int16, c.c_int16)(lambda left, right: None)),
            ("audio_sample_batch", c.CFUNCTYPE(size, ptr, size)(lambda data, frames: frames)),
            ("input_poll", c.CFUNCTYPE(None)(lambda: None)),
            ("input_state", c.CFUNCTYPE(c.c_int16, uint, uint, uint, uint)(self.input_state)),
        )
        self.callbacks = callbacks
        for name, callback in callbacks:
            self.bind("set_" + name, None, type(callback))(callback)

        data = rom.read_bytes()
        self.cartridge = c.create_string_buffer(data)
        # The core derives its save name from this path.
        info = GameInfo(b"unitron-qa.ngc", c.addressof(self.cartridge), len(data), None)
        self.bind("init", None)()
        if not self.bind("load_game", c.c_bool, c.POINTER(GameInfo))(c.byref(info)):
            self.bind("deinit", None)()
            raise RuntimeError(f"Beetle NeoPop could not load {rom}")
        self.run_one = self.bind("run", None)

    def bind(self, name, result, *arguments):
        function = getattr(self.core, "retro_" + name)
        function.restype = result
        function.argtypes = arguments
        return function

    def environment(self, command, data):
        if command == 10:  # RETRO_ENVIRONMENT_SET_PIXEL_FORMAT
            self.pixel_format = c.cast(data, c.POINTER(c.c_int))[0]
            return self.pixel_format in (0, 1, 2)
        if command in (9, 30, 31):  # system, assets and save directories
            c.cast(data, c.POINTER(c.c_void_p))[0] = c.addressof(self.directory)
            return True
        return command == 3  # RETRO_ENVIRONMENT_GET_CAN_DUPE

    def video(self, data, width, height, pitch):
        if data:
            self.frame = c.string_at(data, height * pitch), width, height, pitch

    def input_state(self, port, device, index, button):
        return int(port == 0 and bool(self.buttons & (1 << button)))

    def tick(self, frames, button=None):
        self.buttons = 0 if button is None else 1 << button
        for _ in range(frames):
            self.run_one()

    def press(self, button, settle):
        self.tick(1, button)
        self.tick(settle)

    def close(self):
        self.bind("unload_game", None)()
        self.bind("deinit", None)()

    def capture(self, path):
        if self.frame is None:
            raise RuntimeError("The emulator did not render a video frame")
        source, width, height, pitch = self.frame
        image = bytearray()
        for y in range(height):
            start = y * pitch
            image.append(0)  # PNG filter byte
            if self.pixel_format == 1:
                for blue, green, red, _ in struct.iter_unpack("BBBB", source[start:start + width * 4]):
                    image.extend((red, green, blue))
            else:
                red_shift, green_mask = (11, 63) if self.pixel_format == 2 else (10, 31)
                for (pixel,) in struct.iter_unpack("<H", source[start:start + width * 2]):
                    image.extend((
                        (pixel >> red_shift & 31) * 255 // 31,
                        (pixel >> 5 & green_mask) * 255 // green_mask,
                        (pixel & 31) * 255 // 31,
                    ))

        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(image)) + chunk(b"IEND", b"")
        )


def opening(emu, out, sample_crawl):
    a_button, left = 0, 6  # Libretro maps RetroPad B to Neo Geo Pocket A.
    emu.tick(1013)
    emu.press(a_button, 120)
    emu.press(a_button, 120)
    emu.capture(out / "main-menu.png")
    emu.press(a_button, 120)
    emu.capture(out / "file-picker.png")
    emu.press(a_button, 120)
    emu.capture(out / "file-confirmation.png")
    emu.press(left, 50)
    emu.press(a_button, 150)
    emu.capture(out / "player-picker.png")
    emu.press(a_button, 100)
    emu.capture(out / "player-name.png")
    emu.press(a_button, 100)
    emu.capture(out / "partner-picker.png")
    emu.press(a_button, 100)
    emu.capture(out / "partner-name.png")
    emu.press(a_button, 180)
    emu.capture(out / "party-confirmation.png")
    emu.press(a_button, 150)
    emu.capture(out / "prologue-prompt.png")
    emu.press(a_button, 250)
    emu.capture(out / "prologue.png")

    if sample_crawl:
        for frame in range(550, 22551, 550):
            emu.tick(550)
            emu.capture(out / f"crawl-{frame:05}.png")
        emu.tick(450)
    else:
        emu.tick(23000)
    emu.capture(out / "first-battle.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--rom", type=Path, default=Path("build/unitron-english-preview.ngc"))
    parser.add_argument("--out", type=Path, default=Path("build/qa"))
    parser.add_argument("--sample-crawl", action="store_true", help="capture the crawl every 550 frames")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="unitron-qa-") as work:
        emulator = Emulator(args.core, args.rom, Path(work))
        try:
            opening(emulator, args.out, args.sample_crawl)
        finally:
            emulator.close()
    print(f"Captured the opening route in {args.out}")


if __name__ == "__main__":
    main()
