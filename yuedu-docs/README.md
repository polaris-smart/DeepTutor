# DeepTutor（悦学 fork）功能 README · 按功能域梳理

> 2026-09-04 建。配套：八张 archify 架构图（`dt-architecture/`）+《DT 深度研究报告》（wiki 同级目录）。
> 本档回答"**DT 有哪些功能、入口在哪、我们动过什么**"；机制细节查深研报告，图看配图。
> 基线：fork upgrade-v1.6.3 = 上游 v1.6.4 + 悦学增强（生产镜像 v0914）。

---

## 1. 对话运行时（核心中的核心）

**是什么**：一次用户对话 = 一个 turn。唯一 WS 入口 `/ws`（protocol 2.0），统一命令/订阅/断线重放。
**主链**：前端 TurnRuntimeClient → TurnApplicationService → TurnRuntimeManager(6 服务) → ChatOrchestrator → capability.run → AgenticChatPipeline/AgentLoop → LLM 流式 → StreamBus → coordinator journal(seq) → 订阅回放。
**关键事实**：无 LLM 路由（前端显式选 capability，唯一例外 quiz 正则）；探索轮≤8+settlement；租约+fencing token 支持崩溃恢复；事件 journal 支持断线重放。
**悦学改动**：无（此层未动——动的是上层能力）。
**配图**：`dt-turn-sequence.html`（②）、`dt-master-architecture.html`（①主链视图）

## 2. 能力系统（双层契约）

**是什么**：编排层 TurnCapability ×11（chat/deep_solve/deep_question/deep_research/math_animator/visualize/mastery_path/immersive_reading/course_study/immersive_watching/ask_questions）+ 聊天环内 LoopExtension ×15。
**关键事实**：solve/mastery/reading/watching/course_study/ask_questions 全复用 AgenticChatPipeline（flag+换工具面）；research/question/visualize/math_animator 自建管线；KnowledgeCapability（obsidian/marginnote4）独占整轮；外部插件正门 = entry points `deeptutor.extensions`；无 LLM 路由。
**悦学改动**：注册了 `visualization_generation` loop 能力（visualizers 包）。
**配图**：`dt-capability-contract.html`（⑥）、`dt-assets-panorama.html`（⑦）

## 3. KB 与 RAG

**是什么**：8 个引擎（llamaindex 默认 / graphrag / lightrag / lightrag-server / pageindex / pageindex-oss / ima / weknora）+ obsidian 指针型。KB 创建即锁引擎。
**主链**：上传（proxy matcher 排除清单）→ sha256 内容去重 → raw/ → 解析桥 7 引擎（mineru 云 200 页上限→fork 自动分片）→ doc_intel enrich（**doc_tree 索引时自动写入**）→ chunk 512/50 + 多模态图片节点 → embedding → version-N/docstore（embedding 签名多版本）。
**悦学改动**：doc_intel 整套（页脚法四层判据/qa_split/图文链接/enrich 挂进索引链）；MinerU 大 PDF 自动分片（310c3f649）。
**配图**：`dt-ingest-dataflow.html`（③）

## 4. Book / 阅读 / 教材（三套独立子系统）

- **book/**：LLM 生成学习读本（四阶段编译+熔断）与教材正典流（canonicalize 零 LLM，reading 块 passthrough）双流；KB 指纹漂移→stale_page_ids。
- **reading/**：原文保真沉浸阅读器（locator 模型、content-hash 存储、原生 PDF 批注导出）——与 Book 零代码耦合。
- **textbook_struct**：页脚法四层判据，教材结构重建，printed_page 印刷页锚定。
**悦学改动**：正典化三件套+importer+textbook_struct 全部我方补进包体（上游无此线）。
**配图**：`dt-book-workflow.html`（⑤）

## 5. 学习体系（mastery + 错题闭环 + 题库）

**是什么**：硬门自适应教练循环。SQLite 7 表（workspace 隔离），path_id==book_id。
**关键事实**：掌握度=最近5次新近加权正确率+0.9 硬门（MEMORY/PROCEDURE）或 Feynman 定性门（CONCEPT/DESIGN）；间隔复习=固定 Leitner 序列；**KP 双来源**（官方 agentic mastery_build / 结构树 import-from-book）；**kp_mapper 只挂题库不产 KP**；题库原题优先（bank_cursor 轮转逐字替换）；错题 active→retrying→graduated。
**悦学改动**：question-bank 端点+原题优先+mastery_status 错题暴露+六维画像+ingest_pipeline 四段编排。
**配图**：`dt-mastery-lifecycle.html`（④）

## 6. K12 多用户层（我方主战场）

**是什么**：五角色（admin/teacher/student/parent/user）+ AccountPreset（learner 是 preset 不是 role）。
**权限门实态**：require_teacher 严格（admin 也 403）；learner surface 白名单双层门；kb_writer 上 student 永不可写、parent 直通。
**数据四件各归各**：users.json（白名单清洗）/ classes.json / guardians.json / grants/{uid}.json（enabled_tools None=全量 但 mcp_tools None=默认拒绝）。
**K12 域**：children+family / 班级 / assignments（严格 teacher）/ daily-plan / class-insights（v1 路径残留）/ ingest-pipeline 管理卡。
**悦学改动**：这一层几乎全是我们铺的。
**配图**：`dt-k12-multilayer.html`（⑧）

## 7. 伙伴体系（partners）

**是什么**：IM 渠道 AI 伙伴——18 渠道（telegram/discord/slack/email/飞书/钉钉/企微/**个人微信**/QQ/napcat/whatsapp/msteams/matrix/mattermost/zulip/mochat），Partner=合成用户（workspace 全克隆+SOUL 人格），partner_group 圆桌互问，`/link` 绑定真人账号。每条 IM 消息就是一个普通 chat turn。
**悦学改动**：无（未动）。

## 8. 配置 / LLM / 外围

- **LLM**：model_catalog.json（connections+8 services），30+ provider 档位→6 个真 backend；**悦学**：KeyPool api_key 数组轮换、volcengine_agent_plan 预设、volc_plan TTS adapter（ASR wss 未实现）。
- **visualizers/**：声明式可视化协议（zip 禁 Python、schema 校验、submit_visualization 唯一 commit 点）——悦学 39 学件包的宿主。
- **video_learning / co_writer / i18n**：字幕视频学习、协作写作、状态串 i18n（status.yaml 仅 3 个 agent 有，其余走代码默认）。
- **运维提醒**：`/files/outputs` 公开无鉴权；上传端点必须加 proxy matcher 排除清单。

## 快查

| 要找 | 去 |
|---|---|
| 机制原理 | 深研报告 §1-§7 |
| 有没有 X | 深研报告 §10 + 本档对应域 |
| 图 | dt-architecture/ 八张 |
| 我方资产 | ⑦资产全景图 + 本档各域"悦学改动"行 |
