def parse_port(raw):
    if not isinstance(raw, str):
        raise ValueError("port input must be a string")

    text = raw.strip()
    if not text:
        raise ValueError("port input is empty")

    lowered = text.lower()
    if lowered.startswith("0x"):
        digits = text[2:]
        if not digits or not all(c in "0123456789abcdefABCDEF" for c in digits):
            raise ValueError("invalid port format: %r" % raw)
        port = int(digits, 16)
    else:
        if not text.isdigit():
            raise ValueError("invalid port format: %r" % raw)
        port = int(text)

    if not (0 <= port <= 65535):
        raise ValueError("port out of range: %r" % raw)

    return port


def load_server_config(path):
    config = {}
    try:
        f = open(path, "r", encoding="utf-8")
    except OSError as exc:
        raise ValueError("cannot read config file: %s" % path) from exc
    with f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
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
