# DeepTutor 1.6.3 融合映射：悦学增强层 ↔ learner / guardian

## 结论先行

五值 `Role` 与 `AccountPreset` 可以正交共存；真正的融合风险是上游 1.6.3 仍在若干 learner 入口把“学习者”定义成 `role == "user" && preset == "learner"`。悦学若使用 `role == "student" && preset == "learner"`，学习策略可以生效，但自助画像和设备凭据会被拒绝。因此 1.6.4+ 融合应以“组织角色、账号画像、关系授权三层分离”为主线，而不是让 `student/parent` 与 `learner/guardian` 互相替代。

目标真源如下：

- `role`：悦学组织授权（admin / teacher / student / parent / user）。
- `preset`：沿用上游账号画像和学习策略（standard / learner / custom）。
- guardian relationship：监护授权真源；悦学 `children` 只作为迁移期兼容投影。
- class roster：悦学独有的教学组织真源，继续独立存储。
- course / question source / visualizer binding：保留悦学分发和挂载语义，复用上游对象模型与插件目录。
- session gate：以上游 `require_auth → learning_policy → surface/material/guardian permission` 为主干，K12 角色门禁只做窄增量。

## 范围与证据口径

- 悦学侧：当前工作树 `HEAD b5b002cf9ef8b5e5832c01b3257842b88b81055d`。
- 上游侧：标签 `v1.6.3`，提交 `6e6e56aedb559ccb6e147e25024352b60da28b90`。
- 上游行号全部来自 `git show v1.6.3:<path> | nl -ba`，不以当前同名文件代替。
- `require_consent` 经 `HEAD`、`v1.6.3` 和全历史 `-S` 检索均无定义；本文把任务书中的该词解释为“显式监护授权 + permission 校验”，不会虚构第二套门禁。
- 关系词含义：等价 = 同一能力；正交 = 不同维度；冲突 = 重叠真源或谓词不兼容；互补 = 上游底座与悦学增量可以组合。

## 主映射表

