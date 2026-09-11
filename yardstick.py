#!/usr/bin/env python3
"""YARD Stick One (CC1111 + RfCat) transmit backend for the SeleCard tools.

Both SeleCard models are narrowband, so a Great Scott Gadgets YARD Stick One can
transmit them **natively** — no IQ synthesis, no HackRF. We reuse the exact on-air
*half-bit* strings the HackRF tools already build and clock them straight out of the
CC1111 modem:

    SeleCard II  (selecard2.py)  ->  OOK/ASK : half-bit '1' = carrier ON, '0' = OFF
    SeleCard III (selecard3.py)  ->  2-FSK   : half-bit '1' = mark tone,  '0' = space

Why the YARD Stick One suits this: it covers both bands (315 MHz in 300-348, 426 MHz
in 391-464), has hardware ASK/OOK and 2-FSK modems, and puts out a clean ~+10 dBm
carrier with none of the DC-offset / LO-leakage that complicates transmitting these
narrowband signals on a wideband SDR. You configure the modem and send bytes.

Requires RfCat (`pip install rfcat`, which provides `rflib`) and a YARD Stick One on
USB. RfCat addresses multiple sticks by index, so a multi-radio install can drive one
band per stick (e.g. index 0 @ 315, index 1 @ 426).

------------------------------------------------------------------------------------
STATUS: brought up on a YARD Stick One (rfcat 2.0.1 / rflib) — the transmit path runs
cleanly for both OOK and 2-FSK. Note `d.cleanup()` after every transmit is required:
without it the daemon EP5 threads tear down mid-USB-read and the process dies with a
SIGBUS/segfault (harmless-looking but it corrupts the exit). RF *decode* is still
unconfirmed without a receiver to capture the stick's output. Two things to check on
first use (each a one-line fix):

  (1) 2-FSK tone polarity — if OPEN/STOP/CLOSE come out swapped or the frame won't
      decode, the CC1111's '1'->+deviation convention is inverted vs ours: either
      pass a negative deviation, or `send_2fsk(..., invert=True)`.
  (2) Stray preamble/sync — we set sync mode 0 so the CC1111 sends the frame raw
      (our framing already contains its own preamble). If a receiver won't lock,
      capture the stick's own output on a HackRF/SDR and compare to a real frame.

If a killed/crashed process leaves the stick unresponsive (USBTimeoutError loop),
re-enumerate it once: `python3 -c "import usb.core as u; u.find(idVendor=0x1d50,
idProduct=0x605b).reset()"`.
------------------------------------------------------------------------------------
"""


def bits_to_bytes(halfbits):
    """Pack a half-bit string (first bit first, MSB-first within each byte) into
    bytes, zero-padded up to a whole byte. A trailing run of '0's is OFF (II) /
    space (III) — i.e. silence — so the padding is harmless."""
    s = halfbits + "0" * (-len(halfbits) % 8)
    return bytes(int(s[i:i + 8], 2) for i in range(0, len(s), 8))


def _drate_baud(halfbit_us):
    """Half-bits per second: each half-bit chip is one modem bit."""
    return int(round(1_000_000 / halfbit_us))


def _open(index):
    from rflib import RfCat          # lazy import: the HackRF path needs no RfCat
    d = RfCat(index)
    d.setModeIDLE()
    return d


def send_ook(frames, freq_hz, halfbit_us, index=0):
    """Transmit each half-bit string in `frames` as OOK/ASK (SeleCard II).
    '1' = carrier ON. The radio is opened once and each frame is one RFxmit (the
    call gap acts as the inter-frame silence)."""
    from rflib import MOD_ASK_OOK
    d = _open(index)
    try:
        d.setFreq(int(freq_hz))
        d.setMdmModulation(MOD_ASK_OOK)
        d.setMdmDRate(_drate_baud(halfbit_us))
        d.setMdmSyncMode(0)          # send frames raw; each carries its own preamble
        d.setMaxPower()
        for hb in frames:
            d.RFxmit(bits_to_bytes(hb))
    finally:
        try:
            d.setModeIDLE()
        except Exception:
            pass
        d.cleanup()          # clean thread/USB shutdown — avoids a daemon-thread SIGBUS at exit


def send_2fsk(frames, center_hz, deviation_hz, halfbit_us, index=0, invert=False):
    """Transmit each half-bit string in `frames` as 2-FSK (SeleCard III).

    center_hz    = carrier + half the tone spacing (so +deviation = mark, -deviation = space)
    deviation_hz = half the tone spacing
    invert       = flip mark/space if the CC1111 polarity is opposite ours (test item 1).
    """
    from rflib import MOD_2FSK
    tr = str.maketrans("01", "10")
    d = _open(index)
    try:
        d.setFreq(int(center_hz))
        d.setMdmModulation(MOD_2FSK)
        d.setMdmDeviatn(int(deviation_hz))
        d.setMdmDRate(_drate_baud(halfbit_us))
        d.setMdmSyncMode(0)
        d.setMaxPower()
        for hb in frames:
            d.RFxmit(bits_to_bytes(hb.translate(tr) if invert else hb))
    finally:
        try:
            d.setModeIDLE()
        except Exception:
            pass
        d.cleanup()          # clean thread/USB shutdown — avoids a daemon-thread SIGBUS at exit
