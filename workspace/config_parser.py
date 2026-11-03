"""server 端口解析器（教学任务：初始版本存在待修复问题）。

契约（parse_port）：
- 输入必须是字符串；自动去除首尾空白。
- 空字符串或全空白：拒绝。
- 支持 "0x"/"0X" 前缀的十六进制（前缀大小写均可，前缀后必须至少一位十六进制数字）。
- 其余情况只接受十进制数字串（不允许正负号、小数点等任何其他字符）。
- 合法范围 [0, 65535]，越界拒绝。
- 成功返回 int；非法输入抛出 ValueError。

契约（load_server_config）：
- 读取 server.conf，逐行解析 "key = value"；
- 遇到 key 为 "port" 的行时用 parse_port 校验；
- 空行与以 # 开头的注释行忽略；
- 任何非法行抛出 ValueError。
"""

import os


def parse_port(raw):
    if not isinstance(raw, str):
        raise ValueError("port input must be a string")

    text = raw.strip()
    if not text:
        raise ValueError("port input is empty")

    # 已知问题（待修复）：当前只接受十进制，0x 前缀的十六进制会被拒绝。
    if not text.isdigit():
        raise ValueError("invalid port format: %r" % raw)

    value = int(text, 10)
    if value < 0 or value > 65535:
        raise ValueError("port out of range [0, 65535]: %d" % value)
    return value


def load_server_config(path):
    """解析 server.conf，返回 dict；非法行抛 ValueError。"""
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
