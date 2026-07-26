# 紫鸟 Selenium 自动化

本模块依据紫鸟官方 WebDriver 流程连接本地紫鸟 V6 客户端：

1. 以 `--run_type=web_driver --ipc_type=http --port=...` 启动客户端；
2. 调用 `updateCore`，等待店铺内核就绪；
3. 调用 `getBrowserList`，读取当前子账号已授权店铺；
4. 调用 `startBrowser`，获得 `debuggingPort` 和 `core_version`；
5. 使用内核自带或紫鸟官方 CDN 提供的匹配版本 ChromeDriver；
6. Selenium 通过 `127.0.0.1:{debuggingPort}` 附加到店铺窗口。

> 硬约束：本项目只能使用
> `--run_type=web_driver --ipc_type=http --port=...` 主进程打开紫鸟。禁止先启动普通
> 紫鸟工作台、从普通工作台点击“启动”打开店铺，禁止直接启动店铺 Chromium，也不能
> 用普通浏览器或人工操作替代自动化终态验收。macOS 连接前会同时校验本地 HTTP 端口
> 和主进程参数；任一不匹配都会失败关闭。

## 配置

将以下变量放入项目根目录的本地 `.env`，不要提交真实值：

```dotenv
ZINIAO_COMPANY=
ZINIAO_USERNAME=
ZINIAO_PASSWORD=
ZINIAO_CLIENT_PATH=/Applications/ziniao.app
ZINIAO_SOCKET_PORT=16851
ZINIAO_REQUEST_TIMEOUT_SECONDS=120
ZINIAO_CORE_TIMEOUT_SECONDS=600
ZINIAO_DRIVER_DIR=temporary/ziniao-webdrivers
ZINIAO_CONTACT_STORE_ID=
```

HTTP 单次超时不得低于 120 秒。驱动下载到 Git 忽略的 `temporary/` 目录，并按紫鸟
配置中的 SHA-1 校验。`ZINIAO_CONTACT_STORE_ID` 供 Django 达人联系页面选择目标
店铺；直接 CLI 仍以 `--store-id` 为准。凭据不会出现在 CLI 输出、异常信息或日志中。

## 命令

```bash
export PYTHONPATH="$PWD/ziniao-automation/src"

# 正常退出现有紫鸟主进程并以 WebDriver 模式重启，然后列店铺
.venv/bin/python -m ziniao_automation list --restart-client

# 唯一店铺可省略筛选；多个店铺必须指定 ID 或精确名称
.venv/bin/python -m ziniao_automation probe
.venv/bin/python -m ziniao_automation probe --store-id 123456
.venv/bin/python -m ziniao_automation probe --store-name "店铺名称"

# 打开可见店铺窗口，显示连接浮层并滚动，停留 45 秒供人工观察
.venv/bin/python -m ziniao_automation watch --duration 45

# 持续保持店铺窗口和 Selenium 会话，按 Ctrl+C 才关闭
.venv/bin/python -m ziniao_automation watch --keep-open
```

未配置 `.env` 时，可在本地终端临时使用 `--prompt` 交互输入。`probe` 只验证连接，
不会点击、提交或修改店铺数据，并在验证后结束 Selenium 会话和店铺窗口。`watch`
同样不修改远端数据，只增加临时本地浮层并滚动页面，演示结束后自动关闭测试会话。
使用 `--keep-open` 时，店铺窗口与 Selenium 会话会持续运行，适合人工观察和调试；
停止进程时会自动移除浮层并调用 `stopBrowser`。

## 联系达人调试流程

逐步执行到指定验收点，并保持窗口：

```bash
PYTHONPATH=ziniao-automation/src .venv/bin/python -m ziniao_automation \
  --prompt contact-creator \
  --store-id 27850427216664 \
  --creator @delaneykreusel \
  --through-step 5 \
  --keep-open
```

步骤 2 会输入用户名、点击下拉候选项并下滑验收搜索结果卡片；步骤 3 点击该结果
卡片进入详情；步骤 4 打开私信抽屉；步骤 5 在新标签页打开聊天。这五步不会输入或
发送消息。

后续步骤为：

6. 同时核验聊天页顶部用户名与 URL 中动态发现的 `creator_id`；如传入预期 ID，再做精确断言；
7. 查重后原样发送 `--greeting-message`，核验 SHA-256 和完整消息气泡；
8. 打开并验收“定向合作”页签；
9. 打开“发送其他邀请开展合作”弹窗，并再次核对目标达人；
10. 精确选择邀请，但不点击最终“邀请”；
11. 在所有前置步骤成功后点击一次最终“邀请”，取得该达人的实际 `invitationId`；
12. 刷新并复核右侧定向合作卡片，点击其“发送”一次，再验收合作计划卡片的强终态。

第 7、11 和 12 步是远端写操作，CLI 默认不会执行。必须分别提供显式确认：

```bash
# 只确认聊天对象，不写入远端
PYTHONPATH=ziniao-automation/src .venv/bin/python -m ziniao_automation \
  --prompt contact-creator \
  --store-id 27850427216664 \
  --creator @delaneykreusel \
  --through-step 6

# 完整发送流程；只能在用户已明确授权时使用
PYTHONPATH=ziniao-automation/src .venv/bin/python -m ziniao_automation \
  --prompt contact-creator \
  --store-id '<精确店铺ID>' \
  --creator '@目标达人' \
  --invitation-name '<精确定向合作名称>' \
  --greeting-message '<任务快照中的完整招呼语>' \
  --through-step 12 \
  --confirm-send-greeting \
  --confirm-send-invitation \
  --confirm-send-card
```

