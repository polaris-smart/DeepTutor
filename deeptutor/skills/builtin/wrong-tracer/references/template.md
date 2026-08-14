# 错题归因与追分计划输出合同

把本文件当作归因报告 Markdown 的结构合同。替换所有花括号占位符，删除说明性文字；没有数据时写“未提供”或“待确认”，不要删列或虚构内容。

```markdown
<!--
deeptutor_handoff:
  subject: "{学科}"
  textbook: "{教材名或未提供}"
  scope: "{错题范围/章节}"
  student: "{学生标识或通用}"
  date: "{YYYY-MM-DD}"
  upstream: "{question_notebook:<条目 id> / question_notebook / student_statement}"
  suggested_downstream: "type-cards / pre-exam-checklist"
-->

# 🧭 {范围}错题归因与追分计划

> **数据依据**：{question_notebook 条目 / 学生口述}
> **估算口径**：预计回收分 = 丢分 × 回收系数（记忆型 0.8 / 概念型 0.6 / 程序型与设计型 0.4），且不超过该题丢分；无丢分数据写“—”。均为规划估算，不是成绩承诺。

## 一、错题清单与归因

| 题号 / q_id | 题型 | 题干摘要 | 作答摘录 | 三分法错因 | ErrorType | 证据 | 知识类型 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| {q_id 或题号 N} | {题型} | {一句话题干} | {关键作答片段} | {知识性/方法性/习惯性/待确认} | {structural/deviation/application/metacognitive/待确认/—} | {可追溯反馈或最短确认问题} | {memory/concept/procedure/design/未提供} |

## 二、追分计划表

| 错因 | 追分动作（做哪 3 题） | 预计回收分 | 完成标准 |
| --- | --- | ---: | --- |
| {三分法错因 × ErrorType} | `{q_id-1}`（重做）→ `{q_id-2}`（同类补题）→ `{q_id-3}`（同类补题）；或 `待补题（缺 N 个 q_id）` + 建议题型 | {估算分或—} | {可观察、可判定的标准} |

## 三、口径与待补项

- **已核验**：{守恒检查、真实 q_id 数量、错因证据}
- **待确认**：{错因、知识类型、题目缺口；没有则写“无”}
- **预计分说明**：预计回收分是基于本次错题与知识类型的规划估算，不是成绩承诺；精确分数空间由 `potential-scanner` 评估。

> 下一步：把本报告交给 `type-cards` 生成题型提分卡，或交给 `pre-exam-checklist` 生成考前清单。
```

## 交付前硬检查

- header 恰有七个字段，字段名、顺序与 `pre-exam-checklist` 一致；`upstream` 指向真实来源。
- 每题只归因一次；错因只使用三分法 + 四个 `ErrorType` 值、待确认或破折号。
- 六维维度没有被写成 `ErrorType`；`q_id` 没有被猜测。
- 每条追分动作恰有 3 个真实 q_id 或明确的待补缺口，没有假 ID。
- 预计回收分不超过对应丢分；无丢分数据的行写“—”，没有凭空估算。
