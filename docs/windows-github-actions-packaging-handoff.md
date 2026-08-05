# Windows 无源码发布包与 GitHub Actions 构建交接

更新时间：2026-08-05
当前仓库：`e-commerce-agent`
当前分支：`dev`
开发环境：macOS，Python 3.12.10
目标环境：Windows 10/11 x64

## 1. 交接目标

从 Mac 发起构建，由 GitHub Actions 的 Windows x64 Runner 生成可交付给非技术用户的
Windows 发布包。客户机不安装 Python、pip、Node.js、Git 或编译工具，也不接收仓库、
测试代码和项目 `.py` 源文件。

第一阶段的目标产物：

```text
EcommerceAgent-Windows-x64-<version>.zip
└── EcommerceAgent
    ├── EcommerceAgent.exe
    ├── _internal\                 # PyInstaller one-folder 运行时
    ├── runtime\opencode\         # 若决定继续捆绑 OpenCode
    ├── config\.env.example
    ├── storage\
    ├── logs\
    ├── temporary\
    ├── 首次配置.cmd
    ├── 启动系统.cmd
    ├── 停止系统.cmd
    └── 系统检查.cmd
```
客户机的预期操作仅为：安装紫鸟、解压 ZIP、运行首次配置、填写本机配置、启动系统。

第二阶段可在同一发布目录外再套 Inno Setup，产出标准的
`EcommerceAgent-Setup.exe`，但第一阶段不要同时追求安装器和冻结程序，先把绿色包跑通。

## 2. 已确认的项目现状

### 2.1 当前 Windows 脚本仍是源码部署

`scripts/setup-windows.ps1` 会执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

因此它要求客户机预装 Python，并且将源码目录直接作为运行目录，不符合本次目标。

### 2.2 OpenCode 不是达人联系主流程的编排器

生产联系链路已经使用确定性 Python 状态机：

```text
web-ui/creator_contact/services/contact_runner.py
    -> python -m ziniao_automation.contact_task_runner
    -> ContactAutomationState
```

`ziniao-automation/agent_runner.py` 和根目录 `opencode.json` 主要保留给历史 Agent
执行/调试链路，不是 Web UI 联系 Worker 的主要入口。

### 2.3 仍有一个生产功能依赖 OpenCode

`web-ui/tasks/services/ai_rule_advisor.py` 使用：

```text
opencode run --pure --dir <临时目录> --model ... --format json <prompt>
```

它服务于“用自然语言修改表格导入规则”。`OPENCODE_BINARY` 在
`web-ui/config/settings.py` 中配置。如果发布包不提供 OpenCode，这个功能会报
“未找到 OpenCode 可执行程序”，但确定性表格导入、达人联系和邮件功能不因此全部失效。

短期推荐在发布包中提供固定版本的 Windows OpenCode 可执行程序，并将
`OPENCODE_BINARY` 解析到发布目录；在捆绑前必须核对该版本的官方分发方式、许可证、
Windows x64 文件名和 SHA-256。不要在没有确认重新分发条件时直接提交第三方二进制。

长期可把 `ImportRuleAdvisor` 改为 Python 直接调用 DeepSeek API，从生产运行时移除
OpenCode。

### 2.4 项目是多进程结构

`run_runtime_supervisor` 会守护：

1. Django；
2. 导入 Worker；
3. 达人联系 Worker；
4. 定向合作同步 Worker；
5. 邮件 Worker。

此外，联系任务还会创建独立的 `ziniao_automation.contact_task_runner` 子进程。
当前多处代码通过 `sys.executable + manage.py` 或
`AUTOMATION_PYTHON_EXECUTABLE + -m` 构造命令。冻结后 `sys.executable` 将指向
`EcommerceAgent.exe`，不再是可解释 `manage.py` 或 `-m` 的 Python，因此不能只增加
一个简单的 PyInstaller spec 就宣称完成。

已知需要审计/改造的入口至少包括：

- `web-ui/tasks/management/commands/run_runtime_supervisor.py`
- `web-ui/creator_contact/views.py`
- `web-ui/creator_contact/services/contact_runner.py`
- `web-ui/creator_contact/services/collaboration_sync.py`
- `web-ui/mailing/services/launcher.py`
- `web-ui/config/settings.py`
- `ziniao-automation/src/ziniao_automation/agent_runner.py`

执行前应再次运行：

```bash
rg -n 'sys\.executable|AUTOMATION_PYTHON_EXECUTABLE|python_executable|subprocess\.(Popen|run)' \
  web-ui ziniao-automation shared -g '*.py'
```

## 3. 推荐技术方案

