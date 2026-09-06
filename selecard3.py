#!/usr/bin/env python3
"""
selecard3.py — Bunka Shutter SeleCard III (STX0031) 426 MHz remote: decode & transmit.

Reverse-engineered, manufacturer-independent tool for the SeleCard III wireless
garage-shutter remote. It can:

  * decode              — demodulate a HackRF `cs8` recording and print the command
                          frames (shutter ID, button, checksum-OK) it contains;
  * --id N open/stop/close — synthesise a command frame for card N and (with --tx) send it;
  * --id N reg M        — synthesise the enrolment packet that registers new card M onto
                          the shutter, authorised by already-registered card N.

`--id` is global and always names the card you operate as. Examples:
    python3 selecard3.py --id 1234567 open --tx
    python3 selecard3.py --id 1234567 reg 7654321 --tx
    python3 selecard3.py decode recording.cs8

Radio summary (see PROTOCOL3.md for the full analysis):
  * carrier ~426.0737 MHz, 2-FSK, ~4 kHz shift, ~602 bit/s Manchester
  * frame  = preamble + delimiter + 50 data bits, repeated ~3x per press
  * data   = [000][24-bit ID, LSB-first][8-bit one-hot command][15-bit checksum]
  * ID     = the 8-digit number printed on the card, read as a plain decimal integer
  * check  = ( fold(ID) + K_command ) mod 2^15, LSB-first, fold(x)=(x&0xFFFF)+(x>>16)

The system is fixed-code (no rolling counter): frames are fully replayable. Use only
on equipment you own or are authorised to test. MIT-licensed; see LICENSE.

Requires: numpy, and the `hackrf` CLI tools for capture/transmit.
"""
import argparse
import os
import subprocess
import sys
import tempfile

import numpy as np

# ---- radio / framing constants (measured) ---------------------------------
FS = 2_000_000            # cs8 sample rate the examples use
CENTER = 425_900_000      # HackRF center used when *capturing* (dodges the DC spike)
CARRIER = 426_073_700     # SeleCard III carrier
HALFBIT_US = 830.0        # Manchester half-bit period
# FSK tone offsets (Hz) relative to CARRIER, measured from a real transmission
TONE_SPACE = 100.0        # logical 0 half-bit
TONE_MARK = 4200.0        # logical 1 half-bit
# The FSK tones sit only ~0.1-4.2 kHz above the carrier — right on the HackRF's DC
# offset / LO leakage if transmitted at the carrier. So synthesise the signal
# TX_OFFSET Hz up and transmit with LO = CARRIER - TX_OFFSET (as the II tool does).
TX_OFFSET = 250_000.0
# YARD Stick One 2-FSK: transmit at the tones' center with ±deviation = half the spacing.
FSK_CENTER = CARRIER + (TONE_SPACE + TONE_MARK) / 2      # 426.075850 MHz
FSK_DEV = (TONE_MARK - TONE_SPACE) / 2                   # 2050 Hz
FSK_HALFBIT_US = HALFBIT_US                              # 830 us

# Framing measured from a real transmission, as half-bit levels (generic — carries no
# ID; only the data slots do). A press = preamble, then 3 data frames, each preceded by
# a delimiter (a ~17-half-bit mark run) + preamble; then a short tail.
_PREAMBLE0 = "010101010101010101010101010101010101010101010101010101010101010100"
_INTERFRAME = "0111111111111111110101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010100"
_TAIL = "011111110"

# Registration COMMIT-packet framing (longer frame; 3x 192-half-bit payloads).
_REG_PREAMBLE0 = "0001111111110101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010100010101"
_REG_INTERFRAME = "11111111111111110101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010101010100010101"
_REG_TAIL = "1111110"

# ---- command field + checksum ---------------------------------------------
# 8-bit one-hot command at data bits [27:35]; also feeds the checksum as a one-hot add.
COMMANDS = {"register": 0x100, "close": 0x200, "stop": 0x400, "open": 0x800}
COMMAND_ONEHOT = {"open": "00010000", "stop": "00100000",
                  "close": "01000000", "register": "10000000"}


def fold(idv):
    """The 24-bit ID folded to a running value: low 16 bits + high 8 bits."""
    return (idv & 0xFFFF) + (idv >> 16)


