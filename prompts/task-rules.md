# Chrome Data MCP 任务规则

“获取达人数据”任务由 DeepSeek 通过 OpenCode 调用 `chrome-data` MCP。工具顺序：

1. `chrome_connect`
2. `chrome_open_url`
3. `chrome_check_login`
4. `chrome_open_selection`
5. `chrome_open_product_module`
6. `chrome_open_product_search`
7. `chrome_open_category`
8. `chrome_select_sports_outdoor`
9. `chrome_select_sports_apparel`
10. `chrome_collect_products_for_creators`，商品数量只能是 10–100 的十倍数
11. 对每个商品依次调用：
    - `chrome_open_product_detail`
    - `chrome_open_related_creators`
    - `chrome_export_related_creators`
    - `chrome_close_product_detail`
12. `chrome_disconnect`

每个工具必须传入父任务 `taskId` 和唯一 `stepId`。每次调用后检查结构化
`success`、`status`、`data` 和 `error`，失败时不得猜测成功。

登录失效并返回 `AUTH_REQUIRED` / `WAITING_CONFIRMATION` 时必须停止，提示用户在
对应 CDP Chrome 中登录。不得读取、保存或输出 Cookie、令牌、密码或 API Key。

导出关联达人时固定选择“100 条”。文件只能下载到 `storage/exports/{taskId}/`
隔离目录；日志只能记录文件名、受控路径、大小和行数，不记录带签名的下载 URL。

商品详情必须从列表点击并在新标签页打开。处理完一个商品后关闭该详情标签，
但不得关闭 Chrome 窗口或用户正在使用的普通 Chrome。

详细 MCP 操作日志：

`logs/data-acquisition/{taskId}/operations.jsonl`