`--creator-id` 是可选的预期值；未提供时，工作流以新聊天页 URL 动态发现的
`creator_id` 为准，不能用商品关联达人数据中的其他 UID 猜测。

工作流会对相同招呼语、已创建邀请和已发送合作卡片执行幂等检查。右侧卡片中的 ID 是
定向合作级 `invitationGroupId`，第 11 步创建结果中的 ID 是该达人实际
`invitationId`，两者不能混用。第 12 步从 React 组件状态提取并交叉核对这两个 ID；
最终只接受以下证据：

- 消息类型为 `targetPlan`；
- `isFromMe=true`；
- 实际 `invitationId` 精确匹配；
- `flightStatus` 为 `3` 或 `4`；
- 存在非空 `serverId`；
- 该匹配消息的 UI 状态不是 `pending`、`sending`、`failed` 或 `error`。
- 达人详情页和 `Cooperation Chat` 标签均已关闭；
- 查找达人标签仍存在，并且已经切回该标签。

刷新后已有上述成功证据会直接按幂等成功返回，不点击按钮。如果尚无成功证据但已有
同目标的发送中、失败或不明确状态，流程会停止并保留证据，也不会盲目重试最终按钮。
列表预览、Toast、卡片消失或“定向合作 1”只能作为辅助证据，不能单独判定发送成功。
标签清理证据对应
`creatorTabsClosed=true`、`searchTabKept=true`、`returnedToFindCreators=true`；
任何一项缺失都不能写入店铺级已联系记录。

若点击最终邀请后页面明确提示添加成功，但刷新后右侧邀请卡片仍未同步，工作流会在
每次刷新前随机等待 3–5 秒，最多刷新 3 次。三次后仍不可见时返回
`skipCreator=true`，关闭本达人详情与聊天标签并继续下一位；不会继续发送合作卡片、
不会重复点击邀请，也不会写入 `ContactedCreator`。

使用 OpenCode + DeepSeek 逐步调用 `ziniao-contact` MCP，并在验收完成后重新打开
最终聊天页持续调试：

```bash
./scripts/run-ziniao-contact-agent-macos.sh \
  --store-id 27850427216664 \
  --creator @delaneykreusel \
  --keep-open
```

脚本只在本地终端提示输入紫鸟凭据；密码使用隐藏输入，并仅通过子进程环境传给
MCP，不进入命令参数、源码或 OpenCode 提示词。按 Ctrl+C 会安全关闭持续调试会话。
Agent 同样默认停在第 5 步；远端发送必须指定 `--through-step`、任务招呼语和对应
的显式确认参数。若调用方已有预期聊天 `creator_id`，可以额外传入进行强断言。

## MCP 工具与服务端调用

当前 `ziniao-contact` MCP 的业务工具依次为
`ziniao_open_find_creators`、`ziniao_search_creator`、
`ziniao_open_creator_detail`、`ziniao_open_message_panel`、
`ziniao_open_chat_new_tab`、`ziniao_verify_chat_recipient`、
`ziniao_send_greeting`、`ziniao_open_target_collaboration`、
`ziniao_open_other_invitation_dialog`、`ziniao_select_invitation`、
`ziniao_send_selected_invitation` 和 `ziniao_send_collaboration_card`；
前后另有 `ziniao_connect`、`ziniao_disconnect`。

`ziniao_send_greeting` 接收 `greetingMessage`、`greetingSha256` 和
`confirmSendGreeting=true`，不再只允许某一段硬编码文案。最终
`ziniao_send_collaboration_card` 必须携带第 11 步已经验收的实际 `invitationId`，
并要求 `confirmSendCard=true`。

Django 定向合作同步 Worker 调用只读入口：

```bash
PYTHONPATH=ziniao-automation/src .venv/bin/python \
  -m ziniao_automation.collaboration_sync_runner \
  --store-id '<精确店铺ID>' \
  --json
```

成功输出顶层 `{success, storeId, options, errorCode, errorMessage}`；`options` 只包含
从联盟“定向合作”页读取并验收的进行中选项。该入口不交互提示凭据，生产 Worker 必须
通过环境变量提供 `ZINIAO_COMPANY`、`ZINIAO_USERNAME` 和 `ZINIAO_PASSWORD`。

## 作为 Python 模块使用

```python
from ziniao_automation.client import ZiniaoClient
from ziniao_automation.config import ZiniaoSettings
from ziniao_automation.session import SeleniumStoreSession

settings = ZiniaoSettings.from_env()
client = ZiniaoClient(settings)
client.update_core()
store = client.list_stores()[0]

with SeleniumStoreSession(client, settings, store) as session:
    driver = session.driver
    # 在此实现具体 Selenium 自动化动作。
```

业务脚本应放在 `ziniao-automation/src/ziniao_automation/actions/`，并遵循最小权限、
显式店铺选择和可重试但不重复提交的原则。
