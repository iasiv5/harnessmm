"""Small configuration parser utilities."""

import errno

MAX_PORT = 65535


def parse_port(value):
    """Parse a port number from a string.

    Accepts decimal strings (e.g. "8080") or hex strings with a "0x"/"0X"
    prefix (e.g. "0x1F90" -> 8080).

    Raises ValueError for anything else, including out-of-range ports.
    """
    if not isinstance(value, str):
        raise ValueError("port must be a string")

    text = value.strip()
    if not text:
        raise ValueError("port must not be empty")

    if text[:2] in ("0x", "0X"):
        digits = text[2:]
        if not digits:
            raise ValueError("hex port requires at least one digit")
        if not all(c in "0123456789abcdefABCDEF" for c in digits):
            raise ValueError("invalid hex port")
        number = int(digits, 16)
    else:
        if not text.isdigit() or not text.isascii():
            raise ValueError("invalid decimal port")
        number = int(text, 10)

    if number < 0 or number > MAX_PORT:
        raise ValueError("port out of range [0, 65535]")

    return number


def load_server_config(path):
    """Load a simple key=value server configuration file.

    Returns a dict mapping keys to string values, skipping blank lines and
    lines starting with '#'. Values for the 'port' key are validated with
    parse_port and converted to int. Raises ValueError for missing files or
    malformed lines.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.EISDIR, errno.EACCES):
            raise ValueError("cannot read config file: %s" % path)
        raise

    config = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("invalid config line: %r" % line)
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "port":
            config[key] = parse_port(value)
        else:
            config[key] = value
    return config
