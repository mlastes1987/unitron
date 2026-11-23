        cpu     96C141
        maxmode on

; Cartridge addresses for the menu text engine and the generated font data.
NativeCell      equ     020A2D0h
NativeGlyph     equ     020A1E4h
NativeControl   equ     020A1FAh
Advances        equ     03E0200h
GlyphMasks      equ     03E0300h
PrologueTiles   equ     03EA000h

; Window state at XWA+0F: bit 7 enables the proportional font, bits 4-6
; hold the pixel offset into the current tile. The original flags use bits 1-2.
        org     03E0000h

DrawGlyph:
        bit     7,(XWA+0Fh)
        jr      nz,DrawProportional
        call    NativeCell
        jp      NativeGlyph

DrawProportional:
        call    NativeCell              ; XDE = current character RAM tile
        push    XIX
        ld      XIX,XWA
        ld      A,L
        sub     A,20h
        cp      A,95
        jr      c,ValidGlyph
        ld      A,31                    ; '?'
ValidGlyph:
        extz    WA
        extz    XWA
        ld      XHL,Advances
        ld      C,(XHL+WA)
        sll     8,XWA                   ; 8 offsets, 32 bytes each
        ld      XHL,GlyphMasks
        add     XHL,XWA
        ld      A,(XIX+0Fh)
        and     A,70h
        srl     4,A
        add     C,A                     ; offset after this glyph
        extz    WA
        extz    XWA
        sll     5,XWA
        add     XHL,XWA

        ld      B,0
        and     (XIX+0Fh),8Fh
        cp      C,8
        jr      c,StoreOffset
        ld      B,(XIX+04h)             ; window width in tiles
        bit     7,(XIX+0Eh)
        jr      nz,CheckRightEdge
        dec     2,B                     ; framed window's interior width
CheckRightEdge:
        dec     1,B
        cp      (XIX+06h),B
        scc     c,B                     ; B = 1 if a tile exists to the right
        inc     1,(XIX+06h)
StoreOffset:
        ld      A,C
        and     A,7
        sll     4,A
        or      (XIX+0Fh),A
        ld      C,8
DrawRow:
        ld      WA,(XHL+)
        and     (XDE),WA
        ld      WA,(XHL+)
        bit     0,B
        jr      z,NextRow
        and     (XDE+16),WA
NextRow:
        inc     2,XDE
        djnz    C,DrawRow
        ld      XWA,XIX
        pop     XIX
        rcf
        ret

; FF 08 switches a text record to ASCII. Keep the original parser and all
; its existing control codes, clearing the fractional cursor on page changes.
        org     03E0100h

TextControl:
        cp      L,8
        jr      nz,CheckEndVwf
        and     (XWA+0Fh),8Fh
        set     7,(XWA+0Fh)
        scf
        ret
CheckEndVwf:
        cp      L,9
        jr      nz,ExistingControl
        and     (XWA+0Fh),0Fh
        scf
        ret
ExistingControl:
        cp      L,0
        jr      nz,CheckReset
        and     (XWA+0Fh),0Fh
        jr      Dispatch
CheckReset:
        cp      L,3
        jr      nc,Dispatch
        and     (XWA+0Fh),8Fh
Dispatch:
        ld      H,0
        extz    XHL
        sll     2,XHL
        jp      NativeControl

; The opening crawl uses a separate text interpreter and a 128-tile ring.
; FF 08 + a word draws one precomposed tile. The original tilemap update
; owns the ring cursor, and native controls still insert player names.
        org     03E0180h

PrologueControl:
        cp      A,8
        jr      z,PrologueTile
        and     A,7
        sll     2,A
        jp      0224327h
PrologueTile:
        ld      WA,(XHL+)
        ld      (065B6h),XHL
        extz    XWA
        sll     4,XWA
        ld      XHL,PrologueTiles
        add     XHL,XWA
        ld      A,(065C1h)
        extz    WA
        add     WA,017Dh
        sll     4,WA
        add     WA,0A000h
        extz    XWA
        ld      XDE,XWA
        ld      BC,8
        ldirw   (XDE+),(XHL+)
        jp      02242EAh
