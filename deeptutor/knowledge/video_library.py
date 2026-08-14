"""Local, read-only video-library metadata indexing.

The scanner only reads file names, directory names, timestamps, and (when
available) ``ffprobe`` metadata.  It never copies, moves, transcodes, or
otherwise mutates a source video.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any, Iterable, Sequence

from deeptutor.knowledge.doc_intel.classifier import _SUBJECT_PATTERNS
from deeptutor.knowledge.naming import validate_knowledge_base_name
from deeptutor.runtime.home import get_runtime_data_root
from deeptutor.services.file_io import atomic_write_json

DEFAULT_KB_NAME = "视频库"
VIDEO_INDEX_FILENAME = "video_index.json"
VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm"})

# These are deliberately plain string rules, not an NLP classifier.  The
# longer phrases come first so a title such as ``三角函数`` does not produce the
# less useful nested hint ``函数`` as well.
_KP_HINTS: tuple[str, ...] = (
    "圆锥曲线",
    "三角函数",
    "解三角形",
    "正弦定理",
    "余弦定理",
    "排列组合",
    "二项式定理",
    "立体几何",
    "空间向量",
    "概率统计",
    "数列",
    "导数",
    "不等式",
    "复数",
    "集合",
    "函数",
    "方程",
    "牛顿定律",
    "电磁感应",
    "电路",
    "力学",
    "化学反应",
    "有机化学",
    "元素周期律",
    "细胞",
    "遗传",
    "生态",
    "文言文",
    "古诗文",
    "阅读理解",
    "完形填空",
)
_KP_SEPARATORS = re.compile(r"[、,，;/；|]+")
_LEADING_SEQUENCE = re.compile(
    r"^\s*(?:[【\[]?\d{1,4}[】\]]?|%\d*d)(?:[\s._-]+|$)"
)
_LEADING_DATE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}[_\s-]*")
_TRAILING_SOURCE_ID = re.compile(r"[_\s-]\d{15,}$")
_QUALITY_MARKER = re.compile(
    r"\s*(?:[【\[(（]\s*)?(?:\d{3,4}p|\d{3,4}P|4k|4K|高清|超清|蓝光|BD|WEB[- ]?DL|"
    r"HEVC|H\.?(?:264|265)|x26[45])(?:\s*[】\])）])?\s*",
    re.IGNORECASE,
)
_CHANNEL_COLLECTION = re.compile(r"^(?P<channel>[^-]+)-\d+-+(?P<collection>.+)$")
_COLLECTION_PREFIX = re.compile(r"^(?:\d+-+|[^-]+-\d+-+)")


@dataclass(frozen=True)
class VideoMeta:
    """Metadata that is safe to persist and return from the video API."""

    path: str
    title: str
    channel: str
    collection: str
    duration_s: int | None
    subject_hint: str
    kp_hint: str


@dataclass(frozen=True)
class VideoIndexResult:
    """Outcome of an incremental index refresh."""

    videos: list[VideoMeta]
    scanned: int
    added: int
    skipped: int
    index_path: Path


def clean_video_title(filename: str) -> str:
    """Return a display title with common ordering and quality noise removed."""
    title = Path(filename).stem
    title = _LEADING_DATE.sub("", title)
    title = _LEADING_SEQUENCE.sub("", title)
    title = _QUALITY_MARKER.sub(" ", title)
    title = _TRAILING_SOURCE_ID.sub("", title)
    title = re.sub(r"\s+", " ", title).strip(" ._-")
    return title or Path(filename).stem


def infer_channel_and_collection(video_path: Path, root: Path) -> tuple[str, str]:
    """Infer a channel and collection from the video directory hierarchy."""
    relative_parts = video_path.relative_to(root).parts
    # The downloaded Douyin layout is ``douyin-math/<topic>/(视频/)?<item>/<file>``.
    # The item directory is a post title, not the channel or collection.
    if len(relative_parts) >= 3 and relative_parts[0].lower().startswith("douyin"):
        return relative_parts[0], relative_parts[1]

    parent = video_path.parent
    parent_name = parent.name
    packed = _CHANNEL_COLLECTION.match(parent_name)
    if packed:
        return packed.group("channel").strip(), packed.group("collection").strip()

    channel = parent.parent.name if parent.parent != root else parent_name
    collection = _COLLECTION_PREFIX.sub("", parent_name).strip(" -_")
    if not collection or collection == channel:
        collection = parent_name
    return channel.strip(), collection


def infer_subject_hint(*parts: str) -> str:
    """Reuse document-intelligence's ordered, rule-based subject lexicon."""
    corpus = " ".join(part for part in parts if part)
    for subject, pattern in _SUBJECT_PATTERNS:
        if pattern.search(corpus):
            return subject
    return ""


