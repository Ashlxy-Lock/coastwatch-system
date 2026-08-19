"""Pure-Python VIS framing shared by OpenMV and host-side tests."""

VIS_PREFIX = "VIS"
CONTROL_PREFIX = "CTL"


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


def encode_control(seq, danger, person_enable, environmental_level):
    """Build one canonical ESP32 -> OpenMV control frame.

    ``environmental_level`` is the server model level. In CTL contract v1.1,
    ``danger`` is the ESP32's effective alert: a trusted model level 2/3 OR a
    live, healthy local water alarm level 2/3. Local fault level 4 must leave
    danger clear. ``person_enable`` may also be set while danger is clear for
    fail-safe full-rate monitoring; that state never lights the red LED.
    """
    seq = _clamp(seq, 0, 65535)
    environmental_level = _clamp(environmental_level, 0, 3)
    danger = 1 if danger else 0
    person_enable = 1 if person_enable else 0
    if environmental_level >= 2 and not person_enable:
        raise ValueError(
            "environmental level >= 2 requires person monitoring"
        )
    if danger and not person_enable:
        raise ValueError("danger requires person monitoring")

    payload = "%s,%d,%d,%d,%d" % (
        CONTROL_PREFIX,
        seq,
        danger,
        person_enable,
        environmental_level,
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


def _decode_ascii_frame(frame, require_crlf=False):
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("ascii")
        except Exception:
            raise ValueError("frame must be ASCII")
    if not isinstance(frame, str):
        raise ValueError("frame must be text or bytes")
    if require_crlf and not frame.endswith("\r\n"):
        raise ValueError("control frame must end with CRLF")
    if require_crlf:
        canonical_line = frame[:-2]
        if (
            not canonical_line
            or canonical_line != canonical_line.strip()
            or "\r" in canonical_line
            or "\n" in canonical_line
        ):
            raise ValueError("control frame is not canonical")
        star = canonical_line.rfind("*")
        provided = canonical_line[star + 1 :] if star >= 0 else ""
        if canonical_line.count("*") != 1 or len(provided) != 2:
            raise ValueError("control checksum must have two digits")
        for char in provided:
            if not ("0" <= char <= "9" or "A" <= char <= "F"):
                raise ValueError("control checksum must be uppercase hex")
    if not verify_frame(frame):
        raise ValueError("invalid frame")
    return frame.strip()[1 : frame.rfind("*")]


def _parse_canonical_uint(token, maximum):
    if not token or (len(token) > 1 and token[0] == "0"):
        raise ValueError("non-canonical integer")
    for char in token:
        if char < "0" or char > "9":
            raise ValueError("non-canonical integer")
    value = int(token)
    if value > maximum:
        raise ValueError("integer outside protocol range")
    return value


def decode_vis(frame):
    """Decode a verified VIS frame into a six-integer tuple."""
    try:
        payload = _decode_ascii_frame(frame)
    except ValueError:
        raise ValueError("invalid VIS frame")
    fields = payload.split(",")
    if len(fields) != 7 or fields[0] != VIS_PREFIX:
        raise ValueError("unexpected VIS fields")
    return tuple(int(value) for value in fields[1:])


def decode_control(frame):
    """Strictly decode a canonical ESP32 -> OpenMV control frame.

    Returns ``(seq, danger, person_enable, environmental_level)``. Unlike VIS
    decoding, this command path rejects alternate numeric spellings, LF-only
    lines, out-of-range values, and inconsistent redundant state.
    """
    payload = _decode_ascii_frame(frame, require_crlf=True)
    fields = payload.split(",")
    if len(fields) != 5 or fields[0] != CONTROL_PREFIX:
        raise ValueError("unexpected CTL fields")

    seq = _parse_canonical_uint(fields[1], 65535)
    danger = _parse_canonical_uint(fields[2], 1)
    person_enable = _parse_canonical_uint(fields[3], 1)
    environmental_level = _parse_canonical_uint(fields[4], 3)
    if environmental_level >= 2 and not person_enable:
        raise ValueError("warning or critical CTL must enable monitoring")
    if danger and not person_enable:
        raise ValueError("inconsistent CTL state")
    return (seq, danger, person_enable, environmental_level)


def next_sequence(seq):
    return (int(seq) + 1) & 0xFFFF
