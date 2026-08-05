# 紫鸟联系达人自动化开发交接

更新时间：2026-07-28
项目目录：`/Users/kr/Documents/work/project/E-commerce Agent`
当前分支：`feature/ziniao-control-automation`

## 给新会话的首条指令

可将下面内容直接复制给新会话：

```text
请接手紫鸟联系达人自动化的后续开发。

先完整阅读：
/Users/kr/Documents/work/project/E-commerce Agent/docs/ziniao-contact-handoff.md

继续使用当前分支 feature/ziniao-control-automation，保留现有未提交改动，不要重新实现
已经完成的 Selenium、MCP 和 OpenCode/DeepSeek 流程。先执行只读检查：
1. git status --short
2. 检查是否仍有 contact-creator --keep-open 进程
3. 检查 127.0.0.1:16851 紫鸟 WebDriver 服务
4. 确认 opencode mcp list 中 ziniao-contact 为 connected

当前状态：@delaneykreusel 的已批准招呼语和“金色拉链+短裤13”邀请均已成功发送。
当前流程分为逐达人邀请和批量合作卡片两个阶段；禁止重复发送消息、邀请或卡片。
第三个商品 Top 3 的真实联系测试尚不能标记为完成；只可在用户当前明确授权的范围内
执行并逐个验收。紫鸟密码、Cookie、令牌和 DeepSeek Key 不得进入源码、命令参数、
提示词或日志。

阅读完交接文档后，根据我的下一条需求继续开发。
```

## 强制运行模式

紫鸟只能以现有 WebDriver 主进程模式打开：

```text
--run_type=web_driver --ipc_type=http --port=16851
```

禁止使用普通紫鸟工作台启动店铺，禁止直接重启店铺 Chromium，禁止使用普通浏览器或
人工点击结果替代 Agent 的强终态证据。macOS 进程管理器会同时校验 WebDriver HTTP
端口与主进程参数；模式不匹配时必须失败关闭。若 WebDriver 主进程没有可附加的店铺
调试端口缓存，或缓存的端口与 DevTools browser UUID 验证失败，才通过
`startBrowser` 恢复，不能降级到普通模式。缓存命中时直接附加已有店铺浏览器。

## 当前完成情况

已完成并真实验收以下可见 Selenium 流程：

1. 从 TikTok Shop 卖家首页点击“联盟”，再点击“寻找达人”。
2. 在达人搜索框输入 `@delaneykreusel`。
3. 点击输入框下方的精确候选项 `delaneykreusel / Delaney`。
4. 下滑并确认同一页面出现目标达人搜索结果卡片。
5. 点击搜索结果卡片中的目标达人，验证详情在新标签页打开。
6. 点击详情页私信图标，验证聊天抽屉打开。
7. 如果聊天抽屉未默认选中会话，则点击抽屉中的 `delaneykreusel` 联系人。
8. 点击“在新标签页打开聊天”，验证独立聊天页面。

最终验收状态：

- 详情页标题：`delaneykreusel | TikTok Shop Affiliate`
- 聊天页标题：`Cooperation Chat`
- 聊天页路径：`/seller/im?creator_id=7493994012378827459...`
- 目标达人可见：是
- 聊天输入区可见：是
- 聊天对象双重校验：通过（用户名 + `creator_id`）
- 已批准招呼语：已发送一次，完整消息气泡可见
- 定向合作邀请：`金色拉链+短裤13`
- 邀请创建：成功
- 定向合作级 `invitationGroupId`：`7664550207413847821`
- 邀请阶段标准：最终按钮点击一次并完成详情/聊天标签清理
- 联系终态标准：从精确项目已接受达人列表发送卡片并取得服务端送达证据

测试店铺：

- `browserId`：`27850427216664`
- 店铺名称：`vaelos子账号`
- 紫鸟内核：`146.1.4.29`

不要把企业账号或密码补充到本文档。

任务仍以共享的 `invitationGroupId` 精确绑定定向合作选项。最终邀请步骤不获取或保存
达人专属 `invitationId`。

当前用户指定的招呼语快照为：

```text
Hi We’re obsessed with your content 🥰
Your aesthetic goes so well with VAELOS activewear ✨
We’re a reliable brand selling soft, stretchy yoga & gym wear 🩳🧘‍♀️
We sent you an official collab invite: free samples + high commission 🎁💰
Accept it in your TikTok dashboard to get your products ASAP 🚀
Let’s partner long term and make great content together! 🤩
```