| 能力域 | 悦学实现（文件:行号） | 上游 1.6.3 对应（文件:行号） | 关系 | 融合策略 | 触发条件（上游动作） |
|---|---|---|---|---|---|
| 1. 角色模型 | `deeptutor/multi_user/models.py:9-17` 定义五值 Role 与三值 preset；`deeptutor/multi_user/identity.py:72-97` 分别规范化并可同时保留；`deeptutor/multi_user/identity.py:483-536` 分开修改 | `v1.6.3:deeptutor/multi_user/models.py:9-29` 只有 admin/user，preset 明确不是第三角色；`v1.6.3:deeptutor/multi_user/identity.py:72-90,476-487` 独立规范化；`v1.6.3:deeptutor/multi_user/grants.py:136-158` 展开 learner 策略 | 正交 | **对齐**：保留五值 Role 和上游 preset；引入统一 learner/guardian 谓词，消除散落的 `role == "user"` 假设 | 上游修改 `Role` / `AccountPreset`、新增 preset，或改动 learner-only 入口及设备凭据校验时立即对齐 |
| 2. 监护关系 | `deeptutor/multi_user/identity.py:89-97,498-521` 把 `children` 用户名列表嵌在 parent 记录；`deeptutor/api/routers/courses.py:246-273` 用它授权向孩子复制课程；`deeptutor/api/routers/class_insights.py:348-367` 用它限定家庭学情 | `v1.6.3:deeptutor/multi_user/guardians.py:17-20,81-136,203-222` 是 user-id 关系、四权限、可撤销且读取时复验；`v1.6.3:deeptutor/api/routers/multi_user.py:522-597` 以 `assign_materials` 全量替换只读物料，遗漏项即移除 | 冲突 | **迁移**：guardian relationship 成为唯一授权真源；把 parent→children 转成带 `view_reports` / `assign_materials` 的关系，迁移期只读投影回 `children` | 上游新增或修改 guardian 关系字段、权限、物料管理 API，或首次合入 1.6.4+ guardian 迁移时启动 |
| 3. 班级 | `deeptutor/multi_user/classes.py:1-19,58-60` 以独立 `classes.json` 避开用户 canonical 清洗；`deeptutor/multi_user/classes.py:99-135,143-181,184-237` 校验 live student、teacher owner、管理员越权和非破坏级联 | `v1.6.3:deeptutor/multi_user/models.py:9-10` 没有 teacher/student；`v1.6.3:deeptutor/services/courses.py:250-255` 只有当前用户 workspace 内的课程容器，无 cohort/class/roster 实体 | 正交 | **保留**：继续作为独立 K12 组织层，不塞入 user、guardian 或 course 记录 | 上游首次引入 cohort / class / roster / group learner assignment 模型或批量分配 API 时再做实体映射 |
| 4. 课程分发 | `deeptutor/api/routers/courses.py:200-244` 从本人或 teacher/admin workspace 找模板；`deeptutor/api/routers/courses.py:246-293` 以 admin/parent 矩阵选目标、创建新实例、重挂资源 | `v1.6.3:deeptutor/services/courses.py:1-17,42-52,132-160,250-255` 定义 per-user 课程、资源引用、教学约定和 syllabus；`v1.6.3:deeptutor/api/routers/courses.py:69-186` 只有本 workspace CRUD；`v1.6.3:deeptutor/capabilities/course_study/capability.py:1-16,383-413` 是官方课程编排 | 互补 | **对齐**：保留 copy 作为 `CourseService` 上的薄分发层；课程结构和 course_study 由上游主导，跨账号授权改读 guardian 真源 | 上游加入 course share/template/clone/import-export ACL，或修改 `StudyCourse` / workspace root / course_study 绑定协议时启动 |
| 5. 题源 | `deeptutor/learning/models.py:95-104` 增加 KP `meta`; `deeptutor/api/routers/mastery_path.py:668-720` 写入外部题库；`deeptutor/capabilities/mastery/tools.py:425-453,585-614` 暴露未清错题并用 `bank_cursor` 轮转，题库优先于模型草稿 | `v1.6.3:deeptutor/learning/models.py:80-86` 的 KP 没有题源字段；`v1.6.3:deeptutor/capabilities/mastery/tools.py:449-604` 由 agent 提供题干/答案再注册；`v1.6.3:deeptutor/reading/quiz.py:20-34,111-146` 另有基于阅读上下文的 LLM 测验生成 | 互补 | **保留**：外部题源作为 deterministic provider，模型生成作 fallback；不要并入 attempt/question-history 真源 | 上游提供 QuizProvider / question-source 插件口，或修改 KnowledgePoint schema、持久化迁移、mastery_quiz 契约时接入适配器 |
| 6. 学件挂载 | `deeptutor/learning/models.py:95-104` 在 KP 存 visualizer id；`deeptutor/api/routers/mastery_path.py:723-761` 提供替换式绑定；`deeptutor/services/session/_turn_runtime_shared.py:293-323` 向 tutor 注入挂载 manifest；`deeptutor/learning/policy.py:305-321` 暴露给前端地图 | `v1.6.3:deeptutor/visualizers/protocol.py:23-46` 定义 manifest；`v1.6.3:deeptutor/visualizers/registry.py:21-80,108-152` 组合 core/bundled/user 插件；`v1.6.3:deeptutor/api/routers/visualizers.py:39-56,89-126` 管理目录和导入，但 `v1.6.3:deeptutor/learning/models.py:80-86` 无 KP 绑定 | 互补 | **对齐**：保留 KP→visualizer-id 边，id/启用状态/manifest 均以官方 registry 为准；绑定写入时补 registry 校验 | 上游新增 context/KP 级 visualizer 选择或绑定，或修改 manifest/catalog schema、安装可见性时启动 |
| 7. 前端角色视图 | `web/app/(workspace)/family/page.tsx:15-17,49-81` 仅 parent/admin；`web/app/(workspace)/admin/class/page.tsx:17-19,51-92` 仅 teacher/admin，并加载班级过滤；两页展示逐生 KP 掌握度 | `v1.6.3:web/features/settings/navigation/settings-access.ts:21-40` 按 auth status + preset 显示 learner/guardian 设置；`v1.6.3:web/features/settings/sections/LearnerProfileSettingsSection.tsx:21-64` 自助画像；`v1.6.3:web/features/settings/sections/GuardianSettingsSection.tsx:39-44,87-149` 按权限管理 learner | 互补 | **对齐**：保留家庭/班级学情页；可见性改由后端 capability/relationship 响应驱动，避免前端 Role 集合成为安全真源 | 上游新增 learner/guardian dashboard，或修改 auth status、settings access/navigation、guardian UI 路由时启动 |
| 8. 会话/权限门禁 | `deeptutor/api/routers/auth.py:387-429` 增加 admin-or-teacher，同时保留 admin 门禁；`deeptutor/api/routers/auth.py:432-456` 保留 learning-surface 默认拒绝；`deeptutor/api/main.py:526-565` 挂到 workspace routers。仓库没有 `require_consent` 定义 | `v1.6.3:deeptutor/api/routers/auth.py:380-428` 是 admin + `require_learning_surface`; `v1.6.3:deeptutor/multi_user/learning_access.py:34-70,73-107` 约束 capability/tool/surface/material；`v1.6.3:deeptutor/services/session/turns/request_preparer.py:135-149` 在 turn 入库前应用；`v1.6.3:deeptutor/multi_user/device_credentials.py:154-188` 设备会话还硬编码 user+learner | 冲突 | **对齐**：上游门禁保持 canonical；K12 只增加 teacher/parent 的最小权限谓词；显式 guardian permission 承担 consent，不另建平行门禁 | 上游改动 auth dependency、learning policy、turn request preparation、guardian permission 或设备 session token 校验时逐点复核 |

