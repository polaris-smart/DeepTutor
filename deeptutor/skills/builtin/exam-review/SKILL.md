---
name: exam-review
description: 把整卷判分结果组装成可存档、可发给学生或全班的卷后提分分析报告；当用户提到卷后分析、试卷分析、考试复盘、成绩分析报告、全班讲评、学生存档、导出 A4 或 PDF 报告时使用。
tags:
- study
- exam
- report
always: false
---

# 卷后提分试卷分析

把 `score-diagnosis` 的事实诊断与 `potential-scanner` 的规划估算包装成一份成品报告。它是组装器，不是第三套算法：同一个分数只计算一次，同一个结论保留来源，版式升级不能改变上游数据。

开始后依次调用：

1. `read_skill(name="score-diagnosis", file="SKILL.md")` 与 `read_skill(name="score-diagnosis", file="references/template.md")`；
2. `read_skill(name="potential-scanner", file="SKILL.md")` 与 `read_skill(name="potential-scanner", file="references/template.md")`；
3. `read_skill(name="exam-review", file="references/template.md")`。

生成 A4 HTML 前，再调用 `read_skill(name="pre-exam-checklist", file="references/print-css.md")`，完整读取并复用其 CSS 骨架。

## 第一步：取得两份上游结果

1. 用户已经提供合格的诊断报告与矩阵时直接复用，不重复诊断或重算。
2. 缺少诊断报告时，按 `score-diagnosis` 剧本处理原始逐题判分；缺少矩阵时，把诊断报告的“接力数据”按 `potential-scanner` 剧本转换。执行顺序固定为诊断 → 矩阵。
3. 核对两份 header 的 `subject`、`textbook`、`scope` 与 `student`。除“未提供”可由另一份同源报告补齐外，任何实值冲突都必须停止合并并请用户确认；不要把不同学生或不同考试拼成一份报告。
4. 核对诊断总丢分、知识点丢分合计、矩阵丢分合计。三者不守恒时回到逐题数据纠正，再做包装；不能为了排版漂亮而隐藏差额。
5. 保留上游的“待确认”“未提供”“均分估算”和“未计入估算”，不要在成品化过程中把不确定性抹掉。

## 第二步：设计个性化补弱清单

以矩阵 P1、P2、P3 为主要顺序，为每个有证据的优先知识点生成一条补弱动作。每条必须包含：

- 目标知识点与主要错因；
- **做哪 3 题**：列出 3 个互不重复、真实存在的题目节点 `q_id`；
- 做题顺序与复盘方法；
- 可观察的完成标准，例如“3 题全对并能口述漏条件检查”。

题目选择规则：

1. 先使用上游接力数据中与该知识点关联的真实 `q_id`，优先重做本卷错题。
2. 不足 3 个时，只在当前已挂载的题目检索能力中按同一学科、范围和知识点补题；仅采用工具真实返回的题目节点与 `q_id`，并标明“同类补题”。
3. 仍不足 3 个时写 `待补题（缺 N 个 q_id）` 并列出现有题目，不用题号、模型生成题或虚构字符串冒充 `q_id`。此时报告可以交付，但必须在封面数据说明和交付摘要中披露清单未满合同。
4. 同一个 `q_id` 不得在同一动作内重复。跨动作确需复用综合题时说明它分别训练哪个步骤，不能为了凑数机械复制。

## 第三步：组装完整复盘报告

严格套用 `references/template.md`：

1. **封面**：考试名称、学科、范围、学生或通用、日期、满分、得分、丢分、数据完整性。
2. **第一部分 诊断**：保留逐题错因表、按题型/知识点的丢分分布、TOP3；不得只给摘要而丢掉证据。
3. **第二部分 空间矩阵**：原样带入知识点、当前掌握、丢分、难度、预计可回收分数与优先级，并保留估算口径。
4. **第三部分 个性化补弱清单**：按 P1 起列出动作，每条具体到 3 个真实 `q_id` 或明确的待补题缺口。
5. 报告最前面放标准 `deeptutor_handoff`。七字段顺序与 `pre-exam-checklist` 一致；`upstream` 写 `score-diagnosis + potential-scanner`，`suggested_downstream` 固定为“全开放”。

老师要求“发全班”时生成通用版：`student` 写“通用”，只使用本次全班汇总数据，不混入当前会话某一学生的 mastery 或错题。学生个人存档版则保留该学生标识与个人数据；两种版本不得混写。

## 第四步：写出 Markdown 与 A4 HTML

1. 先生成一份已核验的内容模型；Markdown、HTML 和可选 PDF 都从它转换，不在格式之间重新推理或改数。
2. 必须调用 `exec` 把完整 Markdown 写入新 `.md` 文件，再把同一内容转换为独立 `.html` 文件。文件名使用 `exam-review-<日期>-<短考试名>`；已存在时追加短范围名或递增后缀，不覆盖用户文件。
3. Markdown 与 HTML 源码最前面都放相同的 `deeptutor_handoff` 注释块。HTML 还必须含 `<!doctype html>`、`<meta charset="utf-8">` 与视口元数据。
4. 把 `pre-exam-checklist/references/print-css.md` 的整个 CSS 代码块原样内嵌到 `<style>`，再按需追加报告专用样式。保留 `.sheet`、`.title`、`.lead`、`.lesson-list`、`.lesson`、`.lesson-grid`、`.core-panel`、`.mistake-panel` 和 `.unit-frame` 的结构语义，确保视觉与考前清单一致。
5. 写出后用 `exec` 重新读取并检查：两文件 header 一致；总分/得分/丢分一致；HTML 含 `@media print` 与 `A4 portrait`；Markdown 三部分齐全；不存在未替换的 `{占位符}`。
6. 从 `exec` 返回结果取得 `.md` 与 `.html` 的 Generated artifacts URL，返回可下载链接，不把沙箱路径冒充下载地址。

## 第五步：可选 PDF

用户明确要求 PDF 且 PDF 能力可用时，完整读取 `pdf` skill，再从已核验 HTML 导出 PDF；不要从聊天文本重新排版。导出后检查页数、表格截断、中文字体、页边距和每条补弱动作的 3 个 `q_id`，返回 PDF artifact。

PDF 能力不可用或导出失败时，保留可打印 HTML 与 Markdown，说明具体失败，不阻塞前两种格式，也不伪造 PDF 链接。

## 异常与停止条件

- 只有总分、没有逐题得分：停止完整报告，说明缺少诊断和矩阵的必要输入。
- 两份上游报告的范围或学生冲突：停止合并并展示冲突，不擅自选择。
- 真实 `q_id` 不足：按第二步降级并披露，不编造题目节点。
- `exec` 不可用：可以在聊天中给 Markdown 预览，但明确未生成文件；不要贴一段代码后声称已可下载。
- `.md` 和 `.html` 写出并验证后停止。除非用户明确要求，不自动创建训练、考前清单、notebook 或其他下游资产。