### 3.1 使用 PyInstaller one-folder，不先做 one-file

第一版选择 one-folder：

- 启动速度和故障定位优于 one-file；
- Django 模板、静态文件和图片更容易显式收集；
- 外部 OpenCode、配置和可写数据可保持清晰边界；
- 避免 one-file 临时解压目录影响 `.env`、SQLite、日志和浏览器会话路径；
- 后续可直接由 Inno Setup 把整个目录封装成安装器。

PyInstaller 会打包 Python 字节码，不等于强加密或不可逆。如果需求是提高逆向成本，而
不仅是“不交付可直接阅读的 `.py`”，应在绿色包稳定后单独评估 Nuitka。不要把
PyInstaller 描述成源码安全方案。

### 3.2 增加一个冻结程序入口分发器

建议新增入口，例如 `packaging/windows_entrypoint.py`，负责区分用户入口和内部入口：

```text
EcommerceAgent.exe
    -> 默认：运行 run_runtime_supervisor

EcommerceAgent.exe --internal-manage runserver 127.0.0.1:8000 --noreload
EcommerceAgent.exe --internal-manage run_import_worker
EcommerceAgent.exe --internal-manage run_creator_contact_worker --server-mode
EcommerceAgent.exe --internal-manage run_collaboration_sync_worker
EcommerceAgent.exe --internal-manage run_email_worker --server-mode
EcommerceAgent.exe --internal-contact-task <原 contact_task_runner 参数>
```

同时新增统一命令构造模块，例如 `shared/runtime_commands.py`：

- 开发态继续返回 `python manage.py ...` / `python -m ...`；
- 冻结态返回 `EcommerceAgent.exe --internal-* ...`；
- 所有 Supervisor、View、Worker launcher 和联系执行器统一使用它；
- 不允许各模块自行判断 `getattr(sys, "frozen", False)` 并复制命令拼装逻辑。

所有内部入口都必须保留当前的参数校验、安全授权、退出码、stdout JSONL 和日志行为。

### 3.3 分开“只读资源根目录”和“可写数据根目录”

当前代码大量从 `__file__` 推导 `PROJECT_ROOT`。PyInstaller one-folder 中资源可能位于
`_internal`，而 `.env`、SQLite 和日志必须写在 `EcommerceAgent.exe` 所在的外部目录。

建议明确两个根目录：

- `RESOURCE_ROOT`：模板、静态文件、迁移、内置默认资源；
- `APP_HOME`：EXE 所在目录，包含 `.env`、`storage`、`logs`、`temporary`。

开发态两者可以都指向仓库根目录；冻结态：

```python
APP_HOME = Path(sys.executable).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_HOME))
```

实际实现要适配所选 PyInstaller 版本的 one-folder 布局，并用测试固定行为。以下内容必须
从 `APP_HOME` 解析：

- `.env`
- `DATABASE_PATH`
- `RUNTIME_LOG_ROOT`
- `IMPORT_TEMP_DIR`
- `ZINIAO_DRIVER_DIR`
- `ZINIAO_BROWSER_SESSION_DIR`
- `ZINIAO_BROWSER_STATUS_PATH`
- `ZINIAO_ADAPTIVE_LOCATOR_PATH`
- `MAILING_MEDIA_ROOT`
- 外部 `OPENCODE_BINARY`

模板、静态文件、迁移和内置邮件图片从 `RESOURCE_ROOT` 读取。不要把数据库、`.env`、
日志或客户上传内容写入 `_internal`。

### 3.4 保持交互式桌面应用，不做 Windows Service

系统需要启动紫鸟并操作登录用户的浏览器会话。第一版继续用控制台启动和现有
Supervisor；如果要开机启动，使用任务计划程序并设置“仅当用户登录时运行”。不要默认
注册为运行在隔离会话里的 Windows Service。

## 4. 实施顺序

每一阶段完成后都要保留 macOS 开发态运行能力。

### 阶段 A：建立发布边界

1. 新增 `APP_HOME` / `RESOURCE_ROOT` 辅助模块并补单元测试。
2. 新增统一的子进程命令构造模块。
3. 将所有 Python/Django 子进程入口迁移到统一模块。
4. 新增冻结入口分发器及参数测试。
5. 确认开发态现有启动脚本和完整测试仍通过。

### 阶段 B：建立 PyInstaller one-folder 构建

1. 添加锁定版本的构建依赖文件，例如 `requirements-build.txt`。
2. 新增 `.spec`，显式收集：
   - Django app 模板；
   - 静态 CSS/JS；
   - `web-ui/mailing/assets/FR7A6183.png`；
   - Django migrations；
   - `ziniao_automation` 包；
   - `shared` 包；
   - 动态导入的 Django management commands。
