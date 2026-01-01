#!/usr/bin/env python3
"""
WNRO (GPS week-number rollover) patch for HP 58503A firmware (58503-80007..80010 Rev A).

Firmware layout (68332 / CPU32, 16-bit bus, two banks of 2x 128 KB AMD-style flash):
    1M = 58503_80010 (even bytes)  1L = 58503_80008 (odd bytes)   -> 0x00000-0x3FFFF
    2M = 58503_80009 (even bytes)  2L = 58503_80007 (odd bytes)   -> 0x40000-0x7FFFF

The Motorola Oncore engine reports the date in @@Ea (8ch) / @@Ba (6ch) messages. After a
GPS week rollover its internally derived date is 1024 weeks too early. Both messages are
parsed by the generic field copier at 0x4FEB2 into RAM at 0x100F20 (month.b, day.b, year.w),
and every consumer (RTC set, PTIME, leap-second logic, logs, display) reads it from there.

The patch redirects the parser call in both handlers to a new routine placed in unused
flash at 0x7F000. That routine runs the original parser, then:
    mjd = date_to_mjd(repack(0x100F20))           (firmware routines 0x110FA, 0x10F46)
    while mjd < PIVOT_MJD: mjd += 7168            (1024 weeks)
    0x100F20 = unpack(mjd_to_date(mjd))           (firmware routines 0x10E72, 0x110D6)
(0x10E72 has an original bug returning "Jan 0" for Dec 31 of leap years; the patch fixes
up that case after the call.)
Dates with year < 1980 (no fix / garbage) are left untouched.

The per-chip 16-bit byte-sum checksums checked by the boot loader (stored in the last 2
bytes of each chip) are recomputed.

Usage: wnro_patch.py [--pivot YYYY-MM-DD] SRC_DIR OUT_DIR
"""
import argparse
import datetime
import os
import struct
import sys

ROMS = {  # name: (bank, lane)  lane 0 = even/MSB, 1 = odd/LSB
    "58503_80010_RevA_1M.BIN": (0, 0),
    "58503_80008_RevA_1L.BIN": (0, 1),
    "58503_80009_RevA_2M.BIN": (1, 0),
    "58503_80007_RevA_2L.BIN": (1, 1),
}
CHIP = 0x20000
PATCH_ADDR = 0x7F000

# (address, original bytes, patched bytes)
HOOK_JSR = bytes.fromhex("4EB9") + struct.pack(">I", PATCH_ADDR) + bytes.fromhex("4FEF000C")
HOOKS = [
    (0x504EC, bytes.fromhex("4EBAF9C44FEF000C2440"), HOOK_JSR),  # @@Ba handler
    (0x50512, bytes.fromhex("4EBAF99E4FEF000C2440"), HOOK_JSR),  # @@Ea handler
]

# Known original checksums, so we never patch an unexpected image.
ORIG_CKS = {(0x10000, 0x3FFFC): (0x908D, 0x684D), (0x40000, 0x7FFFC): (0xCD64, 0x7399)}


def mjd(date):
    return (date - datetime.date(1858, 11, 17)).days


