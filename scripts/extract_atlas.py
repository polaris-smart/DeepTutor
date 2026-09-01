#!/usr/bin/env python3
"""M1 教材图集抽取器：parse_cache → atlas.json（离线只读，零 LLM）。

读 MinerU 解析产物（content_list.json + images/），按质量闸选出可教学使用的
图，产出与 ``yuedu_image_atlas`` 包 payload 条目同构的图集清单。

质量闸（滤装饰图/碎片图/重复图）：
  * 短边 >= --min-px（默认 120）
  * 宽高比 <= --max-ratio（默认 6，滤分隔线/横幅）
  * 文件内容 md5 去重（同一图多页引用只留一条）
  * 标题图（image_caption 含"图 N-"语义不强求）；caption 取 image_caption/
    image_footnote 首条，缺省留空由渲染层占位

用法：
  python extract_atlas.py <parse_cache_dir> -o atlas.json \
      [--min-px 120] [--max-ratio 6] [--limit 500]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def find_content_list(root: Path) -> Path | None:
    candidates = sorted(root.glob("*_content_list.json"))
    if not candidates:
        candidates = sorted(root.glob("**/*_content_list.json"))
    return candidates[0] if candidates else None


def image_size(path: Path):
    """Return (w, h) via Pillow when available, else None (size gate skips)."""
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cache_dir", help="parse_cache 册目录（含 content_list.json 与 images/）")
    ap.add_argument("-o", "--out", default="atlas.json")
    ap.add_argument("--min-px", type=int, default=120)
    ap.add_argument("--max-ratio", type=float, default=6.0)
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    root = Path(args.cache_dir)
    cl = find_content_list(root)
    if cl is None:
        print("ERROR: no *_content_list.json under", root, file=sys.stderr)
        return 1
    book = root.name
    blocks = json.loads(cl.read_text(encoding="utf-8"))

    items: list[dict] = []
    seen_hashes: set[str] = set()
    scanned = kept = dropped_small = dropped_ratio = dropped_dup = 0

    for idx, b in enumerate(blocks):
        if b.get("type") != "image":
            continue
        img_rel = str(b.get("img_path") or "")
        img = root / img_rel
        if not img.is_file():
            continue
        scanned += 1
        digest = hashlib.md5(img.read_bytes()).hexdigest()
        if digest in seen_hashes:
            dropped_dup += 1
            continue

        size = image_size(img)
        if size is not None:
            w, h = size
            if min(w, h) < args.min_px:
                dropped_small += 1
                continue
            ratio = max(w, h) / max(1, min(w, h))
            if ratio > args.max_ratio:
                dropped_ratio += 1
                continue

        seen_hashes.add(digest)
        captions = [str(c).strip() for c in (b.get("image_caption") or []) if str(c).strip()]
        footnote = [str(c).strip() for c in (b.get("image_footnote") or []) if str(c).strip()]
        caption = (captions or footnote or [""])[0]
        kept += 1
        if kept > args.limit:
            break
        item = {
            "id": f"fig_{digest[:12]}",
            "file": img_rel,
            "label": caption[:60] if caption else f"图 {kept}",
            "caption": caption[:200],
            "page": int(b.get("page_idx") or 0) + 1,
            "md5": digest,
        }
        if b.get("bbox"):
            item["bbox"] = b["bbox"]
        if size is not None:
            item["w"], item["h"] = size
        items.append(item)

    out = {
        "book": book,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(cl.name),
        "stats": {
            "blocks": len(blocks),
            "image_blocks": scanned,
            "kept": len(items),
            "dropped_small": dropped_small,
            "dropped_ratio": dropped_ratio,
            "dropped_dup": dropped_dup,
        },
        "items": items,
    }
    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"{book}: 扫描图块 {scanned} → 保留 {len(items)} "
        f"(小图 {dropped_small} / 长条 {dropped_ratio} / 重复 {dropped_dup})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
