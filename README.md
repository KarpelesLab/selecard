# SeleCard

Reverse engineering of the **Bunka Shutter (文化シヤッター) SeleCard III** garage-shutter
remote — a `426 MHz` wireless card remote (model **STX0031**) — with a small Python tool
to decode recordings and synthesise / transmit OPEN · STOP · CLOSE commands.

The SeleCard III is a discontinued consumer product. This is an independent, clean-room
analysis from over-the-air observation, published for interoperability and security
research. See [`PROTOCOL.md`](PROTOCOL.md) for the full write-up.

> **No real device IDs appear anywhere in this repository.** Every example uses the
> obviously-synthetic ID `01234567`.

## What was found

| | |
|---|---|
| Carrier | **426.0737 MHz** (in the 426 MHz ARIB STD-T67 telecontrol sub-band) |
| Modulation | **2-FSK**, ~4 kHz shift, constant envelope |
| Line code | **Manchester**, ~602 bit/s |
| Frame | preamble → delimiter → **50 data bits**, repeated ~3× per press |
| Data | `[000][24-bit ID, LSB-first][8-bit one-hot command][15-bit checksum]` |
| ID | the **8-digit number printed on the card, read as a plain decimal integer** (0–16777215) |
| Command | one-hot: `OPEN / STOP / CLOSE` (+ a recessed REGISTER) |
| Checksum | `( fold(ID) + K_command ) mod 2^15`, LSB-first, `fold(x) = (x & 0xFFFF) + (x >> 16)` |

The system is **fixed-code** — there is no rolling counter, so frames are fully
replayable. A valid frame can be synthesised from the printed ID alone (the on-card
checksum is a simple folded additive checksum, not a cryptographic MAC).

## Security note

This documents an **insecure-by-design** fixed-code remote: anyone who can observe or
guess the printed ID can command the shutter. It is published so owners and installers
understand that risk on equipment that is long out of support. **Only transmit to
receivers you own or are explicitly authorised to test.** RF transmission is regulated;
comply with the rules of your jurisdiction. The authors accept no liability (see
[`LICENSE`](LICENSE)).

## Usage

Requires Python 3 with `numpy`, and the [HackRF](https://github.com/greatscottgadgets/hackrf)
CLI tools for capture / transmit.

**Decode a recording** (capture with e.g.
`hackrf_transfer -r rec.cs8 -f 425900000 -s 2000000 -l 24 -g 20` while a button is held):

```console
$ python3 selecard.py decode rec.cs8
ID 01234567  OPEN   checksum OK
ID 01234567  STOP   checksum OK
```

**Synthesise a command** (writes a `cs8` clip; add `--tx` to transmit it):

```console
$ python3 selecard.py send open --id 1234567
synthesised OPEN for ID 01234567 -> selecard_tx.cs8 (557 ms, checksum 100110010111101)

$ python3 selecard.py send open --id 1234567 --tx --tx-gain 40
```

`--id` is the plain 8-digit number printed on the card (leading zeros optional).

**Register (enrol) a card ID onto a shutter** — builds the over-the-air enrolment
COMMIT packet. It needs the ID of a card **already registered** to that shutter (the
authoriser), plus the new ID to add:

```console
$ python3 selecard.py reg --own 1234567 --id 7654321
synthesised REGISTER: enrol ID 07654321 using own ID 01234567 -> selecard_reg.cs8 (839 ms)

$ python3 selecard.py reg --own 1234567 --id 7654321 --tx --tx-gain 40
```

This exists because the enrolment check is, again, just an additive checksum of the two
IDs — there is no cryptographic protection. It is the clearest demonstration of the
system's weakness, and is provided for that reason. **Only enrol cards onto receivers
you own or are authorised to modify.**

## Status

* **SeleCard III (STX0031, 426 MHz FSK)** — fully decoded; commands validated by
  operating a real shutter with synthesised frames.
* **SeleCard II (STX9531C, ~315 MHz)** — earlier generation, **not yet covered**. It is
  OOK on a keyed ~315 MHz crystal driven by an NEC µPD6124A encoder (NEC-family PWM),
  so it needs its own capture; the ID appears to use the same printed-decimal format.

## License

MIT © Karpeles Lab Inc — see [`LICENSE`](LICENSE).
