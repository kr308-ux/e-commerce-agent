# E-commerce Agent

面向电商运营场景的 Browser Agent。当前功能“获取达人数据”由 Django 创建任务，
独立 Agent Worker 使用 OpenCode + DeepSeek 调用 Chrome Data MCP，并将商品及
关联达人导出数据写入 SQLite。

## 获取达人数据流程

1. Django 创建任务，可选择 10、20 … 100 个商品；
2. DeepSeek V4 Flash 通过 OpenCode 逐步调用 Chrome MCP；
3. Chrome 打开出海匠商品搜索并筛选“运动与户外 → 运动服饰”；
4. 跨分页采集指定数量的商品名称、总销量、近 7 天销售额、总销售额和关联达人数；
5. 逐个点击商品，在新标签页打开详情并定位“关联达人”；
6. 点击导出并选择“100 条”，下载 XLSX；
7. Worker 解析 XLSX，将达人指标和社交联系方式关联到对应商品；
8. Django 任务页展示 MCP 步骤、商品和达人结果。

Django HTTP 请求不直接执行浏览器操作。任务必须由独立
`run_creator_worker` 进程领取。

## 数据表

- `tasks_creatoracquisitiontask`：获取达人数据任务、进度和错误状态；
- `tasks_taskstep`：每个 DeepSeek/MCP 步骤的结构化验收记录；
- `tasks_product`：商品名称、总销量、近 7 天销售额、总销售额、关联达人数；
- `tasks_relatedcreator`：商品关联达人及 XLSX 中的销售、粉丝、互动、社交信息；
- `tasks_creatorexportartifact`：导出文件路径、哈希、大小和导入行数。

达人联系模块位于 `web-ui/creator_contact/`，另外持久化招呼语模板、进行中的定向合作、
联系任务及冻结目标、逐步证据、店铺级已联系记录和定向合作同步任务。已联系记录只会在
指定定向邀请按钮点击成功（或确认页面已有同一邀请）后写入；同一店铺下无论商品或
合作项目都会排除该达人。合作卡片服务端送达证据仍用于任务终态验收。

## 首次准备

macOS：

```bash
./scripts/setup-macos.sh
./scripts/start-chrome-cdp-macos.sh
```

Windows 10+：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start-chrome-cdp-windows.ps1
```

在独立 CDP Chrome 中登录出海匠。MCP 不读取或记录 Cookie、密码和令牌。
本地 `.env` 需要配置 `DEEPSEEK_API_KEY`；真实密钥不得提交。

## 紫鸟店铺 Selenium 连接

紫鸟自动化模块使用官方本地 WebDriver HTTP 接口，先执行
`updateCore → getBrowserList`，再优先验证并附加缓存的店铺调试端口；仅在缓存失效时
调用 `startBrowser`，随后建立 Selenium 会话。
企业名称、子账号和密码只从本地 `.env` 读取，不写入源码、命令参数或业务日志。

macOS 首次使用：

```bash
./scripts/setup-macos.sh
if [ ! -f .env ]; then cp .env.example .env; fi
# 手动补充本地 .env 中的 ZINIAO_* 配置
PYTHONPATH=ziniao-automation/src .venv/bin/python -m ziniao_automation --prompt list --restart-client
PYTHONPATH=ziniao-automation/src .venv/bin/python -m ziniao_automation --prompt probe
```

`list` 只列出当前子账号获授权的店铺；`probe` 启动唯一店铺、通过匹配内核版本的
ChromeDriver 建立 Selenium 会话、读取页面标题和地址后安全关闭测试会话。模块使用方法和
安全边界详见 `ziniao-automation/README.md`。

联系达人流程可由 OpenCode + DeepSeek 调用 `ziniao-contact` MCP，严格执行
“联盟 → 寻找达人 → 下拉候选项 → 搜索结果卡片 → 私信抽屉 → 新标签聊天”，每步
返回结构化验收结果。默认只执行前 5 步，不输入或发送消息：

```bash
./scripts/run-ziniao-contact-agent-macos.sh \
  --store-id 27850427216664 \
  --creator @delaneykreusel \
  --keep-open