后续任务应从 Django 招呼语模板选择并冻结文案，再通过 `--greeting-message` 传给
Agent；不要依赖工具名或代码常量来隐式决定发送内容。

## 用户确认的准确搜索交互

搜索流程不能省略中间步骤，准确顺序为：

```text
输入达人姓名
  → 点击输入框下方弹出的候选项
  → 在同一页面下滑到“搜索结果”
  → 点击目标达人结果卡片
  → 新标签打开达人详情
```

特别注意：

- 下拉候选项用于触发搜索。
- 达人详情必须通过搜索结果卡片进入。
- 不要把“点击下拉候选项”误认为“已经点击达人结果卡片”。

## 主要实现

### Selenium 工作流

文件：

`ziniao-automation/src/ziniao_automation/actions/creator_contact.py`

核心类：

- `CreatorContactWorkflow`
- `WorkflowStepResult`

公开步骤：

- `open_find_creators()`
- `search_creator(creator)`
- `open_creator_detail(creator)`
- `open_message_panel(creator)`
- `open_chat_in_new_tab(creator)`
- `verify_chat_recipient(creator, creator_id=None)`
- `send_greeting(creator, creator_id, greeting_message, ...)`
- `open_target_collaboration(creator, creator_id)`
- `open_other_invitation_dialog(creator, creator_id)`
- `select_invitation(creator, creator_id, invitation_name)`
- `send_selected_invitation(creator, creator_id, invitation_name, ...)`
- `send_collaboration_card(creator, creator_id, invitation_name, invitation_id, ...)`

每个方法都会：

- 检查前置页面状态；
- 执行一个业务步骤；
- 等待页面真实变化；
- 返回结构化证据；
- 验收失败时抛出 `ZiniaoWorkflowError`；
- 必要时保存失败截图到 `temporary/ziniao-contact/`。

招呼语由任务参数动态传入，发送前核对非空、2000 字符上限及 SHA-256；兼容常量只提供
CLI 默认值，不能作为服务端任务文案的唯一来源。聊天 `creator_id` 以新聊天页 URL
动态发现为准；调用方传入 ID 时只用于额外强断言。

关键 DOM 定位：

- 搜索框：`input.core-input`
- 搜索下拉项：`role="menuitem"` 且包含精确用户名
- 搜索结果：包含精确用户名的可见 `tr`
- 私信按钮：`button:has(svg.alliance-icon-Message)`
- 新标签聊天按钮：`button:has(svg.arco-icon-launch)`
- 消息输入区：`textarea`、`contenteditable=true` 或消息占位输入框

聊天抽屉存在两种状态：

1. 自动选中目标会话，直接出现 `textarea[placeholder="发送消息"]`；
2. 只打开联系人列表，右侧提示先开始聊天。

第二种状态已经处理：脚本会点击抽屉右侧区域中的精确目标联系人，再等待输入区出现。

最终邀请步骤不再扫描可能短暂出现的 toast；最终按钮点击一次后立即关闭当前达人详情
和聊天标签，并返回可复用的查找达人页。全部邀请完成后进入单进程批量合作卡片阶段。

### 紫鸟连接层

目录：

`ziniao-automation/src/ziniao_automation/`

主要文件：

- `client.py`：紫鸟本地 HTTP API
- `process.py`：紫鸟 V6 WebDriver 模式进程
- `drivers.py`：匹配并校验 ChromeDriver
- `browser_session_cache.py`：跨进程店铺调试端口、browser UUID 和店铺锁
- `session.py`：Selenium 店铺会话生命周期
- `config.py`：环境变量配置
- `errors.py`：类型化错误
- `cli.py`：本地调试 CLI

官方连接顺序：

```text
updateCore
  → getBrowserList
  → 验证缓存的 debuggingPort + DevTools browser UUID
  → 缓存命中则直接附加；失效时才 startBrowser
  → Selenium debuggerAddress
  → 仅断开本次 ChromeDriver
```

已修复 Ctrl+C 时 ChromeDriver 先断开导致清理异常覆盖原始中断的问题。

### MCP

文件：

`ziniao-automation/src/ziniao_automation/mcp_server.py`

