from __future__ import annotations

import json
import os
from pathlib import Path

from deeptutor.knowledge import video_library


def _make_library(root: Path) -> Path:
    collection = root / "一数-1291233-高考最后十课"
    collection.mkdir(parents=True)
    (collection / "01. 三角函数【1080P】.mp4").touch()
    (collection / "02_正弦定理-高清.mp4").touch()
    nested = root / "物理老师" / "力学精讲"
    nested.mkdir(parents=True)
    (nested / "003-牛顿定律（4K）.mp4").touch()
    douyin = root / "douyin-math" / "函数最值" / "视频" / "一招求最值"
    douyin.mkdir(parents=True)
    (douyin / "%03d-函数最值.mp4").touch()
    return root


def test_scan_video_library_extracts_directory_and_rule_metadata(tmp_path: Path, monkeypatch) -> None:
    root = _make_library(tmp_path / "videos")
    monkeypatch.setattr(video_library, "probe_duration", lambda _path: 124)

    videos = video_library.scan_video_library(root)

    by_path = {video.path: video for video in videos}
    trig = by_path["一数-1291233-高考最后十课/01. 三角函数【1080P】.mp4"]
    assert trig.title == "三角函数"
    assert trig.channel == "一数"
    assert trig.collection == "高考最后十课"
    assert trig.duration_s == 124
    assert trig.subject_hint == "数学"
    assert trig.kp_hint == "三角函数"

    mechanics = by_path["物理老师/力学精讲/003-牛顿定律（4K）.mp4"]
    assert mechanics.channel == "物理老师"
    assert mechanics.collection == "力学精讲"
    assert mechanics.subject_hint == "物理"
    assert "牛顿定律" in mechanics.kp_hint

    douyin = by_path["douyin-math/函数最值/视频/一招求最值/%03d-函数最值.mp4"]
    assert douyin.title == "函数最值"
    assert douyin.channel == "douyin-math"
    assert douyin.collection == "函数最值"


def test_index_video_library_reuses_unchanged_entries_and_only_writes_index(
    tmp_path: Path, monkeypatch
) -> None:
    root = _make_library(tmp_path / "videos")
    kb_base_dir = tmp_path / "knowledge_bases"
    calls: list[Path] = []
    monkeypatch.setattr(video_library, "probe_duration", lambda path: calls.append(path) or 60)

    first = video_library.index_video_library(root, kb_name="视频库", kb_base_dir=kb_base_dir)
    source_mtime = (root / "一数-1291233-高考最后十课/01. 三角函数【1080P】.mp4").stat().st_mtime_ns
    second = video_library.index_video_library(root, kb_name="视频库", kb_base_dir=kb_base_dir)

    assert (first.scanned, first.added, first.skipped) == (4, 4, 0)
    assert (second.scanned, second.added, second.skipped) == (4, 0, 4)
    assert len(calls) == 4
    assert (root / "一数-1291233-高考最后十课/01. 三角函数【1080P】.mp4").stat().st_mtime_ns == source_mtime
    payload = json.loads(first.index_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert len(payload["videos"]) == 4

    changed = root / "一数-1291233-高考最后十课/02_正弦定理-高清.mp4"
    os.utime(changed, ns=(changed.stat().st_atime_ns, changed.stat().st_mtime_ns + 1))
    third = video_library.index_video_library(root, kb_name="视频库", kb_base_dir=kb_base_dir)
    assert (third.added, third.skipped) == (1, 3)
    assert len(calls) == 5


def test_search_videos_requires_two_struct_path_keywords() -> None:
    videos = [
        video_library.VideoMeta(
            path="a.mp4",
            title="三角函数与正弦定理",
            channel="一数",
            collection="高考最后十课",
            duration_s=120,
            subject_hint="数学",
            kp_hint="三角函数、正弦定理",
        ),
        video_library.VideoMeta(
            path="b.mp4",
            title="三角函数入门",
            channel="一数",
            collection="基础课",
            duration_s=None,
            subject_hint="数学",
            kp_hint="三角函数",
        ),
    ]

    assert [item["path"] for item in video_library.search_videos(videos, query="三角函数")] == [
        "a.mp4",
        "b.mp4",
    ]
    assert [
        item["path"]
        for item in video_library.search_videos(
            videos, struct_path="必修二/三角函数/正弦定理"
        )
    ] == ["a.mp4"]


def test_clean_video_title_removes_download_order_date_quality_and_source_id() -> None:
    assert (
        video_library.clean_video_title("2026-03-30_03-三角函数【1080P】_7657110472260455731.mp4")
        == "三角函数"
    )


def test_probe_duration_degrades_when_ffprobe_is_not_installed(
    tmp_path: Path, monkeypatch
) -> None:
    def _missing_ffprobe(*_args, **_kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(video_library.subprocess, "run", _missing_ffprobe)
    assert video_library.probe_duration(tmp_path / "video.mp4") is None