def command_checksum(idv, command):
    """15-bit command-frame checksum [35:50], returned LSB-first as a bit string."""
    val = (fold(idv) + COMMANDS[command]) & 0x7FFF
    return format(val, "015b")[::-1]


# Registration (enrolment) commit-packet checksum: [own][check(own)][new][check(new)].
# Same fold, different seeds. See PROTOCOL3.md.
REG_SEED_OWN = 0x800080   # checksum of the already-registered card's own ID
REG_SEED_NEW = 0xFF00FF   # checksum of the ID being enrolled


def reg_checksum(idv, seed):
    """24-bit registration checksum field (LSB-first bit string)."""
    return format((seed + (fold(idv) << 8)) & 0xFFFFFF, "024b")[::-1]


def reg_payload(own_id, new_id):
    """96-bit COMMIT payload [own-ID][check(own)][new-ID][check(new)], as transmitted."""
    lsb = lambda v: format(v, "024b")[::-1]
    return (lsb(own_id) + reg_checksum(own_id, REG_SEED_OWN)
            + lsb(new_id) + reg_checksum(new_id, REG_SEED_NEW))


# ---- low-level DSP ---------------------------------------------------------
def channelize(x, f_shift, dec=20):
    """Shift `f_shift` Hz to DC, band-limit, decimate — FFT-domain, chunked for big files.
    Modular bin indexing so it works for any shift, including near DC."""
    parts, chunk = [], 20_000_000
    for s in range(0, len(x), chunk):
        seg = x[s:s + chunk]
        if len(seg) < 400:
            break
        n = len(seg)
        m = n // dec
        h = m // 2
        X = np.fft.fft(seg)
        c = int(round(f_shift / FS * n)) % n
        Y = np.empty(m, complex)
        Y[:h] = X[np.arange(c, c + h) % n]        # band above carrier -> +freqs
        Y[m - h:] = X[np.arange(c - h, c) % n]    # band below carrier -> -freqs
        parts.append(np.fft.ifft(Y) * (m / n))
    return np.concatenate(parts), FS / dec


def find_offset(x, exclude_dc=2000.0):
    """Locate the signal's frequency offset (Hz) — the strongest narrowband tone,
    ignoring LO/DC leakage. Lets `decode` work regardless of the recording's center."""
    seg = x[:min(len(x), 8_000_000)]
    S = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(len(seg)))))
    freqs = np.fft.fftshift(np.fft.fftfreq(len(seg), 1 / FS))
    S[np.abs(freqs) < exclude_dc] = 0
    return float(freqs[np.argmax(S)])


def _bursts(xd, fsd):
    mag = np.abs(xd)
    # noise-relative for real captures, but capped below the peak so an all-signal
    # (synthesised) file still detects — otherwise the percentile *is* the signal.
    thr = min(np.percentile(mag, 20) * 4, mag.max() * 0.4)
    on = mag > thr
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            if (j - i) / fsd * 1000 > 20:
                runs.append([i, j])
            i = j
        else:
            i += 1
    merged = []
    for a, b in runs:
        if merged and (a - merged[-1][1]) / fsd * 1000 < 12:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if (b - a) / fsd * 1000 >= 40]


def _halfbits(seg, fsd):
    """FSK -> hysteresis-sliced level -> drift-tracking clock -> half-bit string."""
    inst = np.angle(seg[1:] * np.conj(seg[:-1])) * fsd / (2 * np.pi)
    f = np.convolve(inst, np.ones(7) / 7, "same")
    p15, p85 = np.percentile(f, 15), np.percentile(f, 85)
    mid = (p15 + p85) / 2
    band = p85 - p15
    hi, lo = mid + 0.2 * band, mid - 0.2 * band
    lv = np.empty(len(f), np.int8)
    st = 1 if f[0] > mid else 0
    for i, v in enumerate(f):
        st = 1 if v > hi else (0 if v < lo else st)
        lv[i] = st
    rl, cur, ln = [], lv[0], 1
    for v in lv[1:]:
        if v == cur:
            ln += 1
        else:
            rl.append([int(cur), ln])
            cur = v
            ln = 1
    rl.append([int(cur), ln])
    lens = np.array([l for _, l in rl])
    th = np.median(lens[lens < np.percentile(lens, 60)])
    hb = []
    for s, l in rl:
        nn = max(1, int(round(l / th)))
        hb += [str(s)] * nn
        th = 0.85 * th + 0.15 * (l / nn)
    return "".join(hb)