本地 MCP 名称：`ziniao-contact`

工具固定顺序：

1. `ziniao_connect`
2. `ziniao_open_find_creators`
3. `ziniao_search_creator`
4. `ziniao_open_creator_detail`
5. `ziniao_open_message_panel`
6. `ziniao_open_chat_new_tab`
7. `ziniao_verify_chat_recipient`
8. `ziniao_send_greeting`
9. `ziniao_open_target_collaboration`
10. `ziniao_open_other_invitation_dialog`
11. `ziniao_select_invitation`
12. `ziniao_send_selected_invitation`
13. `ziniao_disconnect`

MCP 会强制调用顺序、固定同一目标达人，并缓存已成功步骤，避免 Agent 重复执行。
发生失败时允许安全调用 `ziniao_disconnect`。

两个远端写工具都要求显式布尔确认：

- `ziniao_send_greeting`：
  `confirmSendGreeting=true`
- `ziniao_send_selected_invitation`：
  `confirmSendInvitation=true`

`ziniao_send_greeting` 还要求任务传入完整 `greetingMessage` 和对应
`greetingSha256`，MCP 不得改写文案。发送邀请前会重新核验对象、前置步骤、目标单选框
和最终按钮；点击调用成功返回后立即清理当前达人标签，不等待瞬时 DOM 文案或右侧
合作卡片同步。
邀请阶段不获取达人专属 `invitationId`；该 ID 由后续卡片消息终态证据提供。

启动脚本：

`scripts/start-ziniao-contact-mcp-macos.sh`

配置：

`opencode.json`

在交接时执行 `opencode mcp list`，`ziniao-contact` 状态为 `connected`。

### OpenCode + DeepSeek Agent

模型：

`deepseek/deepseek-v4-flash`

文件：

- `ziniao-automation/src/ziniao_automation/agent_runner.py`
- `prompts/ziniao-contact-rules.md`
- `scripts/run-ziniao-contact-agent-macos.sh`

历史只读 Agent 验收已成功。OpenCode Session：

`ses_06264a117ffeKMSlzF221j3i3f`

该 Session 只覆盖当时的“连接至打开聊天并断开”7 个工具，不代表后来新增的招呼语和
邀请创建步骤已经由同一 Session 跑过。该只读范围内所有调用均返回：

```json
{
  "success": true,
  "status": "SUCCESS"
}
```

当前 Agent 提示会把任务招呼语作为数据原样传入，并附带 SHA-256；业务
`--through-step` 最大为 11，对应业务流程第 12 步。邀请完成不能采信模型最终文本，
只能采信 `ziniao_send_selected_invitation` 返回的按钮点击/幂等证据和标签清理证据。

上一阶段只读 Agent 结果确认：

```json
{
  "success": true,
  "status": "SUCCESS",
  "creator": "@delaneykreusel",
  "completedSteps": [
    "connect",
    "open-find-creators",
    "search-creator",
    "open-creator-detail",
    "open-message-panel",
    "open-chat-new-tab",
    "disconnect"
  ],
  "messageEntered": false,
  "messageSent": false
}
```

### Django 达人联系模块

应用目录：

`web-ui/creator_contact/`

入口：

`http://127.0.0.1:8000/creator-contact/`

已实现的持久化对象：

- `GreetingTemplate`：保存、复用和设置默认招呼语，内容上限 2000 字符并记录 SHA-256；
- `DirectedCollaborationOption`：按店铺缓存“进行中”定向合作，主键语义为
  `invitationGroupId`；
- `CreatorContactTask`：冻结导入批次、选择规则、人数、招呼语、定向合作和三个写操作确认；
- `CreatorContactTarget`：冻结每位达人的排名、`@handle`、7/30 天及总销售额和执行证据；
- `CreatorContactTaskStep`：持久化每个 MCP 工具步骤的安全输入输出摘要；
- `ContactedCreator`：店铺级定向邀请去重表；
- `CollaborationSyncJob`：定向合作只读同步队列。

候选达人从已确认的达人导入批次中选择，支持按达人 ID 自动选择表格候选人（最多
50 位）、近 7 天/近 30 天/总销售额倒序排名，以及手动勾选。销售额相同或手动展示时按导入行号和达人 ID
稳定排序。handle 会移除 `@` 并大小写归一；同店铺
`ContactedCreator` 中已经完成定向邀请按钮点击/幂等确认的达人会在截取 Top N
**之前**排除。任务创建时立即冻结最终目标，Worker 串行处理；另一任务已邀请的目标
会在执行前再次跳过，不区分商品或合作项目。

