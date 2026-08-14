"""Unit tests for student-facing RAG result enrichment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from deeptutor.services.rag.result_enricher import enrich_search_results


@dataclass
class StubNode:
    node_id: str
    text: str
    metadata: dict[str, Any] | None = None
    image_path: str = ""

    def get_content(self) -> str:
        return self.text


def test_enriches_locations_questions_and_images_with_deduplication_and_limits() -> None:
    nodes = [
        StubNode(
            "ordinary",
            "普通教材正文",
            {
                "file_name": "政治必修一.pdf",
                "struct_path": "第一单元/第一课",
                "linked_images": ["images/figure-1.png", "images/figure-1.png"],
            },
        ),
        StubNode(
            "question-1",
            "1. 什么是中国特色社会主义？",
            {
                "file_name": "政治必修一.pdf",
                "struct_path": "第一单元/第一课",
                "is_question": True,
                "q_id": "P1-1",
                "q_type": "选择题",
                "has_answer": True,
            },
        ),
        StubNode(
            "question-1-duplicate",
            "1. 什么是中国特色社会主义？（重复块）",
            {
                "file_name": "政治必修一.pdf",
                "struct_path": "第一单元/第一课",
                "is_question": True,
                "q_id": "P1-1",
            },
        ),
        StubNode(
            "image-1",
            "教材配图",
            {
                "file_name": "政治必修一.pdf",
                "struct_path": "第一单元/第一课",
                "image_of": "question",
            },
            image_path="images/figure-1.png",
        ),
    ]
    nodes.extend(
        StubNode(
            f"question-{index}",
            f"第 {index} 道题",
            {
                "file_name": "政治必修一.pdf",
                "struct_path": "第一单元/第一课",
                "is_question": True,
                "q_id": f"P1-{index}",
            },
        )
        for index in range(2, 8)
    )
    nodes.extend(
        StubNode(
            f"image-{index}",
            f"配图 {index}",
            {
                "file_name": "政治必修一.pdf",
                "struct_path": "第一单元/第一课",
                "image_of": "body",
            },
            image_path=f"images/figure-{index}.png",
        )
        for index in range(2, 8)
    )

    enriched = enrich_search_results(nodes, kb_name="政治")

    assert len(enriched["locations"]) == len(nodes)
    assert enriched["locations"][0] == {
        "node_id": "ordinary",
        "file_name": "政治必修一.pdf",
        "struct_path": "第一单元/第一课",
        "preview": "普通教材正文",
    }
    assert [question["q_id"] for question in enriched["related_questions"]] == [
        "P1-1",
        "P1-2",
        "P1-3",
        "P1-4",
        "P1-5",
    ]
    assert enriched["related_questions"][0]["has_answer"] is True
    assert [image["image_path"] for image in enriched["related_images"]] == [
        "images/figure-1.png",
        "images/figure-2.png",
        "images/figure-3.png",
        "images/figure-4.png",
        "images/figure-5.png",
    ]
    assert enriched["related_images"][0]["image_of"] == ""


def test_metadata_free_nodes_degrade_to_empty_enrichment() -> None:
    nodes = [
        StubNode("ordinary", "普通正文"),
        StubNode("question", "题目正文", None),
        StubNode("image", "图片正文", {}, image_path="images/old-index.png"),
    ]

    assert enrich_search_results(nodes, kb_name="旧知识库") == {
        "locations": [],
        "related_questions": [],
        "related_images": [],
    }
