# T-084 · visualizers 注册表诊断

## 结论

**三选一：真故障。** 但 `visualizers.json` 为空本身不是故障：它是安装策略覆盖层，不是用户包目录索引。真正故障是任务书确认的 39 包位于 `/opt/yuedu/data/visualizers/`，而 `v1.6.3` 在该运行目录布局下只扫描 `/opt/yuedu/data/user/visualizers/`；只存在于前者的包不会进入 catalog，也不能被 Visualize 运行时选择或取资产。（`v1.6.3:deeptutor/runtime/home.py:28-49`；`v1.6.3:deeptutor/services/path_service.py:82-87,115-116,208-214`；`v1.6.3:deeptutor/visualizers/store.py:47-51,106-126`）

判定依据链：

1. `DEEPTUTOR_HOME=/opt/yuedu` 时数据根为 `/opt/yuedu/data`，默认 user root 为其下的 `user/`。（`v1.6.3:deeptutor/runtime/home.py:28-49`；`v1.6.3:deeptutor/services/path_service.py:82-87,115-116`）
2. Store 将包根固定为 `<user root>/visualizers`，将状态文件固定为 `<user root>/settings/visualizers.json`。（`v1.6.3:deeptutor/visualizers/store.py:47-51`；`v1.6.3:deeptutor/services/path_service.py:208-214`）
3. Registry 只合并 core、bundled 和该包根的扫描结果；磁盘用户包一旦扫描成功便直接加入 `installed`，不要求出现在 JSON 的 `installed[]`。（`v1.6.3:deeptutor/visualizers/registry.py:21-50`）
4. 生成提示、提交校验和 iframe 资产都从这个 Registry 取包；未被扫描的 ID 会被判 unavailable，资产请求也会失败。（`v1.6.3:deeptutor/visualizers/loop_capability.py:25-36,74-88`；`v1.6.3:deeptutor/visualizers/tool.py:64-98,109-123`；`v1.6.3:deeptutor/visualizers/registry.py:62-70,119-123`）

## 加载链路

| 环节 | 实际机制 | 源码锚点 |
|---|---|---|
| 定位 | 包目录=`get_user_root()/visualizers`；状态=`settings/visualizers.json` | `v1.6.3:deeptutor/visualizers/store.py:41-51` |
| 读状态 | 读取三组 ID；文件缺失、坏 JSON 或 I/O 错误均退化为空组 | `v1.6.3:deeptutor/visualizers/store.py:53-62` |
| 扫目录 | 每个一级子目录须有 `visualizer.json`，manifest、入口和安全规则全部通过才返回；坏包静默跳过 | `v1.6.3:deeptutor/visualizers/store.py:106-126,192-207` |
| 合并 | core 永远 installed；bundled 受 `installed/uninstalled` 控制；扫描到的 user 包无条件 installed | `v1.6.3:deeptutor/visualizers/registry.py:21-50` |
| 启停 | 最终 enabled=`installed - disabled` | `v1.6.3:deeptutor/visualizers/registry.py:48-57` |
| 对外清单 | GET `/api/visualizers/list` 每次新建 Registry 并返回动态 `installed/enabled` 标志 | `v1.6.3:deeptutor/api/routers/visualizers.py:39-45`；`v1.6.3:deeptutor/visualizers/registry.py:72-80,149-152` |
| 前端消费 | 前端请求上述清单，只把 installed 且 enabled 的项列为可用类型 | `v1.6.3:web/lib/visualizers-api.ts:3,32-36`；`v1.6.3:web/components/visualize/VisualizeConfigPanel.tsx:56-81` |

这里没有“启动时扫描后重写注册表”。`get_visualizer_registry()` 为避免跨用户泄漏而每次返回新实例，构造函数立即 `reload()`；扫描结果只保存在该实例内。（`v1.6.3:deeptutor/visualizers/registry.py:13-21,149-152`）

## 谁写 `visualizers.json`

- enable/disable 只改 `disabled[]`；不会把用户包 ID 写入 `installed[]`。（`v1.6.3:deeptutor/visualizers/store.py:78-86`）
- bundled install/uninstall 才改 `installed[]/uninstalled[]`；`v1.6.3` 唯一 bundled 项是 GeoGebra。（`v1.6.3:deeptutor/visualizers/store.py:88-104`；`v1.6.3:deeptutor/visualizers/builtin.py:294-335`）
- 上传 API 校验 zip 后将用户包复制到 Store 包根，再调用 `set_enabled(..., True)`；因此正常导入用户包后 `installed[]` 仍可为空。（`v1.6.3:deeptutor/api/routers/visualizers.py:89-123`；`v1.6.3:deeptutor/visualizers/store.py:128-171`）
- user uninstall 删除包目录并清理状态残项；运行时可用性的主事实仍是目录是否可发现。（`v1.6.3:deeptutor/visualizers/store.py:173-190`）

所以 v0905 的“39 包全 enabled”按此代码只能是**扫描正确包根后动态计算出的状态**，不是启动过程把 39 个 ID 回填进 JSON；空的 `disabled[]` 会让所有已扫描包 enabled。（`v1.6.3:deeptutor/visualizers/registry.py:38-57`）

## KP 清单与运行时 catalog 不是同一事实