定向合作同步入口：

```bash
PYTHONPATH=ziniao-automation/src \
.venv/bin/python -m ziniao_automation.collaboration_sync_runner \
  --store-id '<精确店铺ID>' \
  --json
```

输出结构为 `{success, storeId, options, errorCode, errorMessage}`。Django 页面创建同步
任务后，由独立 Worker 领取：

```bash
.venv/bin/python web-ui/manage.py run_collaboration_sync_worker
# 调试时只处理一个任务
.venv/bin/python web-ui/manage.py run_collaboration_sync_worker --once
```

联系任务也由独立 Worker 领取：

```bash
.venv/bin/python web-ui/manage.py run_creator_contact_worker --server-mode
# 调试时只处理一个任务
.venv/bin/python web-ui/manage.py run_creator_contact_worker --once
```

常驻模式固定监听 `127.0.0.1:16852/health`。Django 启动任务前先验证该健康响应，存在
时不再创建 Worker，不存在时才启动。紫鸟 WebDriver 主进程固定监听 `16851`；每个执行
子进程结束时仅停止本地 ChromeDriver 服务，保留店铺浏览器和紫鸟主进程，用户主动退出
时才关闭。

默认联系执行器为每个冻结目标调用
`python -m ziniao_automation.agent_runner --through-step 11`，并传入招呼语任务快照和
两个确认参数。后台进程不会进入凭据交互提示：缺少环境变量时会明确失败。

邀请中间阶段不是 Agent 最终回答，而是工具事件同时证明：

- 招呼语已发送；
- `invitationButtonClicked=true` 或 `alreadySent=true`；
- `creatorDetailTargetGone=true`；
- `creatorTabsClosed=true`；
- `searchTabKept=true`；
- `returnedToFindCreators=true`；
- `findCreatorsSearchReady=true`。

满足上述条件会记录 `INVITATION_COMPLETED` 并立即创建或更新店铺级
`ContactedCreator`。全部邀请完成后，
`accepted_card_runner --creators-json` 在一个进程、一个店铺浏览器和一个项目页中循环
处理所有达人；每位达人必须精确存在于目标项目列表、聊天对象匹配，并在卡片发送后取得
React 服务端消息 ID 与送达状态。这组终态证据只决定任务是否最终成功；找不到达人或
证据不完整时保留邀请已完成状态，且该达人仍保持全局排除。

当前真实 Top 3 测试状态：**尚未在本文档更新时宣告完成**。数据快照中的目标商品是
最近一次成功采集任务的第三个商品（数据库商品 ID `13`，
external ID `1731795774080652206`）；当时按近 30 天销售额排序的前三个 handle 为
`@isnt_ellie`、`@sillyalphaboy`、`@littalymillie`。执行前仍须通过服务端候选预览、
店铺级去重和用户授权重新确认，不能把候选列表或单元测试当作真实联系成功。

已创建的测试任务 ID 为 `2abbd920-d436-4a4a-87a8-7d34c1f7a8d5`。此前浏览器崩溃
尝试均未产生远端写入：三个目标的 `message_sent`、`invitation_created` 仍全部为
`false`，`ContactedCreator` 中也没有
这三个 handle。当前 WebDriver 主进程已恢复并通过模式校验，但官方 `startBrowser`
仍要求本地提供 `ZINIAO_COMPANY`、`ZINIAO_USERNAME`、`ZINIAO_PASSWORD`；在凭据仅
通过本地环境安全注入前，不得降级普通模式或宣告 Top 3 成功。

## 启动方式

### 直接运行 Selenium 并保持窗口

```bash
cd "/Users/kr/Documents/work/project/E-commerce Agent"

PYTHONPATH=ziniao-automation/src \
.venv/bin/python -m ziniao_automation \
  --prompt contact-creator \
  --store-id 27850427216664 \
  --creator @delaneykreusel \
  --through-step 5 \
  --keep-open
```

