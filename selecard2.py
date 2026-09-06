#!/usr/bin/env python3
"""
selecard2.py — Bunka Shutter SeleCard II (STX9531C) ~315 MHz remote: decode & transmit.

Reverse-engineered, manufacturer-independent tool for the *earlier* SeleCard II
wireless garage-shutter remote (the 426 MHz SeleCard III is handled by selecard3.py).
It can:

  * decode                 — demodulate a HackRF `cs8` recording and print the frames
                             it contains (card ID, button, counter, checksum-OK);
  * --id N open/stop/close — synthesise a command frame for card N and (with --tx) send it,
                             using a forward counter kept just ahead of the receiver;
  * --id N resync          — sweep the counter 0..4095 (used once if the tracked counter
                             ever falls behind the receiver's stored value).

Unlike the fixed-code SeleCard III, the II carries a **12-bit forward counter**: the
receiver stores the last counter it accepted for a card ID and only acts on a frame whose
counter is ahead of that, within a forward window. So a stale replay is refused; you send
a *fresh, forward* counter. This tool remembers the counter per ID in ~/.selecard2/<id>
and advances it only on a real transmission.

`--id` is global and names the card you operate as. Examples:
    python3 selecard2.py --id 1234567 open --tx
    python3 selecard2.py --id 1234567 resync --tx      # if presses stop being accepted
    python3 selecard2.py decode recording.cs8

Radio summary (see PROTOCOL2.md for the full analysis):
  * carrier ~315.011 MHz, OOK/ASK, ~802 us half-bit Manchester (~312 bit/s)
  * frame  = 67 bits, MSB-first, repeated while the button is held
  * data   = preamble(16) header(7) [24-bit ID] [4-bit cmd] [8-bit ctr_lo] [4-bit ctr_hi] [4-bit checksum]
  * ID     = the 8-digit number printed on the card, a plain decimal integer, MSB-first
  * check  = ( -( nibXOR(FC) ^ cmd ^ nibXOR(ID) ^ 7 ) ) & 0xF, FC = full 12-bit counter

The checksum and ID model are general (validated on multiple cards); the counter is the
only anti-replay, and it is weak (a small forward window). Use only on equipment you own
or are authorised to test. MIT-licensed; see LICENSE. Requires numpy + the hackrf CLI.
"""
import argparse
import os
import subprocess
import sys

import numpy as np

# ---- radio / framing constants (measured) ---------------------------------
FS = 2_000_000            # cs8 sample rate the examples use
CENTER = 314_800_000      # HackRF center used for capture/transmit (keeps signal off DC)
CARRIER = 315_011_000     # SeleCard II carrier (~315.011 MHz)
HALFBIT_US = 802.0        # Manchester half-bit period
PREAMBLE = "1010101010101011"
HEADER = "0010001"
# 2-bit command code lives in the top two bits of a 4-bit field (low two bits are 0):
CMD = {"open": 4, "close": 8, "stop": 12}     # nibble values


# ---- ID / command / checksum ----------------------------------------------
def nibxor(v):
    """XOR-fold an integer's hex nibbles down to 4 bits."""
    r = 0
    while v:
        r ^= v & 0xF
        v >>= 4
    return r


def checksum(fc, cmd_nib, card_id):
    """The 4-bit frame checksum for full counter `fc`, command nibble, and card ID."""
    return (0 - (nibxor(fc) ^ cmd_nib ^ nibxor(card_id) ^ 7)) & 0xF


def frame_bits(card_id, command, fc):
    """The 67 transmitted bits (MSB-first) of one command frame."""
    if not 0 <= card_id < (1 << 24):
        raise ValueError("ID must be a 24-bit value (0 .. 16777215)")
    cn = CMD[command]
    ck = checksum(fc, cn, card_id)
    return (PREAMBLE + HEADER + format(card_id, "024b") + format(cn, "04b")
            + format(fc & 0xFF, "08b") + format((fc >> 8) & 0xF, "04b")
            + format(ck, "04b"))


def frame_halfbits(card_id, command, fc):
    """One frame as its on-air half-bit string (Manchester: bit 1 -> '10', 0 -> '01';
    half-bit '1' = carrier ON). Used by the YARD Stick One OOK backend."""
    return "".join("10" if b == "1" else "01" for b in frame_bits(card_id, command, fc))


# ---- low-level DSP (OOK envelope) -----------------------------------------
def find_offset(x, exclude_dc=20_000.0):
    """Locate the OOK carrier's offset (Hz) from the recording center — the strongest
    narrowband component, ignoring LO/DC leakage."""
    seg = x[:min(len(x), 8_000_000)]
    seg = seg - seg.mean()
    S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(len(seg)))))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(seg), 1 / FS))
    S[np.abs(freqs) < exclude_dc] = 0
    return float(freqs[np.argmax(S)])


