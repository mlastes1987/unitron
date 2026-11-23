"""Build a local English preview and an IPS patch from the verified Japanese ROM."""

import argparse
import hashlib
import itertools
import json
import struct
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_SHA256 = "3033ac4911185e37bc3dbc2fcba29dedf424ace2fcb425b5017034d4901bf246"
BASE_ROM = (
    ROOT / "Kikou Seiki Unitron - Sono Tsuide. Hikari Umareru Chi Yori. (Japan).ngc"
)
ROM_BASE = 0x200000
VWF_CODE = 0x1E0000
VWF_CONTROL = 0x1E0100
PROLOGUE_CONTROL = 0x1E0180
VWF_ADVANCES = 0x1E0200
VWF_MASKS = 0x1E0300
MENU_TEXT = 0x1E6400
PROLOGUE_TILES = 0x1EA000
VWF_MODE = b"\xff\x08"
NATIVE_MODE = b"\xff\x09"
END = b"\xff\x00"
NEWLINE = b"\xff\x01"
PLAYER_NAME = b"\xff\x05"
PLAYER_TOKEN = "{PLAYER}"
PROLOGUE_POINTERS = (
    (0x238A4, 0x21572), (0x238EE, 0x21643), (0x23945, 0x21728),
    (0x2399A, 0x218E2), (0x239F0, 0x21A5B), (0x23A28, 0x21B2C),
    (0x23A7A, 0x21C90), (0x23AB2, 0x21E24), (0x23AF7, 0x22001),
    (0x23B55, 0x2216C), (0x23BA9, 0x222F3), (0x23BEB, 0x22423),
    (0x23C3D, 0x22530), (0x23C81, 0x22768),
)


def font_data(shapes):
    advances = bytearray()
    masks = bytearray()
    for code in range(32, 127):
        char = chr(code)
        rows = shapes[char]
        ink = 0
        for row in rows:
            ink |= row
        left = 5 - ink.bit_length()
        right = (ink & -ink).bit_length() - 1
        advances.append(3 if char == " " else 5 - left - right + 1)

        # K2GE stores eight packed 2-bit pixels in each little-endian word.
        # The window begins as color 3. Clear one plane to draw color 1.
        for shift in range(8):
            for row in rows:
                combined = 0xFFFFFFFF
                for x in range(max(left, 0), 5):
                    if row & (0x10 >> x):
                        combined &= ~(2 << (30 - 2 * (x - left + shift)))
                masks.extend(struct.pack("<HH", combined >> 16, combined & 0xFFFF))
    return advances, masks


def assemble(assembler, converter):
    with tempfile.TemporaryDirectory(prefix="unitron-asm-") as work:
        assembly = Path(work) / "vwf.p"
        binary = Path(work) / "vwf.bin"
        subprocess.run(
            [assembler, "-q", "-o", str(assembly), str(ROOT / "vwf.asm")],
            check=True,
        )
        subprocess.run([converter, str(assembly), str(binary)], check=True)
        return binary.read_bytes()


def write_change(rom, offset, expected, replacement):
    if len(expected) != len(replacement):
        raise ValueError(f"Size mismatch at {offset:#08x}")
    if rom[offset:offset + len(expected)] != expected:
        raise ValueError(f"Unexpected source bytes at {offset:#08x}")
    rom[offset:offset + len(expected)] = replacement


def write_erased(rom, offset, payload):
    if not VWF_CODE <= offset <= offset + len(payload) <= 0x1F0000:
        raise ValueError("English data must fit in the reserved cartridge sector")
    write_change(rom, offset, b"\xff" * len(payload), payload)


def address(offset, size):
    return (ROM_BASE + offset).to_bytes(size, "little")


