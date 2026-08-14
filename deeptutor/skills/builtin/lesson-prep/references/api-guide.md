# 教材树与按结构取节点 API 指南

本指南对应 T021 的两个只读端点。示例中的 `<BASE_URL>` 替换为 DeepTutor 服务地址，例如 `http://127.0.0.1:8001`；`<KB_NAME>`、教材名与路径按实际知识库替换。

如果服务启用了认证，使用登录后的 `dt_token` Cookie，或增加请求头：

```text
Authorization: Bearer <TOKEN>
```

本地单用户模式未启用认证时不需要该请求头。

## 1. 获取知识库内教材树

### 请求

```http
GET /api/v1/knowledge/{kb_name}/textbook-tree
```

`kb_name` 是路径参数，包含空格、斜杠外的特殊字符时应进行 URL 编码。

### curl 示例

```bash
curl --get "<BASE_URL>/api/v1/knowledge/%E6%94%BF%E6%B2%BB/textbook-tree" \
  -H "Authorization: Bearer <TOKEN>"
```

认证关闭时去掉 `-H`。成功响应示例：

```json
{
  "kb_name": "政治",
  "textbooks": [
    {
      "doc_id": "doc-a",
      "file_name": "必修1 中国特色社会主义.pdf",
      "subject": "政治",
      "doc_type": "textbook",
      "grade": "高一",
      "tree": {
        "title": "中国特色社会主义",
        "children": [
          {
            "title": "第一单元",
            "level": 1,
            "children": [
              {
                "title": "第一课",
                "level": 2,
                "children": []
              }
            ]
          }
        ]
      }
    }
  ]
}
```

使用要点：

- 接口聚合同一知识库中带 `doc_tree` 元数据的文档，并按 `doc_id` 去重。
- 无索引或无教材树时仍返回 `200`，`textbooks` 为 `[]`。
- `tree.title` 是展示标题，不保证属于 `struct_path`；候选路径应从实际层级节点构造，并用按结构取节点接口验证。
- 教材名优先与 `file_name` 对照；`subject`、`doc_type`、`grade` 只用于辅助消歧。

## 2. 按结构路径获取课内节点

### 请求

```http
GET /api/v1/knowledge/{kb_name}/docs/by-struct?path={struct_path}&limit={limit}
```

查询参数：

| 参数 | 必填 | 默认值 | 约束 | 含义 |
| --- | --- | --- | --- | --- |
| `path` | 是 | 无 | 非空 | `struct_path` 前缀，例如 `第一单元/第一课` |
| `limit` | 否 | `20` | `1` 到 `100` | 本次最多返回的节点数 |

### curl 示例

推荐用 `--data-urlencode` 传递含中文和 `/` 的路径：

```bash
curl --get "<BASE_URL>/api/v1/knowledge/%E6%94%BF%E6%B2%BB/docs/by-struct" \
  -H "Authorization: Bearer <TOKEN>" \
  --data-urlencode "path=第一单元/第一课" \
  --data-urlencode "limit=100"
```

成功响应示例：

```json
{
  "kb_name": "政治",
  "path": "第一单元/第一课",
  "nodes": [
    {
      "node_id": "node-a1",
      "struct_path": "第一单元/第一课",
      "file_name": "必修1 中国特色社会主义.pdf",
      "preview": "第一课 社会主义从空想到科学、从理论到实践的发展……"
    },
    {
      "node_id": "node-a2",
      "struct_path": "第一单元/第一课/第二目",
      "file_name": "必修1 中国特色社会主义.pdf",
      "preview": "科学社会主义的理论与实践……"
    }
  ]
}
```

使用要点：

- 匹配规则是 `struct_path.startswith(path)`，因此会同时返回本课及其下级小节的节点。
- 当前固定响应字段是 `node_id`、`struct_path`、`file_name`、`preview`；`preview` 最长 200 个字符，不等同于完整正文。
- `q_type`、`is_question`、`q_id`、`has_answer`、`linked_images` 可能存在于底层节点元数据，但当前 T021 响应不保证暴露。若实际部署的响应含这些字段，可以使用；若没有，必须明确标注缺失，不得虚构。
- 同一知识库的不同教材可能拥有相同 `struct_path`。定位教材后必须再按 `file_name` 过滤，并用 `node_id` 去重。
- 路径没有匹配项时仍返回 `200`，`nodes` 为 `[]`。

## 分页与截断

该端点当前只有 `limit`，没有 `offset`、`page`、`cursor` 或 `total`，因此它是数量上限而不是真正的可翻页分页。

1. 一般备课请求直接设置 `limit=100`。
2. 返回节点数恰好等于 `limit` 时，视为“可能被截断”，不要声称素材已经完整。
3. 教材树有下级小节时，可分别用更具体的子路径请求，合并后按 `node_id` 去重；同时保留课级请求，以免漏掉直接挂在课节点下的正文。
4. 如果单一路径本身仍超过 100 个节点，当前接口无法可靠取得第 101 个及之后的节点，应在教案素材说明中披露该限制。

## 错误码

| 状态码 | 常见情况 | 处理方式 |
| --- | --- | --- |
| `200` | 请求成功；也可能是空教材树或空节点列表 | 检查 `textbooks` / `nodes` 是否为空 |
| `401` | 服务启用认证但缺少、过期或无效的 token | 请用户重新登录或提供有效 Bearer token；不要反复重试 |
| `403` | 当前用户没有该知识库的读取权限 | 保留错误详情，请用户换有权限的知识库或申请授权 |
| `404` | 知识库不存在，或默认知识库未配置 | 核对 `kb_name`；不要猜测另一个知识库继续 |
| `422` | `path` 缺失/为空，或 `limit` 不在 `1..100` | 修正查询参数后重试 |
| `500` | 文档存储读取、元数据解析或服务内部异常 | 报告服务返回的 `detail`，停止用该接口生成有教材依据的内容 |

错误响应通常形如：

```json
{
  "detail": "Knowledge base '政治' not found"
}
```

处理错误时保留 `detail` 原文，便于老师或管理员定位问题。