def _envelope(x, offset, dec=10, smooth=4):
    """Mix `offset` Hz to DC, low-pass (boxcar), decimate → normalised OOK envelope."""
    t = np.arange(len(x))
    xb = x * np.exp(-2j * np.pi * offset / FS * t)
    k = 20
    cs = np.concatenate([[0], np.cumsum(xb)])
    lp = (cs[k:] - cs[:-k]) / k
    mag = np.abs(lp[::dec])
    mag = np.convolve(mag, np.ones(smooth) / smooth, "same")
    m = mag.max()
    return (mag / m if m else mag), FS / dec


def _frame_spans(env, fsd, gap_ms=4.0, min_ms=20.0):
    on = (env > 0.28).astype(np.int8)
    runs, cur, ln = [], int(on[0]), 1
    for v in on[1:]:
        if int(v) == cur:
            ln += 1
        else:
            runs.append((cur, ln)); cur = int(v); ln = 1
    runs.append((cur, ln))
    spans, i0, pos = [], None, 0
    for s, l in runs:
        if s and i0 is None:
            i0 = pos
        if (not s) and l / fsd * 1000 > gap_ms and i0 is not None:
            spans.append((i0, pos)); i0 = None
        pos += l
    if i0 is not None:
        spans.append((i0, pos))
    return [(a, b) for a, b in spans if (b - a) / fsd * 1000 >= min_ms]


def _decode_frame(env, fsd, nbits=67):
    """Fit the half-bit clock and slice one 67-bit frame by ON-pulse position
    (ON in the first half = 1, ON in the second half = 0)."""
    hb0 = HALFBIT_US * 1e-6 * fsd
    best = None
    for th in np.arange(hb0 * 0.98, hb0 * 1.02, hb0 * 0.003):
        for st in np.arange(0, 2 * th, max(1.0, th / 40)):
            tot, ok = 0.0, True
            for kk in range(nbits):
                s = st + kk * 2 * th
                i2 = int(s + 2 * th)
                if i2 > len(env):
                    ok = False; break
                tot += abs(env[int(s):int(s + th)].mean() - env[int(s + th):i2].mean())
            if ok and (best is None or tot > best[0]):
                best = (tot, th, st)
    if best is None:
        return None
    _, th, st = best
    bits = []
    for kk in range(nbits):
        s = st + kk * 2 * th
        bits.append("1" if env[int(s):int(s + th)].mean() > env[int(s + th):int(s + 2 * th)].mean() else "0")
    return "".join(bits)


# ---- decode ----------------------------------------------------------------
def decode(path, offset=None):
    """Yield (id, command, counter, checksum_ok) for each frame in a cs8 recording.
    `offset` is the carrier's Hz offset from the recording center; auto-detected if None
    (so this works on real captures *and* on this tool's own transmit files)."""
    raw = np.fromfile(path, dtype=np.int8).astype(np.float32)
    x = raw[0::2] + 1j * raw[1::2]
    if offset is None:
        offset = find_offset(x)
    env, fsd = _envelope(x, offset)
    inv = {v: k for k, v in CMD.items()}
    seen = set()
    for a, b in _frame_spans(env, fsd):
        pad = int(HALFBIT_US * 1e-6 * fsd * 2)
        seg = np.clip(env[max(0, a - pad):b + pad], 0, 1)
        bits = _decode_frame(seg, fsd)
        if not bits or bits[:16] != PREAMBLE or bits[16:23] != HEADER:
            continue
        idv = int(bits[23:47], 2)
        cn = int(bits[47:51], 2)
        if cn not in inv:
            continue
        fc = (int(bits[59:63], 2) << 8) | int(bits[51:59], 2)
        ck = int(bits[63:67], 2)
        ok = ck == checksum(fc, cn, idv)
        key = (idv, cn, fc, ck)
        if key not in seen:
            seen.add(key)
            yield idv, inv[cn], fc, ok


# ---- synthesise + transmit -------------------------------------------------
def frame_env(bits, amp=110.0):
    """One frame's OOK envelope: Manchester bit 1 -> ON,OFF ; bit 0 -> OFF,ON."""
    hb = HALFBIT_US * 1e-6 * FS
    edges = [int(round(i * hb)) for i in range(2 * len(bits) + 1)]
    e = np.zeros(edges[-1], np.float32)
    for k, b in enumerate(bits):
        h0, hm, h1 = edges[2 * k], edges[2 * k + 1], edges[2 * k + 2]
        if b == "1":
            e[h0:hm] = amp
        else:
            e[hm:h1] = amp
    return e


def build_cs8(card_id, command, counters, gap_ms=8.8, amp=110.0):
    """Synthesise the OOK waveform for a burst of frames (one per counter); return cs8."""
    gap = np.zeros(int(gap_ms / 1000 * FS), np.float32)
    env = np.concatenate([np.concatenate([frame_env(frame_bits(card_id, command, c & 0xFFF), amp), gap])
                          for c in counters])
    t = np.arange(len(env))
    iq = env * np.exp(2j * np.pi * (CARRIER - CENTER) / FS * t)
    cs8 = np.empty(2 * len(iq), np.int8)
    cs8[0::2] = np.clip(np.round(iq.real), -127, 127)
    cs8[1::2] = np.clip(np.round(iq.imag), -127, 127)
    return cs8