3. 构建完成后扫描发布目录，拒绝出现项目 `.py`、`.git`、测试、`.env`、真实数据库、
   日志、截图、临时文件、Cookie 或本机路径。
4. 发布目录中只放空的可写目录和脱敏的 `.env.example`。

### 阶段 C：Windows 首次配置和启动脚本

建议新增发布专用脚本，不要直接复用当前假设存在 `.venv` 的脚本。

`首次配置.cmd` / PowerShell 脚本至少完成：

1. 检查 Windows x64 和目录写权限；
2. 检查/询问 `ziniao.exe` 路径；
3. 从 `.env.example` 创建 `.env`，但不覆盖已有 `.env`；
4. 生成随机 `DJANGO_SECRET_KEY`；
5. 创建 `storage`、`logs`、`temporary` 及必要子目录；
6. 设置或验证 `OPENCODE_BINARY`；
7. 执行内部 `migrate` 和 `check`；
8. 输出不包含密钥的诊断摘要；
9. 可选创建桌面快捷方式。

不要把紫鸟、DeepSeek、Gmail 的真实凭据放入 GitHub Secrets 再写入发布物。业务凭据只在
客户机本地输入并保存到本地 `.env`。首次配置日志必须脱敏。

`启动系统.cmd` 应只启动 `EcommerceAgent.exe`，保持控制台可见，并清楚提示
Ctrl+C 的退出方式。第一版不应静默隐藏运行失败。

### 阶段 D：GitHub Actions Windows 构建

新增类似 `.github/workflows/build-windows.yml` 的工作流：

- 触发：`workflow_dispatch` 和版本 Tag；
- 权限：默认只读；
- Runner：`windows-latest`；
- Python：固定 3.12.x、x64；
- 安装运行和构建依赖；
- 运行离线测试，禁止真实联系达人、发送邮件或启动紫鸟；
- 运行 PyInstaller；
- 组装发布目录；
- 执行发布内容安全扫描；
- 执行冻结程序的离线 smoke test；
- 生成 ZIP 和 `SHA256SUMS.txt`；
- 上传 Actions artifact；
- Tag 发布流程可选创建 GitHub Release。

CI 不应依赖真实 `.env`。如果测试需要环境变量，只使用明显的测试值和
`--skip-browser-prepare`。不得在 CI 中调用真实 DeepSeek、Gmail 或紫鸟远端写操作。

建议先只做 `workflow_dispatch`，稳定后再接入 Tag 和 Release，减少未验收版本被自动
发布的风险。

### 阶段 E：Windows 真机验收

GitHub Runner 只能验证构建和离线行为，不能完成紫鸟桌面自动化验收。必须在一台干净的
Windows 10/11 x64 客户机或测试机上执行：

1. 未安装 Python、Node、Git 时能否首次配置；
2. 所有五个受管服务是否启动；
3. 第二次启动是否被单实例锁拒绝；
4. 紫鸟普通工作台模式是否被安全拒绝；
5. 紫鸟 WebDriver 模式和店铺登录等待是否正常；
6. 导入 `.xlsx` / `.xls` / `.csv`；
7. OpenCode 规则修正功能（如果保留）；
8. 只使用授权测试店铺/达人执行联系流程；
9. 只使用测试邮箱验证邮件队列；
10. Ctrl+C 和直接关闭窗口后是否存在残留进程；
11. SQLite WAL、日志、临时文件和浏览器缓存是否全部位于 `APP_HOME`；
12. 路径包含空格和中文时能否运行。

继续参考 `docs/windows-runtime-acceptance.md`，但需要补充冻结发布包特有的验收项。

## 5. GitHub Actions 初始骨架

下面仅作为新对话的方向，不应未经适配直接视为完成版本：

```yaml
name: Build Windows release

on:
  workflow_dispatch:
  push:
    tags:
      - "v*"

permissions:
  contents: read

jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          architecture: "x64"
          cache: "pip"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt
          python -m pip install -r requirements-build.txt

      - name: Run offline tests
        shell: pwsh
        run: |
          python web-ui/manage.py check
          python web-ui/manage.py test tasks creator_contact mailing
          $env:PYTHONPATH = "ziniao-automation/src"
          python -m unittest discover -s ziniao-automation/tests -v

      - name: Build one-folder application
        run: pyinstaller packaging/EcommerceAgent.spec --noconfirm --clean

      - name: Assemble and verify release
        shell: pwsh
        run: ./packaging/windows/assemble-release.ps1

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: EcommerceAgent-Windows-x64
          path: release/EcommerceAgent-Windows-x64.zip
          if-no-files-found: error
```

