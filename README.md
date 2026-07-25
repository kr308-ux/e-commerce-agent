# E-commerce Agent

面向电商运营场景的 Browser Agent。当前版本完成了跨平台开发环境骨架和 Django Web UI，尚未接入任务数据库、Agent Worker、OpenCode、DeepSeek、MCP 或 CDP。

## 当前包含

- Django Templates 构建的响应式工作台；
- 新建任务、任务列表、系统状态等界面占位；
- 任务进度、步骤日志和结果预览页；
- macOS 与 Windows 环境初始化脚本；
- 后续 Worker、Chrome MCP、紫鸟 MCP 的轻量目录骨架。

## macOS 启动

```bash
./scripts/setup-macos.sh
source .venv/bin/activate
python web-ui/manage.py runserver
```

打开 <http://127.0.0.1:8000/>。

## Windows 10+ 启动

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
.\scripts\start-windows.ps1
```

## 手动安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python web-ui/manage.py check
python web-ui/manage.py runserver
```

## 目录说明

```text
web-ui/             Django 界面
agent-worker/       异步任务执行器（待开发）
data-acquisition/   普通 Chrome MCP（待开发）
ziniao-automation/  紫鸟浏览器 MCP（待开发）
storage/            SQLite 数据文件目录
shared/             被两个模块实际复用时再放入公共代码
prompts/            OpenCode 任务提示词（待开发）
scripts/            跨平台环境脚本
```

## 当前边界

本阶段所有任务、进度、日志和结果均为 UI 演示数据。点击“创建演示任务”只展示提示，不会写入数据库、调用模型或操作浏览器。
