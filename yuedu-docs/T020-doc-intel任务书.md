# T020 任务书：doc_intel —— DT 体内全自动文档智能层

> 派单：ZC 自排（老板 2026-08-14 授权："原来的 yuedu 冻结不碰，就在 DT 的 docker 里尽情发挥；现有数据都是测试数据，全部搞定后老板重新导入"）
> 仓库：`~/tools/DeepTutor-yuedu/`（fork，基点 v1.5.11）
> 铁律：**零手动处理、零 DT 外脚本**——一切在 DT 摄入管道内自动发生

## 一、目标（一句话）

用户拖文档进知识库 → DT 自动完成：分类（学科/类型/年级）→ 教材结构树（单元→课→节）→ 题答分离（题干/答案/解析独立标记）→ 图片归属 → 全部写入 node metadata，供检索过滤、树导航、资源带出消费。

## 二、架构设计

### 挂点（最小侵入，官方升级零冲突）

```
官方已有流程：
  上传 → ParseService.parse() → parse_cache/<hash>/<sig>/（md + content_list.json + images/）
       → document_loader 建 nodes → 索引

新增（全部在新目录 deeptutor/knowledge/doc_intel/）：
  ① enrich 挂点：document_loader 建 node 前调 doc_intel.enrich(blocks, md, filename)
     —— 纯函数，输入解析产物，输出 metadata 注射；失败静默降级（不阻塞索引）
  ② LLM 分类：首次入库时后台任务（复用官方 BackgroundTasks/task_manager）
     采样首页+目录页 → doubao-2.0-lite → subject/doc_type/grade → 存 doc 级 manifest
  ③ CLI 触发（补充）：python -m deeptutor.knowledge.doc_intel scan <kb>（重导后批量跑）
```

### 模块拆分（新目录 `deeptutor/knowledge/doc_intel/`）

| 文件 | 职责 | 实现要点 |
|---|---|---|
| `__init__.py` | 对外只暴露 `enrich()` + `classify_document_async()` | 单一入口 |
| `classifier.py` | ①学科②类型③年级 | 规则先行（文件名词典+书名页特征"必修/选择性必修/第X单元"），不确定才 LLM 采样；结果缓存 doc 级 |
| `structure.py` | 教材结构树 | content_list 的 `text_level`（1=章/2=节）+ 标题正则（第X单元/第X课/XX节）→ 三级树；每 node 注 `struct_path:"数学教辅/必修一/第1章/1.1"` |
| `qa_split.py` | 题答分离 | 题号模式（`^\d+[.、)]`/`(1)`变体）+ 题型标记（选择/填空/解答）+ 答案区识别（"参考答案/答案速查"标题后）+ 跨区回填（answer_matcher 方法论移植）；node 注 `is_question/has_answer/answer_ref/q_type` |
| `image_link.py` | 图片归属 | content_list bbox 就近原则 → 挂最近题干/节；node 注 `linked_images:[...]` |
| `prompts.py` | 分类/结构校验 prompt | doubao-2.0-lite，JSON 输出，temperature 0.1 |
| `tests/` | 单测 | 每模块独立可测（喂样本 content_list） |

### metadata schema（写进 LlamaIndex node.metadata，不建新库）

```python
# doc 级（每个 node 都带）
"doc_subject": "数学", "doc_type": "textbook|exam|workbook|lecture|answer_book",
"doc_grade": "高一", "struct_path": "必修一/第1章集合/1.1集合的概念"
# 题目级（仅题目 node）
"is_question": True, "q_type": "choice|fill|solve", "q_id": "P12-3",
"has_answer": False,          # 答案在别处，靠 answer_ref 回填
"answer_nodes": ["...id"],    # 或答案 node 反指
"linked_images": ["img_hash1"]
```

## 三、执行步骤（本单内部）

1. 骨架 + classifier + structure（规则部分，纯本地可测）
2. qa_split + image_link（含跨区回填）
3. LLM 分类接入（后台任务 + 缓存 + 降级）
4. enrich 挂进 document_loader（3 行改动，try/except 包裹）
5. 单测：用 `~/Desktop/mineru/教材目录提取结果.json`（570 份，101 份带目录）当结构校验集 + 数学教辅产物当题答样本
6. 垂直切片验收：**独立测试 KB**（`doc-intel-试验田`）拖入 3 类样本（1 教材 PDF + 1 试卷类 + 1 教辅 md）→ 自动出分类/结构/题答标记 → 检索过滤演示（"只搜题干不含答案"）
7. build 镜像 → 容器替换 → 容器内验证（现有 KB 是测试数据，按老板授权可动）

## 四、验收标准

1. 三类样本自动分类正确（人工抽验 10 份）
2. 教材结构树与校验集（101 份目录）≥80% 结构路径吻合
3. 题答分离：试卷样本题干/答案 node 分离率 ≥90%，抽 20 题人工核
4. 图片归属：抽 20 图人工核 ≥80% 挂对
5. 全程无人工介入：拖入→出结果
6. 现有索引不受损（enrich 失败静默降级验证）
7. npx tsc/pytest 过；commit 历史干净

## 五、边界（不做）

- 不建新 SQLite/新表（全 node metadata）——遵守"不引入第二套库"
- 不动官方 parsing/索引核心逻辑（只加 enrich 挂点 3 行）
- 不做前端（T021）、不做检索消费端（T022）
- 老 yuedu 仓库/库：冻结不碰
- KP 生成/先修链：T020 只出结构骨架，KP 线后续单（原 T019 并入新地基）

## 六、风险与对策

| 风险 | 对策 |
|---|---|
| content_list 结构差异（vlm vs 云 API 产物） | loader 兼容双格式（记忆里已有坑案：vlm 无 title 块只有 text+text_level） |
| LLM 分类配额 | 规则优先+缓存+pending 状态标记 |
| 大库 enrich 拖慢索引 | enrich 纯内存操作 <10ms/百块；LLM 部分后台异步 |
| 数学教辅正在 reindex（10436 文档） | 开发不碰运行库；验收用独立 KB；容器替换等 reindex 完成后窗口 |