def _manch(s, off):
    return "".join("1" if s[i:i + 2] == "10" else ("0" if s[i:i + 2] == "01" else "X")
                   for i in range(off, len(s) - 1, 2))


# ---- decode ----------------------------------------------------------------
def decode(path, offset=None):
    """Yield (id, command, checksum_ok) for each command frame in a cs8 recording.
    `offset` is the signal's Hz offset from the recording center; auto-detected if None
    (so this works on real captures *and* on the output of `send`)."""
    raw = np.fromfile(path, dtype=np.int8).astype(np.float32)
    x = raw[0::2] + 1j * raw[1::2]
    if offset is None:
        offset = find_offset(x)
    xd, fsd = channelize(x, offset)
    inv = {v: k for k, v in COMMAND_ONEHOT.items()}
    seen = set()
    for a, b in _bursts(xd, fsd):
        if (b - a) / fsd * 1000 < 200:
            continue
        hb = _halfbits(xd[a:b], fsd)
        for off in (0, 1):
            dd = _manch(hb, off)
            for i in range(len(dd) - 50):
                fr = dd[i:i + 50]
                if "X" in fr or fr[:3] != "000":
                    continue
                cmd8 = fr[27:35]
                if cmd8 not in inv:
                    continue
                idv = int(fr[3:27][::-1], 2)
                cmd = inv[cmd8]
                ok = fr[35:50] == command_checksum(idv, cmd)
                key = (idv, cmd, fr[35:50])
                if key not in seen:
                    seen.add(key)
                    yield idv, cmd, ok
            break


# ---- synthesise + transmit -------------------------------------------------
def _frame_bits(idv, command):
    """The 50 data bits of one command frame."""
    if not 0 <= idv < (1 << 24):
        raise ValueError("ID must be a 24-bit value (0 .. 16777215)")
    return ("000" + format(idv, "024b")[::-1] + COMMAND_ONEHOT[command]
            + command_checksum(idv, command))


def command_halfbits(idv, command, repeats=3):
    """Full half-bit sequence of a command transmission (framing + `repeats` data
    frames; a real press sends 3). Manchester: data bit 1 -> '10', 0 -> '01'."""
    data = _frame_bits(idv, command)
    manch = "".join("10" if b == "1" else "01" for b in data)  # 100 half-bits
    return _PREAMBLE0 + _INTERFRAME.join([manch] * max(1, repeats)) + _TAIL


def _modulate(halfbits, amp=90.0):
    """FSK-modulate a half-bit string (mark/space tones) into a cs8 int8 array."""
    tsamp = int(round(HALFBIT_US * 1e-6 * FS))
    sig = np.empty(len(halfbits) * tsamp, complex)
    ph, k = 0.0, 0
    for c in halfbits:
        dph = 2 * np.pi * (TX_OFFSET + (TONE_MARK if c == "1" else TONE_SPACE)) / FS
        for _ in range(tsamp):
            sig[k] = amp * np.exp(1j * ph)
            ph += dph
            k += 1
    lead = np.zeros(int(0.02 * FS), complex)
    out = np.concatenate([lead, sig, lead])
    cs8 = np.empty(len(out) * 2, np.int8)
    cs8[0::2] = np.clip(np.round(out.real), -127, 127).astype(np.int8)
    cs8[1::2] = np.clip(np.round(out.imag), -127, 127).astype(np.int8)
    return cs8


def synth(idv, command, amp=120.0, repeats=3):
    """Synthesise the FSK/Manchester waveform for a command; return a cs8 int8 array."""
    return _modulate(command_halfbits(idv, command, repeats), amp)


def reg_halfbits(own_id, new_id):
    """Full half-bit sequence of a registration COMMIT (framing + 3 payload frames),
    reproducing a real enrolment transmission bit-for-bit."""
    payload = reg_payload(own_id, new_id)
    manch = "".join("10" if b == "1" else "01" for b in payload)  # 192 half-bits
    return (_REG_PREAMBLE0 + manch + _REG_INTERFRAME + manch
            + _REG_INTERFRAME + manch + _REG_TAIL)


