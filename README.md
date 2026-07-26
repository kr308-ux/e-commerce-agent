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
.venv/bin/python web-ui/manage.py test tasks
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