def _matched_kp_terms(text: str) -> list[str]:
    """Find non-overlapping configured knowledge-point strings in ``text``."""
    matches: list[str] = []
    for hint in _KP_HINTS:
        if hint not in text:
            continue
        if any(hint in selected or selected in hint for selected in matches):
            continue
        matches.append(hint)
    return matches


def infer_kp_hint(*parts: str) -> str:
    """Return comma-separated knowledge-point clues using only string rules."""
    return "、".join(_matched_kp_terms(" ".join(part for part in parts if part)))


def probe_duration(video_path: Path) -> int | None:
    """Read duration with ffprobe, returning ``None`` when it is unavailable."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return None
    return round(duration) if duration >= 0 else None


def _iter_video_paths(root: Path) -> Iterable[Path]:
    for candidate in root.rglob("*"):
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if candidate.suffix.lower() in VIDEO_EXTENSIONS:
            yield candidate


def _read_video_meta(video_path: Path, root: Path) -> VideoMeta:
    channel, collection = infer_channel_and_collection(video_path, root)
    title = clean_video_title(video_path.name)
    context = (title, channel, collection, str(video_path.parent))
    return VideoMeta(
        path=video_path.relative_to(root).as_posix(),
        title=title,
        channel=channel,
        collection=collection,
        duration_s=probe_duration(video_path),
        subject_hint=infer_subject_hint(*context),
        kp_hint=infer_kp_hint(*context),
    )


def scan_video_library(root: Path) -> list[VideoMeta]:
    """Traverse ``root`` and extract metadata without changing source videos."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Video library root is not a directory: {root}")
    return [_read_video_meta(path, root) for path in sorted(_iter_video_paths(root))]


def video_index_path(kb_name: str = DEFAULT_KB_NAME, *, kb_base_dir: Path | None = None) -> Path:
    """Return the persistent index path for a knowledge base."""
    base_dir = (
        Path(kb_base_dir)
        if kb_base_dir is not None
        else get_runtime_data_root() / "knowledge_bases"
    )
    return base_dir / validate_knowledge_base_name(kb_name) / VIDEO_INDEX_FILENAME


