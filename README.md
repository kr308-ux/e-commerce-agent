# E-commerce Agent

面向电商运营的达人数据导入、联系和邮件触达工作台。

## 达人表格导入

系统支持用户上传 `.xlsx`、`.xls` 和 `.csv` 达人表格：

1. Django 在请求内存中读取文件并生成前 20 行预览；
2. 确定性规则识别表头、达人 ID、昵称、邮箱和任意周期销售额；
3. 用户可使用自然语言要求 DeepSeek V4 Pro 修正转换规则；
4. 预览阶段不创建数据库任务、不写入达人表，也不保存原始文件；
5. 用户确认时重新提交同一文件，后端校验 SHA-256；
6. 独立 `run_import_worker` 使用已锁定规则处理完整文件；
7. Worker 写入达人、销售额快照、批次关系和行级错误后删除临时文件。

预览规则和文件保存在进程内存中并带过期时间。页面刷新、Django 重启或预览过期后，
用户需要重新选择文件。大模型只接收表头和少量样本，不接收完整文件或服务器路径。

## 数据模型

- `tasks_creator`：全局唯一达人，`creator_id` 直接作为联系搜索标识；
- `tasks_importtask`：用户确认后的导入批次和统计；
- `tasks_importtaskcreator`：导入批次包含的达人；
- `tasks_creatorsalesmetric`：任意周期销售额历史快照；
- `tasks_importruleversion`：确认后保存的系统及 AI 规则版本；
- `tasks_importrowerror`：不含原始行数据的错误记录；
- `tasks_importlog`：脱敏导入日志。

旧的商品、商品关联达人、浏览器采集任务和 Chrome Data MCP 已移除。迁移会先把历史达人、
7/30 天销售额、联系记录和邮件记录转入新模型，再删除旧表。

## 达人联系

达人联系模块位于 `web-ui/creator_contact/`。创建任务时选择一个已完成的导入批次，
可按达人 ID 自动选择表格候选人（最多 50 位）、按近 7 天/近 30 天/总销售额
倒序排名，或从列表手动勾选；三种方式都会排除当前店铺已经联系的 `creator_id`。
点击“创建联系任务”即授权并执行完整联系流程。

紫鸟自动化仍使用官方本地 WebDriver 接口完成招呼语、定向合作邀请和合作卡片流程。
`creator_id` 会原样作为紫鸟搜索标识；任务创建时冻结候选达人和所有远端操作授权。

详细自动化边界见 `ziniao-automation/README.md`。

## 达人合作邮件

`web-ui/mailing/` 从一个已确认导入批次筛选包含合法邮箱的达人，并创建全局防重队列。
同一 `creator_id` 发送成功后不会从其他批次重复入队。系统启动期间由受守护的常驻邮件
Worker 轮询队列，逐封串行处理，每次实际尝试之间随机等待 30–60 秒。达人联系任务结束
后，已完成合作卡片终态验收且有邮箱的达人会自动、幂等地进入邮件队列；联系 Worker
无需等待邮件完成即可继续下一项联系任务。单封邮件每天
最多尝试 3 次；当天仍失败的记录会保留完整尝试历史，并在次日恢复为可继续发送状态。
进程在 SMTP 结果落库前中断时，邮件会进入“送达状态待确认”，不会自动重发。运行日志
按日期保存 14 个自然日，发送日志位于
`logs/YYYY-MM-DD/regular/email-sender.log`。

## 环境配置

首次准备：

macOS：

```bash
./scripts/setup-macos.sh
```

Windows 10+ 源码开发环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

复制 `.env.example` 后至少配置：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek/deepseek-v4-flash
IMPORT_RULE_MODEL=deepseek/deepseek-v4-flash
DOM_FALLBACK_MODEL=deepseek/deepseek-v4-flash

ZINIAO_COMPANY=
ZINIAO_USERNAME=
ZINIAO_PASSWORD=
ZINIAO_CONTACT_STORE_ID=

GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
```

导入规则修正和联系达人的定位自愈共用 `DEEPSEEK_API_KEY`。联系任务正常路径由
确定性状态机执行，不逐步调用大模型。可恢复步骤第一次失败后先恢复上一成功检查点，
再由状态机执行一次；第二次仍失败时，才先从 CDP Accessibility Tree 经本地规则
筛出少量候选，AX 无法确认时再用裁剪后的可交互 DOM 补充候选。
`DOM_FALLBACK_MODEL`（默认 `deepseek/deepseek-v4-flash`）只能返回当前
快照中的 `candidateId`，CSS/XPath 由本地生成并通过唯一、可见、可用校验。
交互成功后定位器写入 `ZINIAO_ADAPTIVE_LOCATOR_PATH`，同一页面目标最多保留 3 条，
下次与固定定位器一起在完整等待窗口内按成功率和新鲜度轮询；渲染期间的临时 miss
不扣分，只有完整超时后每条缓存定位器才记录一次结构失败。运行日志按上海时区写入
`logs/YYYY-MM-DD/regular/` 和 `logs/YYYY-MM-DD/model/`；常规点击前随机等待
1–2 秒，最终确认邀请按钮点击前固定等待 3 秒。普通页面刷新前随机等待 5–8 秒；
第 8 步已打开定向合作但缺少“发送其他邀请开展合作”时固定等待 3 秒且只刷新一次。
表格预览修正规则从 `IMPORT_RULE_MODEL` 读取。

## Windows 无源码发布包

GitHub Actions 的 `Build Windows release` 工作流会在官方 Windows x64 Runner 上：

1. 运行全部离线测试；
2. 使用 PyInstaller one-folder 构建 `EcommerceAgent.exe`；
3. 对冻结程序执行迁移、Django 检查和五服务 Supervisor smoke test；
4. 从 OpenCode 官方 Release 下载固定的 Windows x64 CLI 并校验 SHA-256；
5. 扫描并拒绝项目 `.py`、`.env`、数据库、日志、测试和开发机路径；
6. 上传 `EcommerceAgent-Windows-x64-<version>.zip` 与 `SHA256SUMS.txt`。

在 GitHub 仓库的 **Actions → Build Windows release → Run workflow** 中选择 `dev`
分支并填写版本号即可手动构建。客户机无需安装 Python、Node.js、Git 或编译工具；下载后
只需解压、安装紫鸟、双击 `首次配置.cmd`，再双击 `启动系统.cmd`。

发布包内捆绑 OpenCode CLI 1.18.10，来源为官方 Release，构建时核验固定 SHA-256，
并附带 MIT License。业务密钥不会进入 Actions 或发布包，只写入客户机本地 `.env`。

## 启动

macOS：

```bash
./scripts/start-macos.sh
```

Windows 源码开发环境：

```powershell
.\scripts\start-windows.ps1
```

非技术用户也可以直接双击 `scripts\start-windows.cmd`。

Windows 绿色发布包用户应双击发布目录中的 `启动系统.cmd`，不要运行 `scripts` 下的
源码部署脚本。

启动脚本先启动一个单实例 Supervisor，由它守护 Django、达人导入 Worker、达人联系
Worker、定向合作同步 Worker 和邮件 Worker。任一子进程异常退出会按退避策略重启；
Ctrl+C 会先请求优雅退出，再清理残留进程。启动顺序为：先启动 Django，再预启动并
验收当前紫鸟店铺首页，最后启动各 Worker。Supervisor 每次启动还会清理超过
`LOG_RETENTION_DAYS`（默认 14 天）的日期日志目录。
如果店铺浏览器的 DevTools 端点和首页标签页仍然有效，启动过程会直接复用现有浏览器，
不会重新启动紫鸟 WebDriver 端口或重复调用店铺 `startBrowser`。达人联系页会持续显示
店铺首页就绪状态、复用方式和 DevTools 端口。
定向合作同步 Worker 只允许附加这个已缓存端点；缓存缺失或失效时会报错等待浏览器
重新准备，不会自行调用 `startBrowser`。
如果启动时出现登录或验证码页面，启动流程会保持等待；请直接在已打开的店铺浏览器
完成验证。进入店铺首页后流程会自动继续，再启动各 Worker。默认等待时间不设上限，
可通过 `ZINIAO_BROWSER_LOGIN_WAIT_SECONDS` 配置最长等待秒数。

Windows 首次部署和人工验收步骤见
[`docs/windows-runtime-acceptance.md`](docs/windows-runtime-acceptance.md)。

也可以手动执行同一项幂等检查：

```bash
.venv/bin/python web-ui/manage.py prepare_ziniao_browser
```

也可以单独运行导入 Worker：

```bash
.venv/bin/python web-ui/manage.py run_import_worker
```

打开 <http://127.0.0.1:8000/>。

## 数据库迁移

升级前先备份 `storage/agent.db`，再执行：

```bash
.venv/bin/python web-ui/manage.py migrate
```

历史迁移会依次新建模型、迁移旧达人及销售额、切换联系和邮件外键，最后删除商品和浏览器
采集模型。

## 验证

```bash
.venv/bin/python web-ui/manage.py check
.venv/bin/python web-ui/manage.py test tasks creator_contact mailing
PYTHONPATH=ziniao-automation/src \
  .venv/bin/python -m unittest discover -s ziniao-automation/tests -v
.venv/bin/python web-ui/manage.py makemigrations --check --dry-run
git diff --check
```

## 安全边界

- 上传文件大小、扩展名、MIME 和文件结构均会校验；
- 不允许宏，不执行 Excel 公式；
- 确认前文件只保留在内存；
- 确认后的源文件只进入私有临时目录，终态后删除；
- 无关列、完整原始行和原始文件不写数据库；
- 大模型只能返回通过 Schema 和业务校验的白名单规则；
- 日志不得保存密钥、密码、Cookie、完整源数据或服务器文件路径。
- SQLite 默认使用 WAL 以改善本地读写并发，但仍只有一个 writer；系统只启动一个
  联系 Worker 和一个串行邮件 Worker，并对安全的纯数据库领取操作做有限锁重试。
- 不确定的 SMTP 结果必须先在 Gmail 已发送记录中人工核对，再选择“确认已发送”或
  “核对后重试”。
