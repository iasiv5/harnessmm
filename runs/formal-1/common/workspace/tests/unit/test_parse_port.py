"""parse_port 单元测试：十进制与 0x 十六进制双格式 + 非法输入校验。

这些向量同时锁定了既有非法输入校验规则；修复任务不得破坏它们。
"""

import pytest

from config_parser import parse_port


VALID = [
    ("8080", 8080),
    ("0", 0),
    ("65535", 65535),
    ("  8080  ", 8080),
    ("0x1F90", 8080),
    ("0x1f90", 8080),
    ("0X1F90", 8080),
    ("  0x1F90  ", 8080),
    ("0x0", 0),
    ("0xFFFF", 65535),
]

INVALID = [
    "",
    "   ",
    "-1",
    "+80",
    "65536",
    "99999999",
    "0x",
    "0X",
    "0xG",
    "0x1F90G",
    "abc",
    "8080abc",
    "12.5",
    "8 080",
    "0x-1",
    "8080\n8081",
]


@pytest.mark.parametrize("raw,expected", VALID)
def test_valid_ports(raw, expected):
    assert parse_port(raw) == expected


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_ports(raw):
    with pytest.raises(ValueError):
        parse_port(raw)


def test_boundary_values():
    assert parse_port("65535") == 65535
    assert parse_port("0xFFFF") == 65535
    with pytest.raises(ValueError):
        parse_port("65536")
    with pytest.raises(ValueError):
        parse_port("0x10000")