def build_patch(pivot_mjd):
    c = bytearray()
    w = lambda h: c.extend(bytes.fromhex(h))
    # --- run original parser with the caller's 3 stack args, result -> a2 (as caller did)
    w("2F2F000C 2F2F000C 2F2F000C")  # move.l 12(a7),-(a7) x3  (src, dst, desc)
    w("4EB9 0004FEB2")               # jsr     $4FEB2         generic field copier
    w("4FEF000C")                    # lea     12(a7),a7
    w("2440")                        # movea.l d0,a2          (replaced instruction)
    # --- date fix on 0x100F20
    w("48E72000")                    # movem.l d2,-(a7)
    w("0C79 07BC 00100F22")          # cmpi.w  #1980,$100F22  year
    bcs_at = len(c); w("6500")       # bcs.b   out
    w("2F39 00100F20")               # move.l  $100F20,-(a7)  mm dd yyyy
    w("4EB9 000110FA")               # jsr     $110FA         -> yyyy mm dd
    w("588F")                        # addq.l  #4,a7
    w("2F00")                        # move.l  d0,-(a7)
    w("4EB9 00010F46")               # jsr     $10F46         -> MJD
    w("588F")                        # addq.l  #4,a7
    w("2400")                        # move.l  d0,d2
    loop = len(c)
    w("0C82"); c.extend(struct.pack(">I", pivot_mjd))  # cmpi.l #PIVOT,d2
    w("6408")                        # bcc.b   done
    w("0682 00001C00")               # addi.l  #7168,d2
    c.extend(bytes([0x60, (loop - (len(c) + 2)) & 0xFF]))  # bra.b loop
    w("B480")                        # cmp.l   d0,d2
    beq_at = len(c); w("6700")       # beq.b   out
    w("2F02")                        # move.l  d2,-(a7)
    w("4EB9 00010E72")               # jsr     $10E72         MJD -> yyyy mm dd
    w("588F")                        # addq.l  #4,a7
    # 0x10E72 returns "Jan 0" for Dec 31 of leap years; map it back to Dec 31
    w("4A00")                        # tst.b   d0             day == 0 ?
    w("660A")                        # bne.b   +10
    w("0480 00010000")               # subi.l  #$10000,d0     year - 1
    w("303C 0C1F")                   # move.w  #$0C1F,d0      month 12, day 31
    w("2F00")                        # move.l  d0,-(a7)
    w("4EB9 000110D6")               # jsr     $110D6         -> mm dd yyyy
    w("588F")                        # addq.l  #4,a7
    w("23C0 00100F20")               # move.l  d0,$100F20
    out = len(c)
    w("4CDF0004")                    # movem.l (a7)+,d2
    w("4E75")                        # rts
    c[bcs_at + 1] = out - (bcs_at + 2)
    c[beq_at + 1] = out - (beq_at + 2)
    return bytes(c)


def cks(img, lo, hi):
    return sum(img[lo:hi:2]) & 0xFFFF, sum(img[lo + 1:hi:2]) & 0xFFFF


def load(src):
    img = bytearray(0x80000)
    for name, (bank, lane) in ROMS.items():
        data = open(os.path.join(src, name), "rb").read()
        assert len(data) == CHIP, name
        img[bank * 0x40000 + lane:(bank + 1) * 0x40000:2] = data
    return img


def save(img, out):
    os.makedirs(out, exist_ok=True)
    for name, (bank, lane) in ROMS.items():
        base, ext = os.path.splitext(name)
        with open(os.path.join(out, base + "_WNRO" + ext), "wb") as f:
            f.write(bytes(img[bank * 0x40000 + lane:(bank + 1) * 0x40000:2]))


def apply(img, pivot_mjd):
    for (lo, hi), want in ORIG_CKS.items():
        assert cks(img, lo, hi) == want, "unexpected firmware image (checksum mismatch)"
    for addr, orig, new in HOOKS:
        assert img[addr:addr + len(orig)] == orig, hex(addr)
        img[addr:addr + len(new)] = new
    code = build_patch(pivot_mjd)
    assert all(b == 0xFF for b in img[PATCH_ADDR:PATCH_ADDR + len(code) + 16])
    img[PATCH_ADDR:PATCH_ADDR + len(code)] = code
    for lo, hi in ORIG_CKS:
        e, o = cks(img, lo, hi)
        img[hi:hi + 4] = bytes([e >> 8, o >> 8, e & 0xFF, o & 0xFF])
    return code


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", nargs="?", default=".")
    ap.add_argument("out", nargs="?", default="patched")
    ap.add_argument("--pivot", default="2026-01-01",
                    help="dates before this get +1024 weeks; valid until pivot + 1024 weeks")
    a = ap.parse_args()
    pivot = datetime.date.fromisoformat(a.pivot)
    img = load(a.src)
    code = apply(img, mjd(pivot))
    save(img, a.out)
    print("pivot %s (MJD %d), valid until %s" % (pivot, mjd(pivot), pivot + datetime.timedelta(weeks=1024)))
    print("patch: %d bytes at 0x%X; hooks at %s" % (len(code), PATCH_ADDR, ", ".join(hex(h[0]) for h in HOOKS)))
    for lo, hi in ORIG_CKS:
        print("bank 0x%05X-0x%05X checksum even=%04X odd=%04X" % ((lo, hi) + cks(img, lo, hi)))
    print("wrote", a.out)


if __name__ == "__main__":
    sys.exit(main())