`--through-step` 可取 `1` 至 `12`。默认仍为安全的第 5 步；第 6 步会从聊天 URL
动态发现 `creator_id`，可选 `--creator-id` 只用于精确断言。第 7 步及以后必须提供
`--confirm-send-greeting`，第 11 步还必须提供 `--confirm-send-invitation`，第 12 步
必须再提供 `--confirm-send-card`。服务端任务还会显式传入 `--greeting-message`。
不得为已完成的 `@delaneykreusel` 再次运行任何写步骤。

完整写流程仅可对用户明确授权的精确目标使用：

```bash
PYTHONPATH=ziniao-automation/src \
.venv/bin/python -m ziniao_automation \
  --prompt contact-creator \
  --store-id '<精确店铺ID>' \
  --creator '@精确达人handle' \
  --greeting-message '<任务快照中的完整招呼语>' \
  --invitation-name '<精确定向合作名称>' \
  --through-step 12 \
  --confirm-send-greeting \
  --confirm-send-invitation \
  --confirm-send-card
```

### 通过 OpenCode + DeepSeek 执行

```bash
cd "/Users/kr/Documents/work/project/E-commerce Agent"

./scripts/run-ziniao-contact-agent-macos.sh \
  --store-id 27850427216664 \
  --creator @delaneykreusel \
  --keep-open
```

执行过程：

1. 本地安全提示输入紫鸟凭据；
2. OpenCode 使用 DeepSeek 按 `--through-step` 调用 MCP 工具；
3. Agent 完成后调用 `ziniao_disconnect`；
4. `--keep-open` 会用相同 Selenium 流程重新打开最终聊天页并持续保持。

## 当前持续调试会话

早期交接时曾存在 `contact-creator --keep-open` 进程（当时 PID `75652`），该信息
已经是历史记录，不能据此假设当前仍有会话。新会话必须只读查询：

```bash
ps -axo pid=,command= |
  rg 'ziniao_automation.*contact-creator.*--keep-open' |
  rg -v 'rg '
```

如果只需要观察当前页面，不要启动第二个店铺会话。

也要检查紫鸟客户端 WebDriver 端口和当前浏览器调试端口；端口、PID 都是运行时状态，
不要写死。若代码修改后必须重启，先根据只读结果确认精确 PID，再安全发送中断：

```bash
kill -INT <已确认的精确PID>
```

不要使用模糊 `pkill`，也不要强制杀死整个紫鸟客户端。

## 凭据与安全

本次没有把真实紫鸟凭据写入源码、文档、命令参数或 Git。

配置占位位于：

`.env.example`

本地可用变量：

- `ZINIAO_COMPANY`
- `ZINIAO_USERNAME`
- `ZINIAO_PASSWORD`
- `ZINIAO_CONTACT_STORE_ID`
- `ZINIAO_CLIENT_PATH`
- `ZINIAO_SOCKET_PORT`
- `ZINIAO_REQUEST_TIMEOUT_SECONDS`
- `ZINIAO_CORE_TIMEOUT_SECONDS`
- `ZINIAO_DRIVER_DIR`
- `CREATOR_CONTACT_WORKER_HOST`
- `CREATOR_CONTACT_WORKER_PORT`
- `CREATOR_CONTACT_WORKER_START_TIMEOUT_SECONDS`
- `DEEPSEEK_API_KEY`

交互式 `agent_runner.py` 在变量缺失时可在本地终端提示输入，其中密码使用隐藏输入。
Django 的联系和同步 Worker 必须非交互运行：缺少必要环境变量时会失败并记录脱敏错误，
不会等待终端输入。凭据只通过子进程环境传给 MCP。

继续开发时必须遵守：

- 不输出密码、Cookie、令牌或 API Key；
- 不把真实值写入测试、日志、提示词或提交；
- 未经用户明确授权，不输入或发送私信；
- 未经用户明确授权，不邀请达人；
- 不得向 `@delaneykreusel` 重复发送本次招呼语或同一邀请；
- 批量范围只能是用户明确选择并确认的导入批次 Top N，不得扩展到其他批次或达人；
- 每个目标仍须独立通过强终态验收，失败或不明确时不得盲目重试；
- 不修改非目标店铺数据。

## 测试与验收

旧阶段的仓库级回归曾全部通过，但之后已经新增 Django 模块、定向合作同步器和第 12 步；
不要沿用旧的固定测试数量。最终交付前应重新运行下面的完整命令，并以本次输出为准：

