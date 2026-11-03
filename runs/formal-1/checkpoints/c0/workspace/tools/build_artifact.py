#!/usr/bin/env python3
"""构建交付产物 dist/package.zip。

产物包含：config_parser.py、server.conf、manifest.json。
manifest.json 记录每个打包文件的 sha256 与构建元数据，供 verify_artifact.py
做实质校验（哈希比对 + 产物内解析器行为抽查），不是只检查文件存在。
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_FILES = ["config_parser.py", "server.conf"]


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist/package.zip")
    args = ap.parse_args()

    out_path = os.path.join(BASE, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    manifest = {
        "artifact": "harnessmm-teaching-package",
        "task_id": "config-port-hex",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
    }

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in PACKAGE_FILES:
            src = os.path.join(BASE, rel)
            if not os.path.isfile(src):
                print("BUILD FAIL: missing file %s" % rel, file=sys.stderr)
                return 2
            with open(src, "rb") as fh:
                data = fh.read()
            zf.writestr(rel, data)
            manifest["files"][rel] = {
                "sha256": sha256_bytes(data),
                "bytes": len(data),
            }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    size = os.path.getsize(out_path)
    print("BUILD OK: %s (%d bytes, %d files + manifest)"
          % (out_path, size, len(PACKAGE_FILES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
