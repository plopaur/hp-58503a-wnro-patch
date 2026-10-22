# HP 58503A firmware Rev A — GPS week-rollover (WNRO) patch

A small patch for the HP 58503A GPS Time and Frequency Reference Receiver
(firmware 58503-80007..80010 Rev A). It corrects the date the unit uses when its
Motorola Oncore GPS receiver gives a date that is 1024 weeks (about 19.6 years)
too early.

> **Status:** verified by checksum checks and CPU emulation. **Not yet tested on
> real hardware.** Keep your original chips.

**Download:** [Release v1.0](https://github.com/plopaur/hp-58503a-wnro-patch/releases/tag/v1.0)
(the patch script and this document as PDF).

## What's in this repository

| File | Description |
|---|---|
| `wnro_patch.py` | Script that builds the patched firmware from your own copy of the original |
| `README.md` | This document |

**No firmware images are included.** The 58503A firmware is copyrighted by HP,
including the patched versions, which are mostly HP's code. You need to supply
your own copy, read from your unit's flash chips. `.gitignore` excludes `*.BIN`
and `patched/` so images can't be committed by accident.

### Checksums

Use these to confirm that your dumps are the right firmware, and that your
patched build matches the one tested here.

| Image | Description | SHA-256 |
|---|---|---|
| `58503_80010_RevA_1M.BIN` | Original, bank 1, even bytes | `73080037e9ecb0231e3ed5dbf7c0c658bd2f9d3b9cc0aff64752cfdfb409d1d1` |
| `58503_80008_RevA_1L.BIN` | Original, bank 1, odd bytes | `dcb63a7a63847913b0013c33fea6d99a28b4b21764343cc37c38fea9c1dde4b1` |
| `58503_80009_RevA_2M.BIN` | Original, bank 2, even bytes | `004414f6dc48bc0e653e5ed319e5e7c5001664caffe69bcc2f37328f23b75664` |
| `58503_80007_RevA_2L.BIN` | Original, bank 2, odd bytes | `6ea846c6c3a7a71b70d3b1852968285e27f47545f340d8d08a92b069ba1fea81` |
| `58503_80009_RevA_2M_WNRO.BIN` | Patched, bank 2, even bytes | `b523d338ed40e39c421fc1fd28dd3da32f3a1a2e4854201772879ed3e586741e` |
| `58503_80007_RevA_2L_WNRO.BIN` | Patched, bank 2, odd bytes | `b82ed48ad221eb4c73b5400ed2413e075d5f086672cd71be02015e35cf62ca79` |
| `58503A_RevA.BIN` | Original, combined 512 KB image | `a8676aae6ce89ee20ad572965cf5b1721f7f0ddb66f19f590cfd4e909e807709` |
| `58503A_RevA_WNRO.BIN` | Patched, combined 512 KB image | `0434b83a7eb1b8f02e6e77d8f92f368ddf01644da4d39689da6eb8fcef5efc59` |

**Only two chips change:** `80009` (2M) and `80007` (2L). The script also
writes `_WNRO` copies of `80010` and `80008`, but they are byte-identical to the
originals and don't need reprogramming.

The patched checksums apply only to a build with the default cutoff date
(2026-01-01). A different `--pivot` gives different files.

## The problem

The 58503A gets its date from the Motorola Oncore GPS receiver inside it. The
receiver reports the date (month, day, 16-bit year) in its `@@Ea` (8-channel) or
`@@Ba` (6-channel) status message. GPS transmits only a 10-bit week number, so
the receiver must guess which 1024-week period it is in. Once an Oncore passes
its rollover, it reports a date exactly 1024 weeks too early.

HP's firmware never works out the date itself; it copies what the Oncore sends.
It never checks whether the year is plausible.

What a wrong date affects:

- the clock and the reported date (`:PTIME:DATE?`)
- the event log and the display
- leap-second scheduling, which depends on the date

## Firmware analysis

| Item | Details |
|---|---|
| CPU | Motorola 68332 (CPU32), 16-bit bus |
| ROM layout | Two 256 KB banks, each made of an even-byte chip (M) and an odd-byte chip (L): bank 1 = `0x00000–0x3FFFF` (1M `80010`, 1L `80008`), bank 2 = `0x40000–0x7FFFF` (2M `80009`, 2L `80007`) |
| Chips | 128 KB AMD-style flash |
| Oncore message table | `0x50F82`, 60 bytes per entry |
| `@@Ba` / `@@Ea` handlers | Call the generic field copier at `0x504EC` / `0x50512` |
| Field copier | `0x4FEB2`: writes the date to RAM `$100F20` as month (byte), day (byte), year (word). **Everything that uses the date reads it from there.** |
| Date helpers | `0x110FA` month/day/year → year/month/day, `0x110D6` year/month/day → month/day/year, `0x10F46` year/month/day → Modified Julian Day (MJD), `0x10E72` MJD → year/month/day |

### ROM checksums

The boot loader adds up two regions before starting the application:

| Region | Sum stored at |
|---|---|
| `0x10000–0x3FFFB` | `0x3FFFC` |
| `0x40000–0x7FFFB` | `0x7FFFC` |

Each region has two 16-bit sums: one over even-address bytes, one over
odd-address bytes. The 4 stored bytes are laid out as `[even_hi, odd_hi,
even_lo, odd_lo]`. That way each chip holds its own checksum in its last two
bytes.

## The patch

Total change: 152 bytes (75 on the 2M chip, 77 on the 2L chip). Bank 1 is not
touched.

1. **Hooks:** in both the `@@Ba` handler (`0x504EC`) and the `@@Ea` handler
   (`0x50512`), the call to the field copier is redirected:

   ```
   ; original                       ; patched
   jsr     $4FEB2(pc)               jsr     $7F000
   lea     12(a7),a7                lea     12(a7),a7
   movea.l d0,a2                    ; (moved into the new routine)
   ```

2. **New routine** (130 bytes) at `0x7F000`, in flash that was unused (filled
   with `0xFF`):

   ```
   07f000  move.l  12(a7),-(a7)      ; copy the caller's 3 arguments
           move.l  12(a7),-(a7)
           move.l  12(a7),-(a7)
           jsr     $4FEB2            ; original field copier
           lea     12(a7),a7
           movea.l d0,a2             ; replaced instruction
           movem.l d2,-(a7)
           cmpi.w  #1980,$100F22     ; year earlier than 1980 (no fix yet)? -> out
           bcs     out
           move.l  $100F20,-(a7)     ; m:d:yyyy
           jsr     $110FA            ; -> yyyy:mm:dd
           addq.l  #4,a7
           move.l  d0,-(a7)
           jsr     $10F46            ; -> MJD
           addq.l  #4,a7
           move.l  d0,d2
   loop:   cmpi.l  #PIVOT_MJD,d2     ; date >= cutoff? -> done
           bcc     done
           addi.l  #7168,d2          ; +1024 weeks
           bra     loop
   done:   cmp.l   d0,d2             ; unchanged? -> out
           beq     out
           move.l  d2,-(a7)
           jsr     $10E72            ; MJD -> yyyy:mm:dd
           addq.l  #4,a7
           tst.b   d0                ; fix HP bug: day 0 means Dec 31 of a leap year
           bne     store
           subi.l  #$10000,d0        ; year - 1
           move.w  #$0C1F,d0         ; 12/31
   store:  move.l  d0,-(a7)
           jsr     $110D6            ; -> m:d:yyyy
           addq.l  #4,a7
           move.l  d0,$100F20
   out:    movem.l (a7)+,d2
           rts
   ```

3. **Checksum:** the bank 2 checksum at `0x7FFFC` is recalculated. The bank 1
   checksum does not change.

The routine saves d2 and leaves a2 exactly as the original code did, so the
handlers that call it are unaffected.

### Cutoff date

- Dates from 1980-01-01 up to the day before the cutoff get +1024 weeks,
  repeated until they reach the cutoff.
- Dates on or after the cutoff are left unchanged.
- Dates before 1980 are left unchanged. The receiver reports these before it
  knows the time.

With the default cutoff of **2026-01-01**, reported dates are correct until
**2045-08-16**. The cutoff must not be later than the date you install the
patch.

### Bug in HP's own code

HP's MJD → date routine (`0x10E72`) returns December 31 of every leap year as
"January 0" of the next year. The patch corrects this for its own result only.
HP's original call sites still have the bug.

## Building

The script needs only Python 3.7 or later, with no extra packages. It takes the
directory containing the four original chip images, named exactly as in the
[checksums](#checksums) table:

```bash
shasum -a 256 58503_*_RevA_*.BIN
```

Each dump must match its original checksum above. If two dumps from the same
bank look swapped, rename them. If they don't match at all, your unit has
different firmware and this patch does not apply.

To check the firmware as a whole, combine the four dumps into one 512 KB image,
the way the CPU sees it. Each bank interleaves its M (even) and L (odd) chip,
and bank 2 follows bank 1:

```bash
python3 -c "i=lambda m,l: bytes(b for p in zip(open(m,'rb').read(),open(l,'rb').read()) for b in p); open('58503A_RevA.BIN','wb').write(i('58503_80010_RevA_1M.BIN','58503_80008_RevA_1L.BIN')+i('58503_80009_RevA_2M.BIN','58503_80007_RevA_2L.BIN'))"
```

```bash
shasum -a 256 58503A_RevA.BIN
```

The result must match the original combined checksum above. Then build the
patched images:

```bash
python3 wnro_patch.py . patched --pivot 2026-01-01
```

- The first argument is the directory with the originals, the second the output
  directory.
- `--pivot YYYY-MM-DD` sets the cutoff date.

The script checks that the input is an unpatched Rev A image (by its ROM
checksums and the original hook bytes) and that the patch area is empty. It then
writes the recalculated checksum and prints it.

To check the patched build as a whole, combine its four chip images the same
way and compare the result with the patched combined checksum:

```bash
python3 -c "i=lambda m,l: bytes(b for p in zip(open(m,'rb').read(),open(l,'rb').read()) for b in p); open('58503A_RevA_WNRO.BIN','wb').write(i('patched/58503_80010_RevA_1M_WNRO.BIN','patched/58503_80008_RevA_1L_WNRO.BIN')+i('patched/58503_80009_RevA_2M_WNRO.BIN','patched/58503_80007_RevA_2L_WNRO.BIN'))"
```

```bash
shasum -a 256 58503A_RevA_WNRO.BIN
```

## Verification done

- Disassembled the hooks and the new routine to confirm the instructions are as
  intended.
- Both ROM checksum regions of the patched image match their stored sums.
- The combined images built with the commands above are byte-identical to the
  image the script patches in memory, and splitting them again gives back the
  four chip images exactly.
- The only bytes that differ from the original are the two hooks, the new
  routine and the bank 2 checksum (152 bytes).
- Ran the original and the patched combined images side by side in a 68020
  emulator (unicorn). Each run calls HP's real `@@Ba` or `@@Ea` handler with
  destination `$100F20`, the same as the firmware's message table does, so it
  goes through HP's field copier and date routines:
  - every reported date from 1980-01-01 to 2045-08-16 (23,970 days), through
    both handlers: 0 errors. This includes all dates that rolled back once
    (e.g. 2007-02-27 → 2026-10-13) or twice (e.g. 1987-07-14 → 2026-10-13).
  - dates before 1980 and an all-zero date are left exactly as the original
    firmware leaves them
  - apart from the corrected date, RAM (including the time fields) and the
    registers the caller relies on match the original firmware's results
    exactly, and the stack is balanced

## Installing

1. Read your four original chips and save the images.
2. Compare each dump's SHA-256 with the original checksums above. Each chip
   should match the one with its part number. If one doesn't, do not continue.
3. Build the patched images as described under [Building](#building) and check
   them against the patched checksums.
4. Program `58503_80009_RevA_2M_WNRO.BIN` and `58503_80007_RevA_2L_WNRO.BIN`
   into a matching pair of chips. Check the part number on your board and set
   the programmer to match.
5. Put them in the same sockets the originals came from (2M and 2L).
6. Power up. If the unit does not start, a checksum failed: check the chip
   placement and programming.
7. Once the unit is tracking satellites, check `:PTIME:DATE?` and
   `:PTIME:TIME?`.

To go back to the original firmware, refit the original chips.

## Limitations

- **Not proven on hardware.** The patch handles any date shifted back by a whole
  number of 1024-week periods, as long as it falls in 1980 or later.
- The patch changes only how the HP firmware reads the date. It cannot fix
  rollover problems inside the Oncore's own firmware, such as trouble
  acquiring satellites.
- One debug screen is not corrected. It shows the date the Oncore returns to a
  direct date query (`@@Ac`).
- The patched firmware still reports the same version. There is no marker
  string; the change can only be seen in the code at `0x7F000`.
- Rev A only. The Z3801A is closely related but needs its own patch: see
  [hp-z3801a-wnro-patch](https://github.com/plopaur/hp-z3801a-wnro-patch).
- With the default cutoff, reported dates will be wrong again from 2045-08-17.
  Rebuild with a later `--pivot` before then.

## License

Copyright (c) 2026 Philipp Lopaur.

The patch script and this documentation are released under the
[MIT License](LICENSE).

The license does not cover the 58503A firmware itself, which remains
copyrighted by HP and its successors. That includes images built with
`wnro_patch.py`.

## Disclaimer

Use at your own risk. This is an unofficial patch, not supported by HP,
Agilent, Keysight or Symmetricom.