当前 fork 的 KP map 直接返回学习进度中保存的 `kp.visualizers` ID，不查询 Registry。（`deeptutor/api/routers/mastery_path.py:618-632`；`deeptutor/learning/policy.py:291-321`）前端徽标也只展示这些 ID 的数量和 title。（`web/lib/learning-api.ts:117-126`；`web/components/space/learning/StudyOutline.tsx:137-183`）因此 KP 页能显示绑定 ID，不能证明相应包已安装；必须与 `/api/visualizers/list` 的 installed+enabled 集合取交集。（`v1.6.3:deeptutor/api/routers/visualizers.py:39-45`）

## 版本锚点

`v1.6.1` 尚无 `deeptutor/visualizers/registry.py`；`v1.6.2` 的 release commit `3dc372f55` 首次加入整套 visualizer 目录、API 与测试。该版已经使用 `<user root>/visualizers` 扫描，并把扫描到的 user 包直接计为 installed。（`v1.6.2:deeptutor/visualizers/store.py:38-53,108-128`；`v1.6.2:deeptutor/visualizers/registry.py:13-52`）

`v1.6.2 → v1.6.3` 没有改变这两项语义；`v1.6.3` 的对应实现仍是同一路径和同一合并规则。（`v1.6.3:deeptutor/visualizers/store.py:38-51,106-126`；`v1.6.3:deeptutor/visualizers/registry.py:13-50`）可机械复核：

```bash
git cat-file -e v1.6.1:deeptutor/visualizers/registry.py
git log --oneline v1.6.1..v1.6.2 -- deeptutor/visualizers deeptutor/api/routers/visualizers.py
git show --stat v1.6.2 -- deeptutor/visualizers deeptutor/api/routers/visualizers.py
git diff -U5 v1.6.2..v1.6.3 -- deeptutor/visualizers/store.py deeptutor/visualizers/registry.py
git diff --exit-code v1.6.3 -- deeptutor/visualizers deeptutor/services/path_service.py deeptutor/runtime/home.py deeptutor/api/routers/visualizers.py
```

## 修复建议

1. 修部署/mount 目标：单用户默认布局应把 39 包交付到 `/opt/yuedu/data/user/visualizers/<id>/`，每包保留 `visualizer.json` 与其 `renderer_entry`；不要伪造 `visualizers.json.installed`。（`v1.6.3:deeptutor/visualizers/store.py:50-51,106-126,192-207`）
2. 若启用多用户，应按实际用户的 PathService root 安装，不能共享回退到顶层 `/data/visualizers`，否则会破坏 per-user 隔离设计。（`v1.6.3:deeptutor/services/path_service.py:61-67`；`v1.6.3:deeptutor/visualizers/registry.py:149-152`）
3. 本任务不提供代码 diff：当前代码的路径与 per-user 语义自 `v1.6.2` 起一致；增加 `/data/visualizers` fallback 会跨过用户边界。应修部署数据落点，ZC 先用下列只读命令确认后再单独审批数据操作。（`v1.6.2:deeptutor/visualizers/store.py:38-53`；`v1.6.3:deeptutor/visualizers/store.py:38-51`）

## HK 只读验证命令

若生产启用认证，给两条 curl 追加现有登录态 Cookie 或 `Authorization` header；两个 router 均受统一认证依赖保护。（`deeptutor/api/main.py:560-565,664-669`）

1. 比较“当前部署目录”和“源码实际扫描目录”的候选 manifest 数；正确结果应是后者 39。（`v1.6.3:deeptutor/visualizers/store.py:50,106-126`）

```bash
for d in /opt/yuedu/data/visualizers /opt/yuedu/data/user/visualizers; do printf '%s\t' "$d"; find -L "$d" -mindepth 2 -maxdepth 2 -type f -name visualizer.json 2>/dev/null | wc -l; done
```

2. 查询运行时真实 catalog；通过标准是 `yuedu_count=39` 且 `not_ready=[]`。（`v1.6.3:deeptutor/api/routers/visualizers.py:39-45`；`v1.6.3:deeptutor/visualizers/registry.py:72-80`）

```bash
curl -fsS http://127.0.0.1:8001/api/visualizers/list | jq '{yuedu_count: ([.visualizers[] | select(.id | startswith("yuedu_"))] | length), not_ready: [.visualizers[] | select((.id | startswith("yuedu_")) and ((.installed | not) or (.enabled | not))) | {id,origin,installed,enabled}]}'
```

3. 将 KP 页返回的绑定 ID 与运行时可用集做差；通过标准是 `missing_or_disabled=[]`。（`deeptutor/api/routers/mastery_path.py:618-632`；`deeptutor/learning/policy.py:305-321`；`v1.6.3:deeptutor/visualizers/registry.py:55-57`）

```bash
BOOK_ID='替换为生产book_id'; jq -n --slurpfile kp <(curl -fsS "http://127.0.0.1:8001/api/mastery-paths/progress/${BOOK_ID}/map") --slurpfile catalog <(curl -fsS http://127.0.0.1:8001/api/visualizers/list) '[$kp[0].map.modules[].knowledge_points[].visualizers[]?] | unique as $bound | [$catalog[0].visualizers[] | select(.installed and .enabled) | .id] as $ready | {bound:$bound, missing_or_disabled:($bound-$ready)}'
```

本地只读回归：`.venv/bin/pytest -q tests/visualizers/test_registry_and_tool.py tests/api/test_visualizers_router.py`，结果为 `5 passed`；相关生命周期断言见 `tests/visualizers/test_registry_and_tool.py:56-80` 与 `tests/api/test_visualizers_router.py:58-88`。
