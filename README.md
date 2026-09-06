# SeleCard

Reverse engineering of the **Bunka Shutter (文化シヤッター) SeleCard** garage-shutter card
remotes, with small Python tools to decode recordings and synthesise / transmit
OPEN · STOP · CLOSE commands. Both generations are covered:

* **SeleCard III** (`STX0031`, ~426 MHz, 2-FSK) — [`selecard3.py`](selecard3.py), [`PROTOCOL3.md`](PROTOCOL3.md)
* **SeleCard II** (`STX9531C`, ~315 MHz, OOK) — [`selecard2.py`](selecard2.py), [`PROTOCOL2.md`](PROTOCOL2.md)

These are discontinued consumer products. This is an independent, clean-room analysis from
over-the-air observation, published for interoperability and security research.

> **No real device IDs appear anywhere in this repository.** Every example uses the
> obviously-synthetic ID `01234567`.

## SeleCard III (426 MHz)

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

Both models are **insecure by design**: anyone who can observe or guess the printed ID can
command the shutter. The III is fixed-code (frames replay directly); the II adds only a
weak forward counter that a synthesise-and-sweep defeats. This is published so owners and
installers understand that risk on equipment that is long out of support. **Only transmit
to receivers you own or are explicitly authorised to test.** RF transmission is regulated;
comply with the rules of your jurisdiction. The authors accept no liability (see
[`LICENSE`](LICENSE)).

### Usage — `selecard3.py`

Requires Python 3 with `numpy`, and the [HackRF](https://github.com/greatscottgadgets/hackrf)
CLI tools for capture / transmit (both tools share these requirements).

**Decode a recording** (capture with e.g.
`hackrf_transfer -r rec.cs8 -f 425900000 -s 2000000 -l 24 -g 20` while a button is held):

```console
$ python3 selecard3.py decode rec.cs8
ID 01234567  OPEN   checksum OK
ID 01234567  STOP   checksum OK
```

**Operate the shutter** — `--id` is the card you act as (the 8-digit number printed on
it; leading zeros optional). Writes a `cs8` clip; add `--tx` to transmit it:

```console
$ python3 selecard3.py --id 1234567 open
synthesised OPEN for ID 01234567 -> selecard_tx.cs8 (518 ms, checksum 100110010111101)

$ python3 selecard3.py --id 1234567 open --tx --tx-gain 40
```

`open`, `stop`, and `close` all work the same way.

**Register (enrol) a card ID onto a shutter** — `--id` is a card **already registered**
to that shutter (the authoriser); the positional argument is the new ID to add:

```console
$ python3 selecard3.py --id 1234567 reg 7654321
synthesised REGISTER: enrol ID 07654321 using card 01234567 -> selecard_reg.cs8 (839 ms)

$ python3 selecard3.py --id 1234567 reg 7654321 --tx --tx-gain 40
```

This exists because the enrolment check is, again, just an additive checksum of the two
IDs — there is no cryptographic protection. It is the clearest demonstration of the
system's weakness, and is provided for that reason. **Only enrol cards onto receivers
you own or are authorised to modify.**

## SeleCard II (315 MHz) — `selecard2.py`

The earlier II generation is OOK, MSB-first, and — unlike the fixed-code III — carries a
**12-bit forward counter**: the receiver only accepts a counter *ahead* of the last one it
saw (within a window measured at 255). So `selecard2.py` tracks a forward counter per ID
(in `~/.selecard2/`) and advances it only on a real transmission. See [`PROTOCOL2.md`](PROTOCOL2.md).

**Decode** (capture with e.g.
`hackrf_transfer -r rec.cs8 -f 314800000 -s 2000000 -l 24 -g 20` while a button is held):

```console
$ python3 selecard2.py decode rec.cs8
ID 01234567  OPEN   counter  842  checksum OK
```

**Operate** — `--id` is the printed card number. Writes a `cs8`; add `--tx` to transmit:

```console
$ python3 selecard2.py --id 1234567 open --tx
$ python3 selecard2.py --id 1234567 close --tx
```

If presses stop being accepted (the tracked counter fell behind the receiver), re-enter
the window once with a full sweep:

```console
$ python3 selecard2.py --id 1234567 resync --tx     # sweeps counter 0..4095
```

The forward window (255 of 4096) is weak: it stops naive stale-frame replay, but a
synthesise-and-sweep — exactly what `resync` does — defeats it, so the II is, in practice,
no harder to operate than the fixed-code III once you have the printed ID.

## Status

* **SeleCard III (STX0031, 426 MHz FSK)** — fully decoded; commands validated by
  operating a real shutter with synthesised frames.
* **SeleCard II (STX9531C, ~315 MHz OOK)** — fully decoded; general checksum confirmed on
  multiple cards and the forward counter beaten, validated by operating a real shutter.

## License

MIT © Karpeles Lab Inc — see [`LICENSE`](LICENSE).