def reg_synth(own_id, new_id, amp=90.0):
    """Synthesise the registration COMMIT waveform that enrols new_id using own_id."""
    return _modulate(reg_halfbits(own_id, new_id), amp)


def transmit(cs8, tx_gain=30, freq=CARRIER - TX_OFFSET):
    """Write the cs8 to a unique /tmp file, transmit it, then delete it."""
    fd, path = tempfile.mkstemp(prefix="selecard_tx_", suffix=".cs8", dir="/tmp")
    os.close(fd)
    try:
        cs8.tofile(path)
        cmd = ["hackrf_transfer", "-t", path, "-f", str(int(freq)),
               "-s", str(int(FS)), "-a", "1", "-x", str(int(tx_gain))]
        print("+ " + " ".join(cmd))
        subprocess.run(cmd, check=True)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---- CLI -------------------------------------------------------------------
def main():
    # Flat parser: the operation is the only required positional; every `--option` is
    # global and order-independent (that's what makes them options, not positionals).
    ap = argparse.ArgumentParser(
        description="SeleCard III: decode / operate / enrol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  selecard3.py --id 1234567 open --tx\n"
               "  selecard3.py --id 1234567 reg 7654321 --tx\n"
               "  selecard3.py decode recording.cs8")
    ap.add_argument("op", choices=["open", "stop", "close", "reg", "decode"],
                    help="operation to perform")
    ap.add_argument("arg", nargs="?",
                    help="reg: the new card ID to enrol; decode: the cs8 recording path")
    ap.add_argument("--id", type=int, metavar="CARD_ID",
                    help="the card you operate as (shutter card for open/stop/close; "
                         "an already-registered authoriser card for reg)")
    ap.add_argument("--tx", action="store_true", help="transmit with hackrf_transfer")
    ap.add_argument("--tx-gain", type=int, default=30, help="hackrf TX VGA gain, 0-47")
    ap.add_argument("--radio", choices=["hackrf", "yardstick"], default="hackrf",
                    help="transmit backend (yardstick = YARD Stick One via RfCat)")
    ap.add_argument("--yardstick-index", type=int, default=0,
                    help="which YARD Stick One (RfCat device index)")
    ap.add_argument("--repeats", type=int, default=3,
                    help="data-frame repeats per press (a real remote sends 3)")
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
        for idv, cmd, ok in decode(args.arg, offset):
            found = True
            print(f"ID {idv:08d}  {cmd.upper():5}  checksum {'OK' if ok else 'BAD'}")
        if not found:
            print("no command frames decoded", file=sys.stderr)
        return

    if args.id is None:
        ap.error(f"--id is required for '{args.op}'")

    if args.op == "reg":
        if not args.arg:
            ap.error("reg needs the new card ID, e.g. '--id 1234567 reg 7654321'")
        try:
            new_id = int(args.arg)
        except ValueError:
            ap.error(f"the new card ID must be an integer, got {args.arg!r}")
        halfbits = reg_halfbits(args.id, new_id)
        cs8 = reg_synth(args.id, new_id)
        label, default_out = (f"REGISTER: enrol ID {new_id:08d} using card {args.id:08d}",
                              "selecard_reg.cs8")
    else:  # open / stop / close
        halfbits = command_halfbits(args.id, args.op, args.repeats)
        cs8 = synth(args.id, args.op, repeats=args.repeats)
        label, default_out = (f"{args.op.upper()} for ID {args.id:08d} "
                              f"(checksum {command_checksum(args.id, args.op)})",
                              "selecard_tx.cs8")

    if args.out or not args.tx:            # keep a file to inspect (dry run, or on request)
        keep = args.out or default_out
        cs8.tofile(keep)
        print(f"synthesised {label} -> {keep} ({len(cs8) // 2 / FS * 1000:.0f} ms)")

    if args.tx:
        if args.radio == "yardstick":
            import yardstick
            yardstick.send_2fsk([halfbits], FSK_CENTER, FSK_DEV, FSK_HALFBIT_US,
                                index=args.yardstick_index)
        else:
            transmit(cs8, args.tx_gain, freq=CARRIER - TX_OFFSET)   # unique /tmp file, erased
        print(f"transmitted {label} via {args.radio}")


if __name__ == "__main__":
    main()