def transmit(cs8, tx_gain=40, path="/tmp/selecard2_tx.cs8"):
    cs8.tofile(path)
    cmd = ["hackrf_transfer", "-t", path, "-f", str(int(CENTER)),
           "-s", str(int(FS)), "-a", "1", "-x", str(int(tx_gain))]
    print("+ " + " ".join(cmd))
    subprocess.run(cmd, check=True)


# ---- forward-counter state (per ID) ---------------------------------------
STATE = os.path.expanduser("~/.selecard2")


def get_counter(card_id, default=1):
    try:
        return int(open(os.path.join(STATE, str(card_id))).read().strip()) & 0xFFF
    except Exception:
        return default


def set_counter(card_id, val):
    os.makedirs(STATE, exist_ok=True)
    open(os.path.join(STATE, str(card_id)), "w").write(str(val & 0xFFF))


def _tx(card_id, command, counters, args):
    """Transmit each counter's frame via the chosen radio backend."""
    if args.radio == "yardstick":
        import yardstick
        yardstick.send_ook([frame_halfbits(card_id, command, fc) for fc in counters],
                           CARRIER, HALFBIT_US, index=args.yardstick_index)
    else:
        transmit(build_cs8(card_id, command, counters), args.tx_gain)


# ---- CLI -------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="SeleCard II (~315 MHz): decode / operate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  selecard2.py --id 1234567 open --tx\n"
               "  selecard2.py --id 1234567 resync --tx\n"
               "  selecard2.py decode recording.cs8")
    ap.add_argument("op", choices=["open", "stop", "close", "resync", "decode"])
    ap.add_argument("arg", nargs="?", help="decode: the cs8 recording path")
    ap.add_argument("--id", type=int, metavar="CARD_ID", help="the card you operate as")
    ap.add_argument("--counter", type=int, help="force the starting 12-bit counter")
    ap.add_argument("--repeats", type=int, default=15, help="frames per press")
    ap.add_argument("--tx", action="store_true", help="transmit with hackrf_transfer")
    ap.add_argument("--tx-gain", type=int, default=40, help="hackrf TX VGA gain, 0-47")
    ap.add_argument("--radio", choices=["hackrf", "yardstick"], default="hackrf",
                    help="transmit backend (yardstick = YARD Stick One via RfCat)")
    ap.add_argument("--yardstick-index", type=int, default=0,
                    help="which YARD Stick One (RfCat device index)")
    ap.add_argument("--out", metavar="FILE", help="output cs8 path")
    ap.add_argument("--carrier", type=float, help="decode: carrier Hz (with --center)")
    ap.add_argument("--center", type=float, help="decode: recording center Hz (with --carrier)")
    args = ap.parse_args()

    if args.op == "decode":
        if not args.arg:
            ap.error("decode needs a recording path, e.g. 'decode recording.cs8'")
        offset = (args.carrier - args.center
                  if args.carrier is not None and args.center is not None else None)
        found = False
        for idv, cmd, ctr, ok in decode(args.arg, offset):
            found = True
            print(f"ID {idv:08d}  {cmd.upper():5}  counter {ctr:4d}  checksum {'OK' if ok else 'BAD'}")
        if not found:
            print("no frames decoded", file=sys.stderr)
        return

    if args.id is None:
        ap.error(f"--id is required for '{args.op}'")

    if args.op == "resync":
        counters = list(range(0, 4096))          # full forward sweep, crosses any window
        print(f"RESYNC id {args.id:08d}: OPEN sweep counter 0..4095 via {args.radio} "
              f"(~{len(counters) * 0.116:.0f}s). The door opens as the counter passes the "
              f"receiver's stored value.")
        if args.tx:
            _tx(args.id, "open", counters, args)
            set_counter(args.id, 0)               # sweep ended at 4095; next press = +1
        else:
            build_cs8(args.id, "open", counters).tofile(args.out or "selecard2_tx.cs8")
        return

    start = args.counter if args.counter is not None else get_counter(args.id)
    counters = [(start + i) & 0xFFF for i in range(args.repeats)]
    print(f"{args.op.upper()} id {args.id:08d}: counters {start}..{counters[-1]} "
          f"({args.repeats} frame(s)) via {args.radio}")
    if args.tx:
        _tx(args.id, args.op, counters, args)
        set_counter(args.id, (counters[-1] + 1) & 0xFFF)   # advance only after a real TX
        print("transmitted.")
    else:
        build_cs8(args.id, args.op, counters).tofile(args.out or "selecard2_tx.cs8")
        print("(dry run — counter not advanced) add --tx to send")


if __name__ == "__main__":
    main()
