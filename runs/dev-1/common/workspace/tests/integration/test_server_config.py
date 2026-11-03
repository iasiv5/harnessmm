"""集成测试：通过 load_server_config 加载真实配置文件。

server.conf 使用十六进制端口 0x1F90，修复前该文件无法被解析。
"""

import os

import pytest

from config_parser import load_server_config

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_load_demo_config():
    conf = load_server_config(os.path.join(BASE, "server.conf"))
    assert conf["port"] == 8080
    assert conf["host"] == "127.0.0.1"
    assert conf["mode"] == "demo"


def test_missing_config_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_server_config(str(tmp_path / "nope.conf"))


def test_invalid_port_line_rejected(tmp_path):
    bad = tmp_path / "bad.conf"
    bad.write_text("port = 65536\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_server_config(str(bad))


def test_hex_port_line_accepted(tmp_path):
    good = tmp_path / "good.conf"
    good.write_text("# comment\n\nport = 0x0050\n", encoding="utf-8")
    conf = load_server_config(str(good))
    assert conf["port"] == 80