## 逐域分析

### 1. 角色模型：两个轴应分离，但 learner 谓词尚未分离

上游设计已经明确：role 只区分 admin/user，preset 配置普通账号。悦学把 role 扩为组织身份，仍保留 preset，方向正确。失配发生在遗留谓词：当前 `deeptutor/api/routers/auth.py:1040-1054` 的自助画像要求 `current.role == "user"`，当前 `deeptutor/multi_user/device_credentials.py:154-163` 也只认 `role == "user"`；因此 student 即便带 learner preset 仍无法使用这两项上游能力。

融合时应定义统一语义谓词，例如“非 admin 且 preset=learner”为 learner account，“具备有效 guardian relationship”为 guardian actor；Role 继续决定组织页面和班级操作。不要把 student 降回 user，也不要把 learner 升成 Role 值。

### 2. 监护关系：`children` 是名单，guardian 是授权

`children` 适合快速投影家庭成员，但缺少稳定 user id、权限粒度、授权时间、撤销和审计。guardian 记录已经覆盖这些能力，并在每次访问时重新验证双方账号。两套关系若继续独立写入，会出现“家庭页可见但不能分配物料”或相反的分叉。

迁移应以 `(parent user_id, child user_id)` 建 guardian relationship；家庭学情读取 `view_reports`，课程/物料分发读取 `assign_materials`。`children` 在兼容期由有效关系派生，停止作为授权判断。上游的 PUT materials 是全量集合语义，既能添加也能通过遗漏移除已批准物料，悦学不应另做增量 ACL。

### 3. 班级：上游没有对应实体

1.6.3 的 learner/guardian 解决“一名监护人与一名学习者”的授权，course 解决“一个用户 workspace 内的学习容器”；两者都不表达 teacher-owned roster。悦学班级同时包含 owner、成员校验、稳定排序和非破坏删除，属于独立教学组织域。

因此班级继续独立存储是必要边界。未来若上游出现 cohort/group，不按名称直接合并，先比较成员身份、owner、跨班复用、撤销和批量分发语义。

### 4. 课程分发：复制是跨 workspace 传递，course_study 是单实例编排

上游 CourseService 已提供稳定的课程结构和官方 course_study loop，但所有 CRUD 都落在当前用户 workspace，没有跨账号 clone/share。悦学 copy 填补的是分发缺口：读取 staff 模板，给获准目标创建独立实例，重置 syllabus 进度，不复制 agent notes，并重新生成资源边。

这不是与 course_study 竞争的第二课程模型。融合时保持“上游定义课程、悦学定义谁能复制给谁”；监护授权从 `children` 切到 guardian permission 后，copy 路由只剩一个薄 ACL + clone 适配层。若上游提供正式 share/template API，应迁到其 provenance 和 ACL，而不保留两套复制协议。

### 5. 题源：生成机制与题源机制不是同一层

上游 mastery_quiz 的题干和答案来自 agent 调用，reading quiz 则用 LLM 从可见文本生成三题；两者都是生成路径。悦学 `question_bank` 提供经过外部题库约束的确定性输入，`bank_cursor` 解决同一 KP 的顺序轮转，`wrong_items` 把未毕业错题重新带回教学循环。

外部题源应保持 provider 身份，不与答题记录、错题状态或 notebook question bank 混成一个表。融合接口的优先级应固定为“已绑定外部题源 → 上游生成 fallback”，并保留上游注册、确定性评分和 mastery gate。

### 6. 学件挂载：悦学增加关联边，上游提供插件真源

上游已经有成熟的 VisualizerManifest、目录、安装/启停和 sandboxed iframe 导入；缺的是“哪个 KP 应使用哪个 visualizer”的关联。悦学在 KP 上存 id，并把 id 注入 tutor 与前端地图，正好补齐关联层。

