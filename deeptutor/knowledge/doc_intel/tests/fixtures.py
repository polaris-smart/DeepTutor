"""Minimal synthetic block fixtures shaped like real MinerU content_lists.

Real-sample regressions (full volumes) run separately; these fixtures keep
the unit suite fast and dependency-free.
"""

V1_TEACHER_EDITION = [
    {"type": "text", "text": "基础题小题练习 (8)", "text_level": 1, "page_idx": 0, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "一、选择题：本大题共8小题，每小题5分", "text_level": 2, "page_idx": 0, "bbox": [0, 1, 10, 11]},
    {"type": "text", "text": "1. 设集合 A={0,-a}，B={1,a-2}，若 A⊆B，则 a =（  ）", "page_idx": 0, "bbox": [0, 2, 10, 12]},
    {"type": "image", "img_path": "images/aaa.jpg", "page_idx": 0, "bbox": [0, 3, 10, 13]},
    {"type": "text", "text": "【答案】B", "page_idx": 0, "bbox": [0, 4, 10, 14]},
    {"type": "text", "text": "【详解】因为 A⊆B，则有…", "page_idx": 0, "bbox": [0, 5, 10, 15]},
    {"type": "text", "text": "2. 若 z/(z-1)=1+i，则 z=（  ）", "page_idx": 1, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "【答案】C 【详解】因为…", "page_idx": 1, "bbox": [0, 1, 10, 11]},
]

V1_TEXTBOOK_TOC = [
    {"type": "text", "text": "普通高中教科书", "text_level": 1, "page_idx": 0, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "目录", "text_level": 2, "page_idx": 1, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "第1章 集合 …… 1", "text_level": 2, "page_idx": 1, "bbox": [0, 1, 10, 11]},
    {"type": "text", "text": "1.1 集合的概念与表示 …… 5", "text_level": 2, "page_idx": 1, "bbox": [0, 2, 10, 12]},
    {"type": "text", "text": "第2章 常用逻辑用语 …… 20", "text_level": 2, "page_idx": 1, "bbox": [0, 3, 10, 13]},
    {"type": "page_number", "text": "2", "page_idx": 1, "bbox": [0, 9, 10, 19]},
    {"type": "text", "text": "第1章 集合", "text_level": 2, "page_idx": 2, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "集合是一种数学语言", "page_idx": 2, "bbox": [0, 1, 10, 11]},
    {"type": "text", "text": "1.1", "text_level": 2, "page_idx": 3, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "集合的概念与表示", "text_level": 2, "page_idx": 3, "bbox": [0, 1, 10, 11]},
    {"type": "text", "text": "练习", "text_level": 2, "page_idx": 5, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "1. 用∈或∉填空：", "page_idx": 5, "bbox": [0, 1, 10, 11]},
    {"type": "header", "text": "第1章 集合", "page_idx": 6, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "第2章", "text_level": 2, "page_idx": 8, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "第 2 章 常用逻辑用语", "text_level": 2, "page_idx": 8, "bbox": [0, 0, 10, 10]},
    {"type": "text", "text": "命题、定理、定义", "text_level": 2, "page_idx": 9, "bbox": [0, 0, 10, 10]},
]
