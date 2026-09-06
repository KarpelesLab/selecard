# SeleCard II (STX9531C) — RF protocol

Independent over-the-air analysis of the Bunka Shutter **SeleCard II** garage-shutter
remote — the earlier, ~315 MHz generation (the 426 MHz SeleCard III is in
[`PROTOCOL.md`](PROTOCOL.md)). Every example uses the synthetic ID **`01234567`**; no real
device ID appears in this repository.

Conventions: bit strings are written **as transmitted, first bit on the left**. Unlike the
III, the II sends everything **MSB-first**. `nibXOR(v)` = XOR of `v`'s hex nibbles.

## 1. Physical layer

| Property | Value |
|---|---|
| Carrier | ~315.011 MHz |
| Modulation | **OOK / ASK** (keyed carrier), not FSK |
| Line code | Manchester |
| Half-bit period | ~802 µs (≈ 312 bit/s) |
| TX power | ≤ 1 mW (specified-low-power device) |

Manchester as OOK: read each bit from where its ON pulse falls — data bit `1` → `ON,OFF`
(ON in the first half-bit), data bit `0` → `OFF,ON`. A press repeats the frame while the
button is held; a single tap is 1–2 frames.

## 2. Frame

**67 bits**, MSB-first:

```
[ preamble ][ header ][ 24-bit ID ][ cmd ][ ctr_lo ][ ctr_hi ][ cksum ]
    16          7          24         4        8         4        4
 1010101010101011  0010001                nibble   low 8    high 4
```

### ID

The 8-digit number printed on the card is a **plain decimal integer** in `0 … 16777215`,
transmitted as a 24-bit value **MSB-first** (the III sends it LSB-first — that is the one
wire-level difference in the ID encoding between the two models).

Example — ID `01234567`:

```
decimal 1234567 = 0x12D687
ID field (MSB-first, as sent) : 000100101101011010000111
```

### Command

A **2-bit code** in the top two bits of the 4-bit field (the low two bits are always `0`):

| Command | field `[47:51]` | nibble |
|---|---|---|
| OPEN | `0100` | 4 |
| CLOSE | `1000` | 8 |
| STOP | `1100` | 12 |

### Counter

A **12-bit counter** `FC`, split across the frame and sent low-part-first:
`ctr_lo` = bits `[51:59]` (low 8), `ctr_hi` = bits `[59:63]` (high 4), so
`FC = ctr_hi<<8 | ctr_lo`. It **increments by 1 per transmitted frame** and wraps at 4096.

This is the II's only anti-replay (see §3).

### Checksum

The 4-bit trailer `[63:67]` is a nibble-XOR fold, two's-complement negated:

```
cksum = ( -( nibXOR(FC) ^ cmd ^ nibXOR(ID) ^ 7 ) ) & 0xF
```

`cmd` is the command nibble (4/8/12), `7` is a fixed family seed, and the ID enters as its
own nibble-XOR fold — so, like the III, a valid frame is computable from the printed ID
alone. (The low bits of this are linear/XOR; the negate makes the high bits carry, which is
why a pure-XOR model fits only part of it.)

Example — ID `01234567`, OPEN, `FC = 0x100` (ctr_lo `0x00`, ctr_hi `0x1`):

```
nibXOR(0x12D687) = 7      nibXOR(0x100) = 1
cksum = ( -( 1 ^ 4 ^ 7 ^ 7 ) ) & 0xF = ( -5 ) & 0xF = 0xB = 1011
```

## 3. Counter / forward window (the II's anti-replay)

Unlike the fixed-code III, the receiver stores the **last counter it accepted** for a card
ID and accepts a new frame only if its counter is **ahead** of that, within a forward
window:

```
accept  iff  (counter - last_seen) mod 4096  in  [1 .. W]
```

The window `W` was measured on a live receiver to be **255** (0xFF), out of the 4096-value
counter space. On acceptance the receiver advances `last_seen` to the new counter.

**Security consequence.** `W = 255 / 4096` is weak. It refuses naive replay of a *stale*
frame (one whose counter the receiver has already passed), but it does **not** stop an
attacker who can synthesise: sweeping the counter upward (0 → 4095) is guaranteed to enter
the window, and once inside, keeping the counter at `last_seen + 1` stays valid across the
counter's periodic wraparound. `selecard2.py` does exactly this: `resync` sweeps to enter
the window, then normal presses track `last_seen + 1`.

## 4. Method (brief)

Captured with a HackRF at 2 MS/s; mixed to the carrier and low-passed to an OOK envelope;
Manchester-decoded by ON-pulse position with a per-frame clock fit. The ID model was
confirmed against the printed numbers on donor cards. The checksum was solved by sweeping
the **full** counter range — a formula fitting a narrow range proved to be an overfit
until a rollover capture (holding the button until the counter wrapped 255→0) exposed the
counter's high nibble and the true fold. The ID term was confirmed on a second card, and a
frame built entirely from these formulas — with a swept-then-tracked forward counter —
operated a real shutter end to end.
