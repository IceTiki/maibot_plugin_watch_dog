# Watch Dog

新版 MaiBot SDK 消息过滤插件。规则间为或，子条件间为与，首条命中即屏蔽。默认规则为空。

## 配置

复制 config.example.toml 的规则到 MaiBot 生成的 config.toml。scope 支持 global、groups、private；ids 为群号或私聊用户号字符串列表，private 留空匹配所有私聊。conditions.kind 支持 image、forward、poke、text_regex；正则需提供 pattern，使用 search 语义。reply 留空静默屏蔽，TOML 多行字符串支持换行。

配置修改自动重载；无效更新保留旧规则。转发仅递归检查适配器已提供的正文，不下载缺失节点，不进行 OCR。poke 要求 event 包含 poke 类型、target_id 和 self_id，且目标为机器人。适配器缺少这些字段时不能识别。递归最大深度 64。

## Docker 安装

将本目录复制到服务器 /path/to/maibot/data/MaiMBot/plugins/maimai_plugin_watch_dog，对应容器 /MaiMBot/plugins/maimai_plugin_watch_dog。安装 SDK 依赖后，在 /path/to/maibot 执行 docker compose restart core。查看 docker logs --tail 100 maim-bot-core 确认加载。

先在测试群配置规则，分别发送命中与不命中的消息，确认命中消息不触发识图、命令或聊天上下文，正常消息正常处理。验证静默回复、回复失败、嵌套转发和戳一戳。已有上下文不自动清理。其他提前执行的内置 Hook 不受本插件控制。

## 开发验证

python -m unittest discover -s tests -v

尚未在实际服务器验收。插件依赖适配器的结构化消息；缺少会话 ID 时仍屏蔽，但无法自动回复。