def text_record(lines, advances, columns, *, blank_rows=0, clear=False):
    encoded = []
    for line in lines:
        text = line.encode("ascii")
        if any(not 32 <= code <= 126 for code in text):
            raise ValueError(f"Unsupported text {line!r}")
        width = sum(advances[code - 32] for code in text)
        if width > columns * 8:
            raise ValueError(f"Text is too wide by {width - columns * 8} px: {line!r}")
        encoded.append(text)
    prefix = b"\xff\x02" if clear else b""
    return prefix + VWF_MODE + (NEWLINE * (1 + blank_rows)).join(encoded) + NATIVE_MODE + END


def indent(pixels, advances):
    space = advances[0]
    return " " * ((pixels + space // 2) // space)


def choices(yes_x, no_x, advances):
    before_yes = indent(yes_x, advances)
    yes_width = sum(advances[ord(char) - 32] for char in before_yes + "Yes")
    return before_yes + "Yes" + indent(no_x - yes_width, advances) + "No"


def wrap_prologue(paragraph, advances):
    def width(line):
        width = 0
        for index, run in enumerate(line.split(PLAYER_TOKEN)):
            if index:
                # A native name is tile-aligned. Allow up to eight cells.
                width = (width + 7) // 8 * 8 + 64
            width += sum(advances[ord(char) - 32] for char in run)
        return width

    line = ""
    for word in paragraph.split():
        candidate = f"{line} {word}" if line else word
        if width(candidate) <= 144:
            line = candidate
        else:
            if not line or width(word) > 144:
                raise ValueError(f"Prologue word too wide: {word!r}")
            yield line
            line = word
    if line:
        yield line


def render_prologue_run(text, shapes, advances):
    width = sum(advances[ord(char) - 32] for char in text)
    row_width = (width + 7) // 8 * 8
    rows = [0] * 8
    x = 0
    for char in text:
        glyph = shapes[char]
        ink = 0
        for row in glyph:
            ink |= row
        if ink:
            # Trim the side bearings to match the runtime menu font.
            shift = row_width - x - ink.bit_length()
            for y, row in enumerate(glyph):
                rows[y] |= row << shift if shift >= 0 else row >> -shift
        x += advances[ord(char) - 32]

    for column in range(0, row_width, 8):
        words = []
        for y, row in enumerate(rows):
            shadow = (rows[y - 1] >> 1) if y else 0
            word = 0
            for pixel in range(column, column + 8):
                bit = 1 << (row_width - 1 - pixel)
                color = 1 if row & bit else 3 if shadow & bit else 0
                word = (word << 2) | color
            words.append(word)
        yield struct.pack("<8H", *words)


def prologue_data(shapes, advances):
    sections = json.loads((ROOT / "prologue.json").read_text(encoding="utf-8"))
    atlas = {}

    def encode(line):
        runs = []
        for text in line.split(PLAYER_TOKEN):
            tiles = bytearray()
            for tile in render_prologue_run(text, shapes, advances):
                if not any(tile):
                    tiles.append(0)
                else:
                    index = atlas.setdefault(tile, len(atlas))
                    tiles.extend(VWF_MODE + index.to_bytes(2, "little"))
            runs.append(tiles)
        return PLAYER_NAME.join(runs)

    records = []
    for paragraphs in sections:
        layout = [
            (NEWLINE * 2).join(encode(line) for line in wrap_prologue(paragraph, advances))
            for paragraph in paragraphs
        ]
        records.append((NEWLINE * 3).join(layout) + NEWLINE + END)
    return records, b"".join(atlas)


def build_rom(base, renderer):
    if len(base) != 0x200000 or hashlib.sha256(base).hexdigest() != BASE_SHA256:
        raise ValueError("The input does not match the 2 MiB Japanese retail ROM")
    if not 0x100 < len(renderer) <= VWF_ADVANCES - VWF_CODE:
        raise ValueError("Renderer exceeds its reserved space")

    rom = bytearray(base)
    shapes = {
        char: bytes.fromhex(bitmap)
        for char, bitmap in json.loads((ROOT / "font.json").read_text(encoding="utf-8")).items()
    }
    if any(len(rows) != 8 or any(row >= 32 for row in rows) for rows in shapes.values()):
        raise ValueError("Font must contain 5 by 8 bitmaps")
    advances, masks = font_data(shapes)
    write_erased(rom, VWF_CODE, renderer)
    write_erased(rom, VWF_ADVANCES, advances)
    write_erased(rom, VWF_MASKS, masks)

    # Replace the native glyph path and control dispatcher with guarded hooks.
    write_change(rom, 0xA1E0, bytes.fromhex("1d d0 a2 20"), b"\x1b" + address(VWF_CODE, 3))
    write_change(
        rom, 0xA1F3, bytes.fromhex("26 00 eb 12 eb ee 02"),
        b"\x1b" + address(VWF_CONTROL, 3) + b"\0" * 3,
    )
    write_change(
        rom, 0x24321, bytes.fromhex("c9 cc 07 c9 ee 02"),
        b"\x1b" + address(PROLOGUE_CONTROL, 3) + b"\0" * 2,
    )

    cursor = MENU_TEXT

    def redirect(pointer, original, payload, *, size=4):
        nonlocal cursor
        write_erased(rom, cursor, payload)
        write_change(rom, pointer, address(original, size), address(cursor, size))
        cursor += len(payload)

    redirect(0x14958, 0x14A65, text_record(["   Main Menu"], advances, 8, clear=True))
    labels = ["New Game", "Load Game", "Link", "Options", "Help", "Parts List"]
    redirect(
        0x14976, 0x14A71,
        text_record(["   " + label for label in labels], advances, 10, blank_rows=1, clear=True),
    )

    # New Game and Load Game share the same file-picker labels.
    file_picker = cursor
    redirect(
        0x1448C, 0x14630,
        text_record(["Select a File"], advances, 14),
    )
    for pointer in (0x14493, 0x1449A):
        write_change(rom, pointer, address(0x14630, 4), address(file_picker, 4))
    redirect(
        0x144CF, 0x1463E,
        text_record(["", indent(42, advances) + "<Empty>"], advances, 16),
    )

    file_choices = choices(72, 104, advances)
    for pointer, original, prompt in (
        (0x14668, 0x1469E, "Load this file?"),
        (0x14674, 0x146C3, "Delete this file?"),
        (0x1468A, 0x146E8, "Use this file?"),
    ):
        redirect(pointer, original, text_record([prompt, "", file_choices], advances, 16))
    redirect(
        0x14683, 0x1470A,
        text_record(["Save data will be lost.", "Overwrite this file?", file_choices], advances, 16),
    )

    for pointer, original, prompt in (
        (0x20727, 0x20786, "Choose your player"),
        (0x207F0, 0x2086C, "Choose your partner"),
    ):
        redirect(
            pointer, original,
            text_record([prompt, "", indent(56, advances) + "Left / Right"], advances, 18),
        )
    for pointer, original, label in (
        (0x209FA, 0x20A2C, "Player"),
        (0x20A07, 0x20A33, "Partner"),
    ):
        redirect(pointer, original, text_record([label], advances, 6))
    redirect(
        0x208D4, 0x2095C,
        text_record(["Start the game?", "", choices(40, 88, advances)], advances, 18),
    )

    for pointer, original, label in (
        (0x20DE9, 0x20DF5, "Player name"),
        (0x20DED, 0x20E0A, "Partner name"),
        (0x20DF1, 0x20E1F, "Erel name"),
    ):
        redirect(pointer, original, text_record([label, "Enter a name"], advances, 11))
    for pointer, original, label in (
        (0x213D3, 0x213B9, "Hirag."),
        (0x213D9, 0x213BF, "Katak."),
        (0x213DF, 0x213C5, "Back"),
        (0x213E5, 0x213CA, "Done"),
    ):
        redirect(pointer, original, text_record([label], advances, 5))

    # Native name entry and save data still store the game's own character codes.
    name_defaults = (
        (0x20CC7, 0x20CCF, "Jess"), (0x20CCB, 0x20CD4, "Nina"),
        (0x20CEB, 0x20D13, "Axel"), (0x20CEF, 0x20D19, "Lumia"),
        (0x20CF3, 0x20D1E, "Veitz"), (0x20CF7, 0x20D24, "Sera"),
        (0x20CFB, 0x20D28, "Gavi"), (0x20CFF, 0x20D2D, "Yuuna"),
        (0x20D03, 0x20D32, "Ryuka"), (0x20D07, 0x20D37, "Phil"),
        (0x20D0B, 0x20D3C, "Lloyd"), (0x20D0F, 0x20D41, "Millie"),
    )
    for pointer, original, name in name_defaults:
        redirect(pointer, original, bytes(0xA4 + ord(char) - ord("A") for char in name.upper()) + END)
    redirect(
        0x2BDE0, 0x2BE55,
        text_record(["Skip the prologue?", choices(88, 120, advances)], advances, 18),
    )
    redirect(
        0xBC45, 0xEFF6A,
        text_record(["Attack", "Special", "Items", "Charge", ""], advances, 9, blank_rows=1, clear=True),
    )
    for pointer, original, label in (
        (0xBC53, 0xEFF95, "Surrender"),
        (0xBC5A, 0xEFF8E, "Retreat"),
    ):
        redirect(pointer, original, text_record([label], advances, 9))

    battle_prompts = (
        (0xF012C, "What will you do?"),
        (0xF0135, "What's the plan?"),
        (0xF0140, "What will you do?"),
        (0xF014A, "What's your move?"),
        (0xF0153, "What should we do?"),
        (0xF0160, "So, what's the plan?"),
        (0xF016A, "What are we gonna do?"),
        (0xF0175, "What would you like to do?"),
        (0xF0180, "What are you planning?"),
        (0xF018C, "What should we do?"),
        (0xF0196, "Um... what should we do?"),
    )
    for index, (original, label) in enumerate(battle_prompts):
        redirect(0xF010B + index * 3, original, text_record([label], advances, 18, clear=True), size=3)

    records, tiles = prologue_data(shapes, advances)
    for (pointer, original), payload in zip(PROLOGUE_POINTERS, records, strict=True):
        redirect(pointer, original, payload)
    write_erased(rom, PROLOGUE_TILES, tiles)
    return bytes(rom)


def ips_patch(base, patched):
    if len(base) != len(patched):
        raise ValueError("IPS writer expects cartridge size to stay fixed")
    records = bytearray(b"PATCH")
    offset = 0
    for changed, pairs in itertools.groupby(zip(base, patched), lambda pair: pair[0] != pair[1]):
        if not changed:
            offset += sum(1 for _ in pairs)
            continue
        payload = bytes(result for _, result in pairs)
        for index in range(0, len(payload), 0xFFFF):
            start = offset + index
            chunk = payload[index:index + 0xFFFF]
            records.extend(start.to_bytes(3, "big") + len(chunk).to_bytes(2, "big") + chunk)
        offset += len(payload)
    return records + b"EOF"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, default=BASE_ROM)
    parser.add_argument("--out", type=Path, default=ROOT / "build")
    parser.add_argument("--asw", default="asw")
    parser.add_argument("--p2bin", default="p2bin")
    args = parser.parse_args()

    base = args.rom.read_bytes()
    patched = build_rom(base, assemble(args.asw, args.p2bin))
    args.out.mkdir(parents=True, exist_ok=True)
    rom_path = args.out / "unitron-english-preview.ngc"
    patch_path = args.out / "unitron-english-preview.ips"
    if any(path.resolve() == args.rom.resolve() for path in (rom_path, patch_path)):
        raise ValueError("Choose a separate output directory from the base ROM")
    rom_path.write_bytes(patched)
    patch_path.write_bytes(ips_patch(base, patched))
    print(rom_path)
    print(patch_path)


if __name__ == "__main__":
    main()
