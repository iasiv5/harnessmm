#!/usr/bin/env python3
"""校验交付产物 dist/package.zip（实质校验，不是只检查文件存在）。

校验内容：
1. zip 可打开且包含 manifest.json 与声明的全部文件；
2. manifest 中每个文件的 sha256 与 zip 内实际内容一致（防内容损坏/掉包）；
3. 从产物内提取 config_parser.py 到临时目录并真实导入，
   对样例向量执行行为抽查（十进制、十六进制、非法输入）。

全部通过打印 "ARTIFACT VERIFY: PASS" 并退出 0；任何一步失败退出非零。
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import zipfile

SAMPLES = [
    ("8080", 8080),
    ("0x1F90", 8080),
    ("0X1f90", 8080),
    ("65535", 65535),
    ("0xFFFF", 65535),
    ("0", 0),
]
INVALID_SAMPLES = ["-1", "65536", "0x", "abc", "", "  "]


def fail(msg):
    print("ARTIFACT VERIFY: FAIL — %s" % msg, file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="dist/package.zip")
    args = ap.parse_args()

    path = args.file
    if not os.path.isfile(path):
        return fail("artifact not found: %s" % path)

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        return fail("bad zip: %s" % exc)

    names = set(zf.namelist())
    if "manifest.json" not in names:
        return fail("manifest.json missing from artifact")

    try:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except Exception as exc:
        return fail("manifest unreadable: %s" % exc)

    declared = manifest.get("files", {})
    if not declared:
        return fail("manifest declares no files")

    for rel, meta in declared.items():
        if rel not in names:
            return fail("declared file missing from zip: %s" % rel)
        actual = hashlib.sha256(zf.read(rel)).hexdigest()
        if actual != meta.get("sha256"):
            return fail("sha256 mismatch for %s (manifest=%s actual=%s)"
                        % (rel, meta.get("sha256"), actual))
        if zf.getinfo(rel).file_size != meta.get("bytes"):
            return fail("size mismatch for %s" % rel)

    tmpdir = tempfile.mkdtemp(prefix="hmm_verify_")
    try:
        zf.extractall(tmpdir)
        parser_path = os.path.join(tmpdir, "config_parser.py")
        if not os.path.isfile(parser_path):
            return fail("config_parser.py missing after extract")
        spec = importlib.util.spec_from_file_location(
            "artifact_config_parser", parser_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        for raw, expected in SAMPLES:
            try:
                got = mod.parse_port(raw)
            except Exception as exc:
                return fail("parse_port(%r) raised %r, expected %r"
                            % (raw, exc, expected))
            if got != expected:
                return fail("parse_port(%r) = %r, expected %r"
                            % (raw, got, expected))

        for raw in INVALID_SAMPLES:
            try:
                got = mod.parse_port(raw)
            except ValueError:
                continue
            except Exception as exc:
                return fail("parse_port(%r) raised non-ValueError %r" % (raw, exc))
            return fail("parse_port(%r) = %r but must be rejected" % (raw, got))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("ARTIFACT VERIFY: PASS (%d files hash-checked, %d+ behaviour samples OK)"
          % (len(declared), len(SAMPLES) + len(INVALID_SAMPLES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
