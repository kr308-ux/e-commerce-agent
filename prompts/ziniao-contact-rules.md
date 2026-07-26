# 紫鸟联系达人 MCP 任务规则

“紫鸟联系达人”任务由 DeepSeek 通过 OpenCode 调用 `ziniao-contact` MCP。工具顺序：

紫鸟只能由 `--run_type=web_driver --ipc_type=http --port=...` 主进程打开。禁止使用
普通紫鸟工作台启动店铺、禁止直接启动店铺 Chromium，禁止把普通浏览器或人工点击结果
写成 Agent 成功证据。连接前必须通过 WebDriver 模式校验。

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
13. `ziniao_send_collaboration_card`
14. `ziniao_disconnect`

搜索步骤必须先输入达人用户名并点击输入框下方的精确候选项，再下滑验收同一页面中的
搜索结果卡片。打开详情步骤只能点击搜索结果卡片，不能再次点击下拉候选项。

每次调用后必须检查结构化 `success`、`status`、`data` 和 `error`。不得读取、保存或
输出紫鸟密码、Cookie、令牌或 DeepSeek API Key。

任何远端写操作都必须满足以下安全门：

- 聊天页顶部用户名和 URL `creator_id` 必须同时匹配目标达人。
- 招呼语只能使用任务快照中的精确文本与 SHA-256；调用时必须显式传入
  `greetingMessage`、`greetingSha256` 和 `confirmSendGreeting=true`，发送后
  必须验收完整消息气泡。
- 只能精确选择任务指定的定向合作邀请，名称与 `invitationGroupId` 必须同时
  匹配。选择步骤不得点击最终“邀请”按钮。
- 最终发送邀请必须等待所有前置步骤成功，并显式传入
  `confirmSendInvitation=true`；发送后必须验收弹窗关闭和成功证据。
- 创建邀请后必须取得达人专属 `invitationId`，再调用
  `ziniao_send_collaboration_card`；必须显式传入 `confirmSendCard=true`。
- 如果页面提示邀请添加成功，但右侧卡片尚未出现，必须每次随机等待 3–5 秒后刷新，
  最多刷新 3 次。三次后仍不可见则返回 `skipCreator=true`，关闭本达人详情和聊天
  标签，调用 `ziniao_disconnect` 并跳过该达人；不得调用合作卡片发送工具或重复点击
  邀请，也不得写入已联系表。
- 合作卡只有在刷新后读到本人发送的同一 `invitationId` 的 `targetPlan` 消息、
  非空服务端消息 ID 且 `flightStatus` 为成功终态时，才算最终成功。
- 最终工具必须同时返回 `creatorTabsClosed=true`、`searchTabKept=true` 和
  `returnedToFindCreators=true`，证明已关闭本达人详情与聊天标签并切回查找达人
  标签；缺少任何一项都不能标记成功或写入已联系表。
- 对已经可见的相同消息、合作邀请或成功合作卡不得重复发送；发送中、失败或
  状态不明时必须停止并等待人工复核。

如果当前任务未明确授予某项发送确认，必须在对应发送工具之前停止正常流程并安全
调用 `ziniao_disconnect`。
