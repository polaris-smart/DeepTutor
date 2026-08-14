# 考试范围、教材节点与 mastery 调用指南

示例中的 `<BASE_URL>` 替换为 DeepTutor 服务地址，`<KB_NAME>` 和路径按当前会话知识库替换。服务启用认证时使用登录后的 `dt_token` Cookie，或发送 `Authorization: Bearer <TOKEN>`；本地单用户模式未启用认证时不加认证头。

## 一、用真实教材树定位范围

### 请求

```http
GET /api/v1/knowledge/{kb_name}/textbook-tree
```

```bash
curl --get "<BASE_URL>/api/v1/knowledge/<KB_NAME>/textbook-tree" \
  -H "Authorization: Bearer <TOKEN>"
```

典型响应：

```json
{
  "kb_name": "历史",
  "textbooks": [
    {
      "doc_id": "doc-a",
      "file_name": "教材文件名.pdf",
      "subject": "历史",
      "doc_type": "textbook",
      "grade": "高一",
      "tree": {
        "title": "教材展示标题",
        "children": [
          {
            "title": "第一单元",
            "children": [
              {"title": "第1课 课名", "children": []}
            ]
          }
        ]
      }
    }
  ]
}
```

定位规则：

1. 教材名先完整匹配 `file_name` 或 `tree.title`，再尝试忽略空格、书名号、扩展名和“普通高中教科书”等通用前后缀；用 `subject`、`doc_type`、`grade` 消歧。
2. 从 `tree.children` 逐层构造候选路径。`tree.title` 是展示标题，不保证属于 `struct_path`，不要直接拼入路径。
3. 范围不明时，把真实树中的教材名和完整路径列成一次选择；不要生成不存在的“第 N 章/单元”。
4. `textbooks: []` 表示当前知识库没有可用教材树。此时停止定位并请用户核对知识库或教材解析状态。

## 二、按结构取范围节点

### 请求

```http
GET /api/v1/knowledge/{kb_name}/docs/by-struct?path={struct_path}&limit={limit}
```

含中文或 `/` 的范围用 URL 编码；curl 优先使用 `--data-urlencode`：

```bash
curl --get "<BASE_URL>/api/v1/knowledge/<KB_NAME>/docs/by-struct" \
  -H "Authorization: Bearer <TOKEN>" \
  --data-urlencode "path=第一单元/第1课" \
  --data-urlencode "limit=100"
```

当前固定响应至少包含：

```json
{
  "kb_name": "历史",
  "path": "第一单元/第1课",
  "nodes": [
    {
      "node_id": "node-a1",
      "struct_path": "第一单元/第1课/第一目",
      "file_name": "教材文件名.pdf",
      "preview": "最长 200 字的节点预览……"
    }
  ]
}
```

使用规则：

1. 接口按 `struct_path.startswith(path)` 匹配，会返回该路径及其下级节点。
2. 同一知识库可能有不同教材共享相同结构名。锁定教材后必须按 `file_name` 过滤，并按 `node_id` 去重。
3. 实际部署若返回 `content` 或 `text`，优先把它视为正文；只有 `preview` 时只使用可见的 200 字摘要，不补齐被截断的事实。
4. `is_question`、`q_id`、`q_type`、`has_answer` 可能存在于底层元数据，但当前接口不保证暴露。只在真实响应含这些字段时把节点标成题目。
5. 单元范围先请求一次，再按教材树的每个课级子路径分别请求，合并去重。这样可补齐分散节点，但不能突破单一路径 100 条的上限。
6. 返回数量恰好等于 `limit` 时可能截断。该接口没有 `offset`、`page`、`cursor` 或 `total`；更细路径仍满 100 条时必须披露素材不完整。
7. 为最终每条短句保留证据：`node_id`、`struct_path`、可见原文片段。数字、年份、人名和条约名从片段原样复制。

## 三、学生版读取 mastery 薄弱点

只在学生版、当前回合已有 mastery path 且工具已挂载时调用：

```text
mastery_status()
```

不要传 `path_id`；当前 mastery path 由运行时注入。典型结果结构：

```json
{
  "status": "active",
  "next": {
    "action": "probe",
    "knowledge_point_id": "kp-1"
  },
  "map": {
    "counts": {"mastered": 2, "learning": 1, "new": 3, "total": 6},
    "due_reviews": 0,
    "complete": false,
    "modules": [
      {
        "id": "module-1",
        "name": "第一单元",
        "knowledge_points": [
          {
            "id": "kp-1",
            "name": "知识点名称",
            "type": "memory",
            "status": "learning",
            "mastery": 0.5
          }
        ]
      }
    ]
  }
}
```

映射规则：

1. 先用模块名限定当前考试范围，再把知识点 `name` 与教材节点标题或正文做明确匹配。
2. `learning` 才标为已识别薄弱点；把对应核心考点前移并加 `🔥`，但短句事实仍来自教材节点。
3. `new` 表示未学习，不等于做错；可以优先覆盖，但不要标“薄弱”。`mastered` 不标薄弱。
4. 名称只能模糊对应、模块不在本次范围或工具返回 `status: empty` 时，不做个性化标记。
5. 工具返回“No mastery path is active”或根本未挂载时，直接生成通用内容；不要创建新路径或让用户等待。
6. 老师通用版始终跳过此步骤，即使当前会话恰好有 mastery 数据。

## 四、错误与停止条件

| 状态/结果 | 处理 |
| --- | --- |
| `200` 且 `textbooks: []` / `nodes: []` | 说明空结果，核对知识库或真实路径 |
| `401` | 请用户重新登录或提供有效 token，不反复重试 |
| `403` | 保留 `detail`，请用户申请权限或换有权限的知识库 |
| `404` | 核对 `kb_name`，不猜另一个知识库 |
| `422` | 修正空 `path` 或超出 `1..100` 的 `limit` |
| `500` | 报告服务 `detail`，停止生成声称“有教材依据”的成品 |

节点证据不足以满足每课 5 条核心和 2 条易错时，停止交付合同版。可以展示已定位范围和素材缺口，但不能用模型记忆凑满条数。