完整命令：

```bash
.venv/bin/python web-ui/manage.py check
.venv/bin/python web-ui/manage.py test tasks creator_contact mailing
.venv/bin/python web-ui/manage.py makemigrations --check --dry-run
PYTHONPATH=ziniao-automation/src \
  .venv/bin/python -m unittest discover -s ziniao-automation/tests -v
opencode mcp list
git diff --check
```

单元测试和 Django 测试只证明本地逻辑；它们不能替代真实第三商品 Top 3 的逐目标页面
验收，也不能据此写入 `ContactedCreator`。

## 当前 Git 状态

当前所有紫鸟相关改动仍未提交、未合并、未推送。

下面是早期交接时的历史快照，已经不完整：

```text
 M .env.example
 M README.md
 M opencode.json
 M requirements.txt
 D ziniao-automation/src/actions/.gitkeep
?? docs/ziniao-contact-handoff.md
?? prompts/ziniao-contact-rules.md
?? scripts/run-ziniao-contact-agent-macos.sh
?? scripts/start-ziniao-contact-mcp-macos.sh
?? ziniao-automation/README.md
?? ziniao-automation/src/ziniao_automation/
?? ziniao-automation/tests/
```

此后又新增了 `web-ui/creator_contact/`、定向合作同步动作与 runner，并修改 Django
settings/urls 和相关页面。新会话必须先执行 `git status --short` 获取实时状态；这些
改动属于当前任务，必须保留，不要 reset、checkout 或覆盖。

## 重要文件索引

```text
README.md
opencode.json
prompts/ziniao-contact-rules.md
scripts/run-ziniao-contact-agent-macos.sh
scripts/start-ziniao-contact-mcp-macos.sh
ziniao-automation/README.md
ziniao-automation/src/ziniao_automation/agent_runner.py
ziniao-automation/src/ziniao_automation/mcp_server.py
ziniao-automation/src/ziniao_automation/actions/creator_contact.py
ziniao-automation/src/ziniao_automation/actions/collaboration_sync.py
ziniao-automation/src/ziniao_automation/collaboration_sync_runner.py
ziniao-automation/src/ziniao_automation/cli.py
ziniao-automation/src/ziniao_automation/client.py
ziniao-automation/src/ziniao_automation/session.py
ziniao-automation/tests/test_creator_contact.py
ziniao-automation/tests/test_collaboration_sync.py
ziniao-automation/tests/test_mcp_server.py
ziniao-automation/tests/test_process.py
ziniao-automation/tests/test_visible_demo.py
web-ui/creator_contact/models.py
web-ui/creator_contact/forms.py
web-ui/creator_contact/views.py
web-ui/creator_contact/services/candidate_selector.py
web-ui/creator_contact/services/collaboration_sync.py
web-ui/creator_contact/services/contact_runner.py
web-ui/creator_contact/management/commands/run_collaboration_sync_worker.py
web-ui/creator_contact/management/commands/run_creator_contact_worker.py
```

## 后续开发建议

除非用户下一条需求另有指定，建议按以下顺序继续：

1. 先确认工作区、当前浏览器/紫鸟会话和 Worker 状态，并用进程管理器确认主进程确为
   WebDriver HTTP 模式；普通工作台模式必须直接失败。
2. 运行 Django 迁移、全部测试和静态检查，复核同步器合并后的最终代码。
3. 用只读同步任务获取目标店铺“进行中”定向合作，并核对
   `金色拉链+短裤13` 的 `invitationGroupId`。
4. 在 `/creator-contact/` 中复核第三个商品、Top 3 候选、招呼语快照、定向合作和三个
   写操作确认。
5. 仅在用户已授权的目标上串行完成邀请按钮点击与标签清理；失败或不明确时停止该目标，
   不盲点按钮。
6. 验证邀请按钮点击/幂等确认后立即写入 `ContactedCreator`；批量卡片阶段再精确核验
   项目成员、聊天对象和服务端送达证据，不得用卡片失败撤销邀请去重。
7. Windows 已增加单实例 Supervisor、进程组清理、精确紫鸟模式校验和常驻邮件
   Worker；仍需按 `docs/windows-runtime-acceptance.md` 在客户机完成实机验收。

不要自动执行 commit、merge 或 push，除非用户在新会话中明确要求。