实现时固定第三方 Action 的主版本只是起点；正式供应链策略可进一步固定 commit SHA。

## 6. 发布安全要求

发布物必须排除：

- `.git/`、`.github/`（运行时不需要）；
- `.env`；
- `storage/agent.db*` 和真实备份；
- `logs/` 中的历史文件；
- `temporary/` 中的内容；
- 测试、报告、截图、缓存；
- Mac `.venv` 和 `node_modules`；
- OpenCode 的用户级认证文件和 Session；
- DeepSeek、紫鸟、Gmail 密钥；
- 开发机绝对路径。

构建脚本应采用白名单组装发布目录，而不是复制仓库后再靠黑名单删除。

建议生成：

- ZIP SHA-256；
- 构建版本、Git commit、Python/PyInstaller/OpenCode 版本清单；
- 可选 SBOM；
- 后续正式发布时增加代码签名，降低 Windows SmartScreen 警告。

## 7. 完成定义

满足以下条件才算第一阶段完成：

- Mac 用户可以在 GitHub 页面手动触发 Windows 构建；
- Actions 在 Windows x64 上完成测试、构建、安全扫描和 ZIP 上传；
- ZIP 不含项目 `.py` 源文件及任何真实数据/密钥；
- 干净 Windows x64 客户机不安装开发环境即可配置和启动；
- Django 与四类 Worker 均由 Supervisor 正常守护；
- 联系任务子进程可在冻结态启动、取消和超时清理；
- 紫鸟浏览器准备、OpenCode 规则修正（若保留）、导入、联系、同步、邮件均通过对应验收；
- Ctrl+C 和异常退出不留下受管 Python/应用子进程；
- macOS 源码开发启动与现有测试没有回归；
- README 和 Windows 验收文档已更新为发布包流程。

## 8. 暂不包含的工作

第一阶段不要顺手扩大到：

- 自动安装或重新分发紫鸟；
- 后台 Windows Service；
- 自动填入真实业务凭据；
- 自动对真实达人或客户邮箱做验收；
- 在线自动升级和数据库自动回滚；
- 多用户远程访问 Django；
- 同时支持 Windows ARM64；
- 追求单文件 EXE；
- 把 PyInstaller 当作强源码保护。

这些内容应在绿色包稳定后分别设计。

## 9. 新对话开始前需要用户确认的最少事项

新对话可以先采用以下默认值推进，不必因非关键问题停住：

- 目标：Windows 10/11 x64；
- 形式：PyInstaller one-folder 绿色 ZIP；
- Python：3.12.x；
- 构建触发：先 `workflow_dispatch`；
- 数据：新安装创建空 SQLite，绝不打包开发数据库；
- 运行：用户登录后的交互式控制台；
- OpenCode：先保留接口，但在实际捆绑前核对官方发布和许可证；
- 签名和 Inno Setup：第二阶段。

真正需要尽早确认的是：GitHub 仓库是否允许 Actions 读取源码，以及最终是否允许把
OpenCode Windows 二进制重新分发给客户。如果不允许上传 GitHub，应改用 Mac 上的
Windows 虚拟机或自托管 Windows Runner；如果不允许捆绑 OpenCode，则选择首次配置时
下载固定校验版本，或改成直接调用 DeepSeek。

## 10. 可直接复制到新对话的提示词

```text
请根据 docs/windows-github-actions-packaging-handoff.md 开始实施第一阶段：
在不破坏 macOS 开发态的前提下，为本项目建立 GitHub Actions Windows x64 的
PyInstaller one-folder 绿色发布包。

先完整阅读交接文档和相关启动/Worker 代码，检查当前工作树，不要覆盖我的已有修改。
按阶段推进：先解决冻结态 APP_HOME/RESOURCE_ROOT 和多进程入口，再写 spec、Windows
发布脚本和 workflow。每一阶段补测试并运行验证。禁止把 .env、开发数据库、日志、
临时文件、真实密钥或项目 .py 源文件放进发布物；CI 禁止真实联系达人、发送邮件或调用
紫鸟远端写操作。

第一版目标是 Windows 10/11 x64 的 one-folder ZIP，不做单文件 EXE、Windows Service
或 Inno Setup。OpenCode 保留为外部运行时接口；在下载或捆绑二进制前，先核对官方
Windows 发布方式、许可证、固定版本和 SHA-256，并把结论告诉我。

请先给出基于当前代码的实施计划，然后直接开始实现和验证；只有会实质改变发布方案的
选择才需要向我确认。
```
