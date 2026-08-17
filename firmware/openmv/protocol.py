"""Pure-Python VIS framing shared by OpenMV and host-side tests."""

VIS_PREFIX = "VIS"


def _clamp(value, minimum, maximum):
    value = int(value)
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value


def xor_checksum(payload):
    """Return the XOR of ASCII characters between '$' and '*'."""
    checksum = 0
    for char in payload:
        code = ord(char)
        if code > 0x7F:
            raise ValueError("protocol payload must be ASCII")
        checksum ^= code
    return checksum


def encode_vis(seq, target_detected, score, cx, cy, in_zone):
    """Build one canonical VIS frame terminated with CRLF.

    When no target is present, all target-dependent fields are forced to zero.
    The score is a detector score from 0 to 100; for the Haar baseline it is an
    area-based quality heuristic, not a calibrated probability.
    """
    seq = _clamp(seq, 0, 65535)
    target = 1 if target_detected else 0

    if target:
        score = _clamp(score, 0, 100)
        cx = _clamp(cx, 0, 4095)
        cy = _clamp(cy, 0, 4095)
        zone = 1 if in_zone else 0
    else:
        score = 0
        cx = 0
        cy = 0
        zone = 0

    payload = "%s,%d,%d,%d,%d,%d,%d" % (
        VIS_PREFIX,
        seq,
        target,
        score,
        cx,
        cy,
        zone,
    )
    return "$%s*%02X\r\n" % (payload, xor_checksum(payload))


def verify_frame(frame):
    """Return True when a frame has valid delimiters and XOR."""
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("ascii")
        except Exception:
            return False

    line = frame.strip()
    if not line.startswith("$"):
        return False

    star = line.rfind("*")
    if star <= 1 or len(line) - star != 3:
        return False

    payload = line[1:star]
    provided = line[star + 1 :]
    try:
        expected = int(provided, 16)
        return xor_checksum(payload) == expected
    except Exception:
        return False


def decode_vis(frame):
    """Decode a verified VIS frame into a six-integer tuple."""
    if not verify_frame(frame):
        raise ValueError("invalid VIS frame")

    if isinstance(frame, bytes):
        frame = frame.decode("ascii")
    payload = frame.strip()[1 : frame.rfind("*")]
    fields = payload.split(",")
    if len(fields) != 7 or fields[0] != VIS_PREFIX:
        raise ValueError("unexpected VIS fields")
    return tuple(int(value) for value in fields[1:])


def next_sequence(seq):
    return (int(seq) + 1) & 0xFFFF
