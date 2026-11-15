# Watch Dog v0.4.0

MaiBot SDK 插件。所有规则按配置顺序检查，第一条完整命中后执行并结束。没有动作类型的固定排序。

## 配置

完整参数见 config.example.toml。每条规则包含 name、group、user、condition、action，可选 enabled。

- group 的 * 包含群聊与私聊，private 只匹配私聊，其余项为群号。
- user 为发送者 QQ 列表，* 匹配所有发送者。ID 支持数字字符串或整数。
- 群、用户和所有额外条件同时满足。condition=[] 允许只按范围匹配。
- action.kind 支持 block、follow_up、context_only、native。
- block 中的 reply 为固定回复文本，支持换行。发送失败仍中止消息。
- context_only 追加一次当前上下文后中止。追加失败记录消息 ID 并保持中止。
- native 直接交回主程序。follow_up 标记为提及麦麦，最终回复由主程序决定。
- 本版没有概率抽样；旧 context_only、group_context_only 和概率字段需要显式迁移。
- 空规则列表或插件关闭时所有消息走原生流程。
- 类型、正则、空 ID 列表及非法窗口设置会在配置校验时拒绝；无效热更新保留旧规则。

条件包括 image、forward、poke、poke_except_bot、text_regex、interaction。结构化 NapCat 通知通过 SnowLuma 适配器读取；poke_except_bot 匹配戳别人或缺失目标的戳一戳。转发仅检查适配器提供的正文，最多递归 64 层。正则预编译，使用 search；相邻文本拼接，各转发节点分别匹配；媒体说明与引用原文不作为正文。

互动类型为 nickname、at_bot、reply_to_bot、poke_bot、message，列表内部任一命中。昵称读取 bot.nickname 和 bot.alias_names，配置广播时失效缓存，最长 30 秒刷新。只检查本条原始文本。真实外层 at 核对目标 QQ，引用核对原作者，查不到作者不命中。通知及 bot 自身消息不作为普通追问。

## 留步

默认窗口 60 秒，本轮上限 300 秒；单条 action 可覆盖，0 或省略继承全局。窗口按规则、机器人账号、会话、发言人隔离。

主动互动命中才开窗或续期。已有窗口可以满足当前 follow_up 规则的 interaction 条件，但其他条件仍需满足。普通追问仅更新活跃时间。麦麦成功发言为同会话窗口有效且最近一个窗口时长内发言的人续期，受本轮上限约束。到期后重新互动可以再开窗。

有效配置更新、停用、卸载或重启清空窗口。前置 Hook 决定路径，后置 Hook 标记 is_mentioned。相同路由和消息 ID 在 5 分钟内避免重复追加、固定回复及窗口续期，最近记录上限 10000。消息内容不写入插件日志。

示例及本次部署策略将所有群和私聊统一处理。昵称、@、引用开窗；戳麦麦只入上下文，戳别人屏蔽。窗口内发给其他人的普通发言同样被视为追问。上下文追加依赖主程序运行时历史，重启持久性取决于宿主。

## WebUI

规则和条件使用强类型配置模型，提供中文标签、范围列表、类型下拉与互动多选，支持拖拽规则排序。

当前 MaiBot WebUI 不支持规则内部单个 action 对象的表单编辑，经用户选择，action 在原始 TOML 编辑。插件在可视化 Schema 中隐藏 action，避免显示 [object Object]；编辑其他字段保留已有动作。新增规则默认关闭并默认 native，需在原始 TOML 设置动作后再启用。窗口覆盖的 0 表示继承默认值。

## 安装与升级

Docker 主机插件目录为 /path/to/maibot/data/MaiMBot/plugins/local_watch-dog，挂载到 /MaiMBot/plugins/local_watch-dog。release ZIP 根目录直接解压到该目录，勿嵌套额外目录。

升级前备份插件和配置。v0.3 配置显式迁移命令为：

```sh
python3 migrate.py old-config.toml new-config.toml
```

该迁移器保留旧过滤规则顺序、名单、正则和固定回复，删除旧概率/群策略，新增全局互动留步和 context_only 两条尾规则。迁移是本次用户选定的全局策略调整，非所有旧部署的等价升级。迁移成功后用新配置替换运行时 config.toml，停用独立 gsskk.followup-window，重启：

```sh
cd /path/to/maibot
docker compose restart core
docker logs --since 2m maim-bot-core
```

检查 Watch Dog v0.4.0 加载、10 条规则、独立留步停用。实群与私聊分别检查普通消息只入上下文、昵称/@/引用开窗、同人追问放行、其他人不借窗、窗口到期。后续真实互动应能读取先前追加的内容。自动化模拟不能替代真实 QQ 事件验收。

## 验证

```sh
python -m unittest discover -s tests -v
```

本地缺少 SDK 时测试只替代宿主外壳，部署前还需在实际 MaiBot SDK 中运行全部测试和配置 Schema 检查。

窗口续期行为参考 gsskk/maibot-plugin-followup，许可证见 LICENSE.followup。