def _coerce_video_meta(payload: object) -> VideoMeta | None:
    if not isinstance(payload, dict):
        return None
    try:
        duration = payload.get("duration_s")
        return VideoMeta(
            path=str(payload["path"]),
            title=str(payload.get("title") or ""),
            channel=str(payload.get("channel") or ""),
            collection=str(payload.get("collection") or ""),
            duration_s=round(float(duration)) if duration is not None else None,
            subject_hint=str(payload.get("subject_hint") or ""),
            kp_hint=str(payload.get("kp_hint") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def load_video_index(index_path: Path) -> list[VideoMeta]:
    """Read a video index; a missing or malformed index behaves as empty."""
    try:
        payload = json.loads(Path(index_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("videos", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    return [meta for row in rows if (meta := _coerce_video_meta(row)) is not None]


def _load_cached_entries(index_path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    rows = payload.get("videos", []) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return {}
    return {
        str(row.get("path")): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("path"), str)
    }


def index_video_library(
    root: Path,
    *,
    kb_name: str = DEFAULT_KB_NAME,
    kb_base_dir: Path | None = None,
) -> VideoIndexResult:
    """Refresh the JSON index, reusing entries whose relative path and mtime match."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Video library root is not a directory: {root}")

    index_path = video_index_path(kb_name, kb_base_dir=kb_base_dir)
    cached = _load_cached_entries(index_path)
    videos: list[VideoMeta] = []
    scanned = added = skipped = 0

    for video_path in sorted(_iter_video_paths(root)):
        scanned += 1
        relative_path = video_path.relative_to(root).as_posix()
        cached_entry = cached.get(relative_path)
        mtime_ns = video_path.stat().st_mtime_ns
        cached_meta = _coerce_video_meta(cached_entry)
        if cached_meta is not None and cached_entry.get("mtime_ns") == mtime_ns:
            videos.append(cached_meta)
            skipped += 1
            continue

        videos.append(_read_video_meta(video_path, root))
        added += 1

    rows = [
        {**asdict(video), "mtime_ns": (root / video.path).stat().st_mtime_ns}
        for video in videos
    ]
    atomic_write_json(
        index_path,
        {
            "version": 1,
            "root": str(root),
            "videos": rows,
        },
    )
    return VideoIndexResult(videos, scanned, added, skipped, index_path)


def _keyword_set(text: str) -> set[str]:
    """Turn persisted hints or a textbook structure path into rule keywords."""
    tokens = {token.strip() for token in _KP_SEPARATORS.split(text) if token.strip()}
    tokens.update(_matched_kp_terms(text))
    return tokens


def _query_score(video: VideoMeta, query: str) -> int:
    normalized = "".join(query.lower().split())
    if not normalized:
        return 0
    title = "".join(video.title.lower().split())
    kp_hint = "".join(video.kp_hint.lower().split())
    score = 0
    if normalized in title:
        score += 2
    if normalized in kp_hint:
        score += 1
    return score


def search_videos(
    videos: Sequence[VideoMeta],
    *,
    query: str = "",
    struct_path: str = "",
    limit: int = 20,
) -> list[dict[str, object]]:
    """Filter and rank index rows using title/KP matches and path keyword overlap."""
    results: list[dict[str, object]] = []
    struct_keywords = _keyword_set(struct_path) if struct_path.strip() else set()
    for video in videos:
        query_score = _query_score(video, query)
        if query.strip() and not query_score:
            continue
        struct_score = len(_keyword_set(video.kp_hint) & struct_keywords)
        if struct_keywords and struct_score < 2:
            continue
        results.append(
            {
                "path": video.path,
                "title": video.title,
                "channel": video.channel,
                "collection": video.collection,
                "duration_s": video.duration_s,
                "score": query_score + struct_score,
            }
        )
    return sorted(results, key=lambda row: (-int(row["score"]), str(row["title"])))[:limit]


def main(argv: Sequence[str] | None = None) -> int:
    """Run the one-shot local scanner used by containers and import jobs."""
    parser = argparse.ArgumentParser(
        description="Index a local video library without changing videos"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan_parser = subparsers.add_parser(
        "scan", help="scan a library and refresh its metadata index"
    )
    scan_parser.add_argument("root", type=Path, help="video-library directory to scan")
    scan_parser.add_argument(
        "--kb", default=DEFAULT_KB_NAME, help=f"knowledge base name (default: {DEFAULT_KB_NAME})"
    )
    args = parser.parse_args(argv)

    if args.command == "scan":
        try:
            result = index_video_library(args.root, kb_name=args.kb)
        except ValueError as exc:
            parser.error(str(exc))
        print(
            f"扫描到 {result.scanned} 个视频；新增/更新 {result.added} 个；"
            f"跳过 {result.skipped} 个；索引：{result.index_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
