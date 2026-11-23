# Kikou Seiki Unitron English preview

This project adapts the Japanese retail ROM with a proportional 5×8 English font from the Odysselya translation. The preview covers the main menu, file selection, character setup, all 14 sections of the scrolling prologue, and the battle command menu and its prompts. Most gameplay text, menu subpages, and title graphics still need translation.

Apply `build/unitron-english-preview.ips` to the 2,097,152-byte Japanese ROM with CRC32 `F029F1C0`. The build also produces a local `build/unitron-english-preview.ngc`.

## Build and check

Build with Python 3.10 or later and [Macroassembler AS](https://github.com/flamewing/asl-releases/releases). Use the full executable paths if the tools are outside your PATH.

```powershell
python build.py --asw .scratch/asl/asw.exe --p2bin .scratch/asl/p2bin.exe
python headless.py --core .scratch/mednafen_ngp_libretro.dll --sample-crawl
```

The headless route uses the [Beetle NeoPop core](https://docs.libretro.com/library/beetle_neopop/), starts with an isolated save directory, and captures the translated screens in `build/qa/`.

The menu renderer assembles glyphs at runtime. The scrolling prologue uses precomposed tiles from the same font and the game's native player-name insertion. Edit the translation in `prologue.json` and rebuild to rewrap it by pixel width. The Odysselya glyph bitmaps in `font.json` are distributed under the terms in `LICENSE-font.txt`.