当前绑定端点只做去空、去重和数量上限，没有验证 id 是否存在、已安装、已启用。融合时不要复制 manifest 到 KP；保存边时查询官方 registry，运行时仍按 registry 的可见性和版本解析。这样插件卸载/禁用不会留下可执行的影子实现。

### 7. 前端角色视图：视图互补，安全边界不能停在前端

悦学 family/class 页解决聚合学情，分别对应 parent/admin 与 teacher/admin；上游 UI 解决 learner 自助画像和被授权 guardian 的物料、限制、凭据管理。它们面向不同任务，可以同时存在。

融合风险是可见性模型分叉：悦学页面硬编码 Role 集合，上游 settings 按 preset 显示入口。前端判断只能改善导航，真正授权必须由后端 role + relationship + permission 决定。迁移 children 后，family 页应以有效 guardian relationship 的 learner 列表渲染，而不是继续直接读 parent 记录。

### 8. 会话与权限：沿用 canonical 主干，修正 K12 边缘谓词

`require_learning_surface` 在悦学与 v1.6.3 语义等价：先认证，再按 URL 映射 surface，交给 learning policy 默认拒绝；turn 准备阶段还会清空 learner 的工具、KB、web search 和 partner 注入。悦学新增的 `require_admin_or_teacher` 是班级只读视图所需的窄增量。

冲突来自不同位置对“普通用户”的解释不一致，而不是缺少另一层 consent dependency。guardian permission 已经是显式授权记录，应直接作为监护同意边界。每次上游改 auth、turn preparation 或 device token，都要用 `admin/user/student/teacher/parent × standard/learner/custom` 组合矩阵复核，而不是只跑 admin/user。

## 融合顺序与不变量

建议按依赖顺序执行未来融合：

1. 先统一 account predicates，使 student+learner 能通过 learner profile、learning policy 和 device credential。
2. 再把 `children` 迁到 guardian relationship，保留兼容投影并切换 family/course-copy 读取方。
3. 课程 copy 改读 guardian permission；班级继续独立。
4. 题源和 KP visualizer binding 作为上游模型的扩展字段/关联边接入迁移机制。
5. 最后统一前端入口可见性，并用服务端拒绝结果做端到端验收。

全过程必须保持四条不变量：

- preset 变化不自动篡改组织 Role；Role 变化也不静默删除 learner profile/grant。
- 没有有效 guardian relationship + permission，任何 parent/standard/custom 账号都不能访问 learner 数据。
- course copy 创建新实例，不共享进度或 agent notes；资源仍只是引用。
- visualizer id 不绕过 registry 的安装、启用、schema 与 sandbox 规则。

## 上游 `feat(learner)` 变更清单（v1.6.2..v1.6.3）

以下 11 条已全部纳入上表和分析：

| 提交 | 标题 | 主要映射域 |
|---|---|---|
| `90351189a` | feat(learner): complete guardian management flows | 监护、前端、门禁 |
| `ecd7434ae` | feat(learner): let guardians remove approved materials | 监护、课程/物料分发 |
| `24427d340` | feat(learner): add guardian management UI | 监护、前端 |
| `1fafdf48a` | feat(learner): add guardian authorization | 监护、门禁 |
| `364bbf7da` | feat(learner): add profile settings navigation | 前端角色视图 |
| `e7d3f3c5d` | feat(learner): add self-service learner profiles | 角色、前端、门禁 |
| `545c3610d` | feat(learner): expose learner profile controls | 角色、前端 |
| `93d1a8031` | feat(learner): add account-scoped learner profiles | 角色模型 |
| `b0ff5f18b` | feat(learner): add revocable device login credentials (#1116) | 角色、监护、门禁 |
| `3f62d8599` | feat(learner): add guardian authorization | 监护、门禁 |
| `8ea83989a` | feat(learner): add server-enforced account presets (#1111) | 角色、门禁 |

## 复核命令

```bash
git rev-parse v1.6.3^{commit}
git log v1.6.2..v1.6.3 --format='%h %s' --grep='^feat(learner)'
git show v1.6.3:deeptutor/multi_user/guardians.py | nl -ba
git show v1.6.3:deeptutor/multi_user/device_credentials.py | nl -ba
git show v1.6.3:deeptutor/api/routers/courses.py | nl -ba
git show v1.6.3:deeptutor/capabilities/mastery/tools.py | nl -ba
rg -n 'require_consent' deeptutor web --glob '!web/package-lock.json'
wc -l docs/fusion-mapping-163.md
```
