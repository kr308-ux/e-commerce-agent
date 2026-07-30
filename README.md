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
同一 `creator_id` 发送成功后不会从其他批次重复入队。邮件服务逐封串行发送，失败记录
需要人工确认后显式重试。

## 环境配置

首次准备：

macOS：

```bash
./scripts/setup-macos.sh
```

Windows 10+：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

复制 `.env.example` 后至少配置：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek/deepseek-v4-flash
IMPORT_RULE_MODEL=deepseek/deepseek-v4-pro
DOM_FALLBACK_MODEL=deepseek/deepseek-v4-pro

ZINIAO_COMPANY=
ZINIAO_USERNAME=
ZINIAO_PASSWORD=
ZINIAO_CONTACT_STORE_ID=

GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
```

导入规则修正和联系达人的 DOM 兜底共用 `DEEPSEEK_API_KEY`。联系任务正常路径由
确定性状态机执行，不逐步调用大模型；只有 DOM 或定位失败时，才把裁剪后的可交互
DOM 交给 `DOM_FALLBACK_MODEL` 分析，默认 `deepseek/deepseek-v4-pro`。模型返回的
定位器仍需在本地通过唯一、可见、可用校验。运行日志按上海时区写入
`logs/YYYY-MM-DD/regular/` 和 `logs/YYYY-MM-DD/model/`；常规点击前随机等待
1–2 秒，最终确认邀请按钮点击前固定等待 3 秒，页面刷新前仍随机等待 5–8 秒。
表格预览修正规则从 `IMPORT_RULE_MODEL` 读取。

## 启动

macOS：

```bash
./scripts/start-macos.sh
```

Windows：

```powershell
.\scripts\start-windows.ps1
```

启动脚本同时运行 Django、达人导入 Worker、达人联系 Worker 和定向合作同步 Worker。
启动顺序为：先启动 Django，再预启动并验收当前紫鸟店铺首页，最后启动各 Worker。
如果店铺浏览器的 DevTools 端点和首页标签页仍然有效，启动过程会直接复用现有浏览器，
不会重新启动紫鸟 WebDriver 端口或重复调用店铺 `startBrowser`。达人联系页会持续显示
店铺首页就绪状态、复用方式和 DevTools 端口。
如果启动时出现登录或验证码页面，启动流程会保持等待；请直接在已打开的店铺浏览器
完成验证。进入店铺首页后流程会自动继续，再启动各 Worker。默认等待时间不设上限，
可通过 `ZINIAO_BROWSER_LOGIN_WAIT_SECONDS` 配置最长等待秒数。

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
