"""凭据解析：全部通过环境变量驱动，仓库内不含任何本机路径或供应商特定默认值。

复跑本实验所需的三个环境变量：
  HARNESSMM_API_KEY           直接给 API key（最简单的方式）
  或
  HARNESSMM_CREDENTIALS_FILE  一个 YAML 凭据文件路径（如本地开发环境的凭据库）
  HARNESSMM_CREDENTIALS_REF   该文件中 refs 下的键名（如某 provider 的密钥引用名）

任何一种方式都可以；key 永远不写日志、不落盘。
"""

import os
import re


class MissingCredentialsError(RuntimeError):
    pass


def load_api_key():
    key = os.environ.get("HARNESSMM_API_KEY")
    if key:
        return key

    cred_file = os.environ.get("HARNESSMM_CREDENTIALS_FILE")
    cred_ref = os.environ.get("HARNESSMM_CREDENTIALS_REF")
    if cred_file and cred_ref and os.path.isfile(os.path.expanduser(cred_file)):
        text = open(os.path.expanduser(cred_file), encoding="utf-8").read()
        marker = '"%s":' % cred_ref
        idx = text.find(marker)
        if idx >= 0:
            rest = text[idx + len(marker):]
            start = rest.find('"')
            if start >= 0:
                end = -1
                for i in range(start + 1, len(rest)):
                    c = rest[i]
                    if c == "\\":
                        continue
                    if c == '"':
                        end = i
                        break
                if end > start:
                    return rest[start + 1:end]

    raise MissingCredentialsError(
        "no API credentials found. Set HARNESSMM_API_KEY, or set both "
        "HARNESSMM_CREDENTIALS_FILE and HARNESSMM_CREDENTIALS_REF. "
        "Keys are read at runtime and never logged.")


def scrub(text, key):
    """脱敏：防止 key 意外进入任何落盘内容。"""
    if not isinstance(text, str) or not key:
        return text
    return text.replace(key, "[REDACTED-KEY]")
