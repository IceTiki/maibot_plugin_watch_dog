# Watch Dog

新版 MaiBot SDK 消息过滤插件。规则间为或，子条件间为与，首条命中即屏蔽。默认规则为空。

## 配置

复制 config.example.toml 的规则到 MaiBot 生成的 config.toml。scope 支持 global、groups、private；ids 为群号或私聊用户号字符串列表，private 留空匹配所有私聊。conditions.kind 支持 image、forward、poke、text_regex；正则需提供 pattern，使用 search 语义。reply 留空静默屏蔽，TOML 多行字符串支持换行。

配置修改自动重载；无效更新保留旧规则。转发仅递归检查适配器已提供的正文，不下载缺失节点，不进行 OCR。从 0.3.1 起，poke 匹配适配器上报的所有戳一戳，包括群友互戳、戳麦麦及私聊戳一戳。支持 NapCat 经 SnowLuma 适配器传递的结构化通知以及原有 event 格式。递归最大深度 64。

## Docker 安装

将本目录复制到服务器 /path/to/maibot/data/MaiMBot/plugins/maimai_plugin_watch_dog，对应容器 /MaiMBot/plugins/maimai_plugin_watch_dog。安装 SDK 依赖后，在 /path/to/maibot 执行 docker compose restart core。查看 docker logs --tail 100 maim-bot-core 确认加载。

先在测试群配置规则，分别发送命中与不命中的消息，确认命中消息不触发识图、命令或聊天上下文，正常消息正常处理。验证静默回复、回复失败、嵌套转发和戳一戳。已有上下文不自动清理。其他提前执行的内置 Hook 不受本插件控制。

## 开发验证

python -m unittest discover -s tests -v

尚未在实际服务器验收。插件依赖适配器的结构化消息；缺少会话 ID 时仍屏蔽，但无法自动回复。

## 从 0.1.0 修复配置

更新到 0.1.1 后，在原始 TOML 编辑器中补上以下配置节并保存。已有的 `[[rules]]` 保持顶层，不要改成 `[[plugin.rules]]`。如果已有顶层 `enabled`，将它移动到 `[plugin]` 下，避免两处开关。

```toml
[plugin]
config_version = "0.1.1"
enabled = true
```

配置节可放在现有规则之前。空规则的完整初始配置如下（`rules` 必须在 `[plugin]` 之前）：

```toml
rules = []

[plugin]
config_version = "0.1.1"
enabled = true
```

## 0.1.2

修复预处理消息缺少会话 ID 时无法自动回复的问题；使用平台和群号/私聊用户号打开会话，保留已提供的账号路由。必须一起更新 Manifest（新增 chat.open_session 能力）并重载插件。规则配置无需修改。会话解析或发送失败仍然拦截消息。

## 按发送者控制 Planner（普通模式）

将以下配置追加到原始 TOML，将示例号码替换为发送者 QQ 号。此名单适用于所有 QQ 群和私聊。升级到 0.2.1 后，旧配置中的 group_id 会被忽略；同一 QQ 出现在多条规则时，按配置顺序取第一条。

```toml

[[context_only]]
user_ids = ["10001", "10002"]
planner_pass_probability = 0.05
allow_planner_on_at_bot = true
allow_planner_on_reply_to_bot = true
```

默认概率为 0；1 表示全部放行。两个例外默认开启，关闭后按普通概率抽样。直接 @ 和引用只看外层真实消息段，自动附带的 @ 也计入。无法核对引用作者时按普通概率处理。已有屏蔽规则优先，名单重叠取第一条。未放行的消息加入麦麦当前会话上下文，不触发 Planner；不保证进入 WebUI 历史或重启后保留。追加失败会记录消息 ID 并保持拦截。

## 按发送者完全屏蔽（0.2.2）

将示例 QQ 替换为目标账号，放在需要优先拦截的规则之前。顶层 ids 用于限定会话，条件中的 ids 匹配原始发送者。命中后不会进入 context_only 或 Planner。

```toml
[[rules]]
name = "完全屏蔽指定 QQ"
scope = "global"
reply = ""
conditions = [{kind = "sender_qq", ids = ["20001"]}]
```

## 指定群仅直接 @ 放行

已有屏蔽规则优先；随后按群匹配，再按发送者匹配。只检查外层真实 @ 段，引用自动携带的外层 @ 也算。普通消息仅加入当前上下文。

```toml

[[group_context_only]]
group_id = "20002"
planner_pass_probability = 0.0
allow_planner_on_at_bot = true
allow_planner_on_reply_to_bot = false
```