```

经用户明确授权后，流程还支持“校验聊天对象 → 原样发送任务招呼语 → 打开定向合作 →
选择并创建精确邀请”。招呼语来自 `--greeting-message` 或 Django 任务快照，不再依赖
代码中的固定文案。发送招呼语和最终邀请分别要求显式确认。

最终邀请步骤不获取达人专属 `invitationId`；点击一次按钮即完成第一阶段，不再扫描会
瞬间消失的 toast。随后关闭该达人详情和聊天标签并保留查找达人页；所有达人邀请完成后，
由一个批量进程进入精确项目的已接受达人列表发送合作卡片。
完整参数见 `ziniao-automation/README.md`。

运行时硬约束：紫鸟只能以
`--run_type=web_driver --ipc_type=http --port=16851` 主进程打开。该端口已就绪时直接
复用；每个店铺的调试端口和 DevTools browser UUID 会跨 Agent 进程缓存。下一位达人
会附加同一个浏览器，并通过 CDP 激活上一次保留的查找达人页。任务结束只断开
ChromeDriver，不关闭店铺浏览器或紫鸟主进程；只有用户主动退出时才关闭。普通紫鸟工作台、
从工作台启动的店铺 Chromium、普通浏览器和人工点击结果均不能作为联系任务的执行或
验收路径。

## 达人联系模块

先应用数据库迁移，再访问 <http://127.0.0.1:8000/creator-contact/>：

```bash
.venv/bin/python web-ui/manage.py migrate
```

页面可以保存并复用
招呼语，发起店铺定向合作同步，选择已成功“获取达人数据”任务中的商品，并按近 30 天
销售额、近 7 天销售额降序预览前 N 位尚未联系的达人。创建任务时会冻结招呼语、
定向合作和达人列表，避免执行期间数据变化。

`.env` 至少需要配置目标店铺和非交互 Worker 所需凭据：

```dotenv
ZINIAO_CONTACT_STORE_ID=
ZINIAO_COMPANY=
ZINIAO_USERNAME=
ZINIAO_PASSWORD=
DEEPSEEK_API_KEY=
```

定向合作同步可使用独立 Worker；达人联系任务既可由 Worker 轮询，也可在任务详情页
点击“从 Django 启动测试”。Django 会复用监听
`127.0.0.1:16852/health` 的常驻联系 Worker；仅在健康端口不存在时启动一次：

```bash
.venv/bin/python web-ui/manage.py run_collaboration_sync_worker
.venv/bin/python web-ui/manage.py run_creator_contact_worker --server-mode
```

定向合作同步 Worker 只读取“进行中”选项。联系 Worker 串行处理任务冻结的达人，
遇到同店铺已成功联系的规范化 `@handle` 会跳过；搜索结果没有精确同名账号也会立即
安全跳过。邀请阶段完成全部达人后，卡片阶段只打开一次目标项目和“达人详情”，并在
同一个 WebDriver 会话中依次发送；邀请按钮点击成功时即写入店铺级防重复表。
邀请阶段不再扫描瞬时 DOM 提示；批量卡片阶段会逐个精确核验项目成员、聊天对象和
合作卡片。找不到目标达人时保留“邀请已完成、卡片待处理”状态，但不会再次入选邀请。
当前仓库中的第三个商品
Top 3 真实联系属于单独的显式授权测试，不能仅因创建模块或通过单元测试就视为已经
执行成功。

## 启动服务

macOS 使用两个终端：

```bash
.venv/bin/python web-ui/manage.py runserver
./scripts/start-worker-macos.sh
```

Windows 使用两个终端：

```powershell
.\scripts\start-windows.ps1
.\scripts\start-worker-windows.ps1
```

打开 <http://127.0.0.1:8000/>，选择商品数量并创建“获取达人数据”任务。

## 验证

```bash
npm run build
npm run check:data-acquisition
npm test
.venv/bin/python web-ui/manage.py check
.venv/bin/python web-ui/manage.py test tasks creator_contact
PYTHONPATH=ziniao-automation/src \
  .venv/bin/python -m unittest discover -s ziniao-automation/tests -v
.venv/bin/python web-ui/manage.py makemigrations --check --dry-run
git diff --check
```

真实浏览器检查需要 CDP Chrome 在线且已经登录。操作日志位于
`logs/data-acquisition/{taskId}/operations.jsonl`，达人导出文件位于
`storage/exports/{taskId}/`；两者均被 Git 忽略。

## 安全边界

- `.env`、日志、导出文件、Chrome 临时 Profile、`node_modules` 和 `dist` 不提交；
- OpenCode 通过环境变量读取 DeepSeek Key，不把 Key 写入配置或日志；
- 导出下载 URL 中的临时签名不写入操作日志；
- MCP 断开使用 `browser.disconnect()`，不会关闭 CDP Chrome 窗口；
- 商品详情标签可以在处理完成后关闭，普通 Chrome 不受影响。
