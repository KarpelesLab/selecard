# SeleCard III (STX0031) — RF protocol

Independent over-the-air analysis of the Bunka Shutter SeleCard III garage-shutter
remote. Every example below uses the synthetic ID **`01234567`**; no real device ID
appears in this repository.

Conventions: bit strings are written **as transmitted, first bit on the left**. IDs and
checksums are carried **LSB-first**. `fold(x) = (x & 0xFFFF) + (x >> 16)`.

## 1. Physical layer

| Property | Value |
|---|---|
| Carrier | 426.0737 MHz (426 MHz ARIB STD-T67 telecontrol sub-band) |
| Modulation | 2-FSK, constant envelope |
| Tone shift | ~4 kHz (logical 0 ≈ carrier +0.1 kHz, logical 1 ≈ carrier +4.2 kHz) |
| Line code | Manchester |
| Half-bit period | 830 µs (≈ 602 bit/s) |
| TX power | ≤ 1 mW (specified-low-power device) |

A press emits one burst of **~3 back-to-back frame repeats**. Each frame is a leading
run of alternating half-bits (**preamble**, ~64 half-bits, a square wave at the half-bit
rate), a Manchester-violation **delimiter**, then the Manchester-coded **data**.

Manchester convention used here: data bit `1` → half-bits `10`, data bit `0` → `01`.

## 2. Frame

The data payload is **50 bits**:

```
[ 000 ][ 24-bit ID, LSB-first ][ 8-bit command ][ 15-bit checksum ]
  3            24                     8                15
```

### ID

The 8-digit number printed on the card is a **plain decimal integer** in `0 … 16777215`
(= 2²⁴−1, i.e. up to 8 decimal digits — the manufacturer's "~16.77 million combinations").
It is *not* octal: real cards contain the digits 8 and 9. It is transmitted as a 24-bit
value, **LSB-first**.

Example — ID `01234567`:

```
decimal 1234567 = 0x12D687
24-bit, MSB-first : 0001 0010 1101 0110 1000 0111
ID field (LSB-first, as sent) : 111000010110101101001000
```

### Command

8-bit **one-hot**; the set bit selects the button:

| Command | field `[27:35]` |
|---|---|
| OPEN | `00010000` |
| STOP | `00100000` |
| CLOSE | `01000000` |
| REGISTER | `10000000` (recessed enrolment button) |

### Checksum

The 15-bit trailer `[35:50]` is a **folded additive checksum** with the command mixed in
as a one-hot add:

```
K = { OPEN: 2^11, STOP: 2^10, CLOSE: 2^9, REGISTER: 2^8 }
checksum = ( fold(ID) + K_command ) mod 2^15        # written LSB-first
```

Example — ID `01234567`, OPEN:

```
fold(1234567)          = 54937
(54937 + 2^11) mod 2^15 = 56985
56985, 15-bit LSB-first = 100110010111101   <- the checksum field
```

It is a plain additive check, **not** a cryptographic MAC — so a valid frame can be
computed from the printed ID alone.

## 3. Fixed-code / replay

There is **no rolling counter**: the same button always transmits the same frame. Frames
are therefore fully replayable, and synthesisable from the ID. `selecard.py send …`
builds a frame from scratch and (with `--tx`) transmits it.

## 4. Registration (over-the-air enrolment)

A new card is enrolled *by an already-registered card*, over the air, within radio range
of the shutter:

1. Press REGISTER **3×** → the card beeps ~1 s (enters enrolment mode).
2. Enter the new card's 8 decimal digits: **OPEN ×N** for digit value *N*, **STOP** to
   advance a digit, **CLOSE** to restart. Digits 0–9 → the ID is decimal.
3. Press REGISTER **1×** to commit.

Only the two REGISTER presses transmit; the digit entry is **RF-silent** (counted locally
on the card). The commit packet is a longer frame carrying four 24-bit fields:

```
[ own-ID ][ A = check(own-ID) ][ new-ID ][ C = check(new-ID) ]
```

`A` and `C` use the **same fold**, seeded differently:

```
A = ( 0x800080 + 256 * fold(own_ID) ) mod 2^24     # LSB-first
C = ( 0xFF00FF + 256 * fold(new_ID) ) mod 2^24     # LSB-first
```

Because these are again simple additive checks, an enrolment packet is computable from
the IDs alone — the system has no cryptographic protection at any layer.

## 5. Method (brief)

Captured with a HackRF at 2 MS/s; channelised to the carrier; FSK demodulated with a
hysteresis slicer and a drift-tracking half-bit clock; Manchester-decoded. The ID model
was confirmed by matching decoded frames to the numbers printed on donor cards. The
checksums were solved as additive (a XOR/linear model was falsified by a held-out value:
`ID+1` and `ID+65536` each flip the same checksum bit, and setting both *carries* into the
next bit — the signature of addition, not XOR). Command synthesis was validated by
operating a real shutter with a frame built entirely from these formulas.
