"""Fetch the compact Chinese/English OCR model used by MaaFramework."""

from __future__ import annotations

import urllib.request
import hashlib
from pathlib import Path

BASE = "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/OCR/ppocr_v4/zh_cn"
DEST = Path(__file__).resolve().parent.parent / "resources" / "model" / "ocr"
SHA256 = {
    "det.onnx": "767e663dded50813dca0abc9a49859e345e0f74a48b387661188f096a9211d74",
    "rec.onnx": "da4815ea386ee8083be319da8d20e70f4db0b6404ba0d2da7f675831e1b37fa",
    "keys.txt": "28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7",
}


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for name, digest in SHA256.items():
        path = DEST / name
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            print(f"skip {name}")
            continue
        print(f"download {name}")
        request = urllib.request.Request(f"{BASE}/{name}", headers={"User-Agent": "AutoFlow/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(f"model checksum mismatch: {name}")
        path.write_bytes(data)
        print(f"  {len(data):,} bytes")


if __name__ == "__main__":
    main()
