# Windows 客户机人工验收清单

本文只用于 Windows 10/11 客户机人工验收。开发机上的自动化测试不会启动紫鸟、不会
联系真实达人，也不会发送真实邮件。

## 一、取得并核验绿色发布包

1. 在 GitHub 仓库打开 **Actions → Build Windows release**，选择一次成功运行。
2. 下载 `EcommerceAgent-Windows-x64` artifact 并解压一次，得到发布 ZIP 和
   `SHA256SUMS.txt`。
3. 在 PowerShell 中核验发布 ZIP：

   ```powershell
   Get-FileHash .\EcommerceAgent-Windows-x64-*.zip -Algorithm SHA256
   ```

   结果必须与 `SHA256SUMS.txt` 完全相同。
4. 把发布 ZIP 解压到可写、路径可包含空格和中文的目录。客户机不得安装或依赖 Python、
   Node.js、Git 和项目源码。

## 二、首次配置

1. 安装紫鸟客户端，但不要复制开发机的 `.env`、数据库、日志或浏览器会话。
2. 双击发布目录中的 `首次配置.cmd`。脚本应创建空数据目录、本机 `.env` 和随机
   `DJANGO_SECRET_KEY`，但第二次运行不得覆盖已有 `.env`。
3. 在自动打开的记事本中至少核对：

   - `ZINIAO_CLIENT_PATH`：留空使用默认路径，或填写实际 `ziniao.exe` 完整路径；
   - 紫鸟公司、用户名、密码和店铺 ID；
   - Gmail 地址和应用专用密码；
   - `LOG_RETENTION_DAYS=14`；
   - `SQLITE_ENABLE_WAL=true`。

4. 保存并关闭记事本。脚本应自动执行迁移和 Django 检查，最后显示“首次配置完成”。
5. 确认以下内容全部位于解压目录，而不是 `_internal`：

   - `.env`；
   - `storage\agent.db`；
   - `logs\`；
   - `temporary\`；
   - 浏览器驱动、会话状态和自适应定位器数据库。

## 三、启动与进程守护

双击发布目录中的 `启动系统.cmd`。预期窗口依次显示：

1. Django 已启动；
2. 紫鸟以 `--run_type=web_driver --ipc_type=http --port=16851` 模式通过验收；
3. 导入 Worker 已启动；
4. 达人联系 Worker 已启动并监听 `16852`；
5. 定向合作同步 Worker 已启动；
6. 邮件 Worker 已启动并监听 `16853`。

重复双击启动脚本时，第二个窗口应提示“系统已经启动”，不能再创建第二套 Worker。

如果紫鸟已经以普通工作台模式运行，系统必须拒绝继续自动化，不能默默复用或按进程名
结束其他紫鸟进程。

运行 `系统检查.cmd` 应显示 Django 系统检查通过。运行 `停止系统.cmd` 或在启动窗口按
Ctrl+C 后，五个受管服务应全部退出。

## 四、无真实外部写入的检查

1. 打开 Django 页面，确认导入、联系、邮件页面可访问。
2. 在发布目录打开 PowerShell，执行日志清理预览：

   ```powershell
   .\EcommerceAgent.exe --internal-manage cleanup_runtime_logs --dry-run
   ```

3. 检查 `logs\YYYY-MM-DD\` 下存在运行日志，且异常日志不包含密码、Cookie 或 API Key。
4. 按 Ctrl+C。预期所有受管服务依次停止，不留下额外 Django/Worker Python 进程。

5. 查看发布目录，确认不存在项目 `.py`、`.git`、测试、真实 `.env` 模板值、历史数据库、
   历史日志、截图、Cookie 或开发机绝对路径。
6. 验证 `.xlsx`、`.xls`、`.csv` 导入，并使用测试提示验证 OpenCode 规则修正功能。

## 五、排队与并发业务验收

仅使用你明确授权的测试店铺、测试达人和测试邮箱。

1. 创建三个联系任务 A、B、C，并依次点击启动。
2. 确认系统始终只有一个 Contact Worker；任务顺序严格为 A → B → C。
3. A 完成后检查：

   - 只有合作卡片终态成功且有邮箱的达人进入邮件队列；
   - 无邮箱、失败、需人工复核的达人不入队；
   - Email Worker 开始串行发信；
   - A 的邮件发送期间，Contact Worker 已开始 B。

4. B、C 完成后，邮件应追加到同一队列；同一达人不得产生重复投递。
5. 达到每日上限后，邮件保留在队列，不应每隔几秒重复尝试。

## 六、异常恢复验收

以下测试不要对真实客户邮箱或未授权达人执行。

1. 在没有远端操作的等待阶段结束 Contact Worker：

   - Supervisor 应自动重启；
   - 尚未开始目标操作的任务重新排队。

2. 在测试达人操作过程中结束 Contact Worker：

   - 当前目标应变为“需人工复核”；
   - 系统不得自动重发私信、邀请或合作卡片；
   - 后续排队任务可以继续。

3. 在测试邮箱的 SMTP 发送阶段结束 Email Worker：

   - 对应邮件应变为“送达状态待确认”；
   - 系统不得自动重发；
   - 在 Gmail“已发送”中核对后，选择“确认已发送”或“核对后重试”。

4. 点击邮件页面的“停止发送”：

   - 当前邮件完成后服务进入“已暂停”；
   - 常驻 Worker 不得立刻重新开始；
   - 新建队列或人工重试会显式恢复发送。

## 七、路径和关机专项验收

1. 分别在含空格和中文的解压路径运行首次配置、检查、启动和停止脚本。
2. 直接关闭启动窗口后，确认没有残留 `EcommerceAgent.exe` 子进程。
3. 再次启动后确认 SQLite WAL 正常恢复，未出现第二套 Worker。
4. 在未安装 Python、Node.js 和 Git 的干净 Windows 10/11 x64 测试机重复上述检查。

## 八、验收记录

请记录以下信息并反馈：

- Windows 版本和 PowerShell 版本；
- 紫鸟实际安装路径；
- 启动窗口中五个服务的 PID；
- A/B/C 三个任务的执行顺序；
- 联系与邮件并发时是否出现 `database is locked`；
- Ctrl+C 后是否存在残留进程；
- 直接关闭窗口后是否存在残留进程；
- 绿色包解压路径是否包含空格或中文；
- ZIP SHA-256 与 `SHA256SUMS.txt` 是否一致；
- 任何“需人工复核”或“送达状态待确认”记录的截图。
