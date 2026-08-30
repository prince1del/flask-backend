"""One-off: compare Mother SO.zip vs split so.rar for Balaji AW26."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.so_pack_consolidate import analyze_so_pack

MOTHER = Path(
    r"G:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\SO AW 26\Balaji home decor\Mother SO.zip"
)
SPLIT = Path(
    r"G:\My Drive\2026-2027\Oder Management\AW26 order\Bedsheet\SO AW 26\Balaji home decor\split so.rar"
)


def extract_rar(rar_path: Path, dest: Path) -> list[Path]:
    for cmd in (
        ["7z", "x", "-y", f"-o{dest}", str(rar_path)],
        ["unrar", "x", "-o+", "-y", str(rar_path), str(dest) + "\\"],
    ):
        exe = cmd[0]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return sorted(dest.rglob("*.pdf"))
    raise RuntimeError("No 7z/unrar found to extract split so.rar")


def main() -> None:
    mother = analyze_so_pack(MOTHER.read_bytes(), MOTHER.name)
    dest = Path(tempfile.mkdtemp(prefix="balaji-split-"))
    pdfs = extract_rar(SPLIT, dest)
    print("split pdfs:", [p.name for p in pdfs])
    # analyze merged as if uploaded together
    split_data = analyze_so_pack(SPLIT.read_bytes(), SPLIT.name)
    for so in sorted({str(l.get("so_number")) for l in split_data.get("line_detail") or []}):
        mlines = [l for l in (mother.get("line_detail") or []) if str(l.get("so_number")) == so]
        slines = [l for l in (split_data.get("line_detail") or []) if str(l.get("so_number")) == so]
        if not slines:
            continue
        mq = sum(float(l.get("qty") or 0) for l in mlines)
        sq = sum(float(l.get("qty") or 0) for l in slines)
        mn = sum(float(l.get("net_amount") or 0) for l in mlines)
        sn = sum(float(l.get("net_amount") or 0) for l in slines)
        print(f"SO {so}: mother qty={mq} net={mn:.0f} | split qty={sq} net={sn:.0f} | delta qty={sq-mq}")


if __name__ == "__main__":
    main()
