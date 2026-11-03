"""参考答案：修复后的 parse_port（仅供实验者与审计比对，绝不暴露给被测模型，
也不放在被测模型可见的 workspace 内）。"""

import os


def parse_port(raw):
    if not isinstance(raw, str):
        raise ValueError("port input must be a string")

    text = raw.strip()
    if not text:
        raise ValueError("port input is empty")

    if text[:2].lower() == "0x":
        digits = text[2:]
        if not digits or any(c not in "0123456789abcdefABCDEF" for c in digits):
            raise ValueError("invalid hex port: %r" % raw)
        value = int(digits, 16)
    else:
        if not text.isdigit():
            raise ValueError("invalid port format: %r" % raw)
        value = int(text, 10)

    if value < 0 or value > 65535:
        raise ValueError("port out of range [0, 65535]: %d" % value)
    return value


def load_server_config(path):
    if not os.path.isfile(path):
        raise ValueError("config file not found: %s" % path)
    result = {}
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                raise ValueError("config line %d: missing '='" % lineno)
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                raise ValueError("config line %d: empty key" % lineno)
            if key == "port":
                result[key] = parse_port(value)
            else:
                result[key] = value
    return result
