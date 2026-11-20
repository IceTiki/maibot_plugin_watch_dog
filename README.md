# Watch Dog v0.5.0 配置手册

Watch Dog 决定每条新收到的 QQ 消息如何处理：拦截、固定回复、仅加入麦麦的上下文、交给 MaiBot 原生调度，或允许同一个人在短时间内连续追问。可从下面的小配置开始，也可复制 [config.example.toml](config.example.toml) 获得含黑名单、私聊、特殊群和默认群的完整模板。示例中的数字是占位 QQ 或群号。

## 从一份可运行的配置开始

下面的配置让示例群的普通消息只进入上下文，直接 @ 或引用麦麦时走原生流程。单独发送 `/ping` 固定回复 `pong!`。其他会话没有匹配的 route，默认走原生流程。

~~~toml
[plugin]
config_version = "0.5.0"
enabled = true

[[routes]]
group = ["20001"]  # 示例互动群；替换为你的群号
user = ["*"]
use = ["common", "at_bot", "context_only"]

[rule_groups]
common = [
  { name = "/ping", condition = [{ kind = "text_regex", pattern = '^\s*/ping\s*$' }], action = { kind = "block", reply = "pong!" } },
]
at_bot = [
  { condition = [{ kind = "interaction", types = ["at_bot", "reply_to_bot"] }], action = { kind = "native" } },
]
context_only = [
  { action = { kind = "context_only" } },
]
~~~

## 第一步：为会话和发送者选一条 route

每写一次 `[[routes]]` 就增加一个匹配项。按文件顺序排列，通常把机器人黑名单放最前，私聊和指定群放中间，通配项放最后。

| 字段 | 怎么填 |
|---|---|
| `group` | 群号列表。`["private"]` 表示所有私聊，`["*"]` 同时包含群聊和私聊。在群号旁用 `#` 写群名，匹配时只看群号。 |
| `user` | 发送者 QQ 列表。`["*"]` 表示所有发送者。想屏蔽只在某个群出现的账号，就同时填写该群号和账号。 |
| `use` | 有顺序的规则组名列表，至少一个，名字必须已在 `[rule_groups]` 定义。 |

`group` 和 `user` 各自的列表内取“任一”，两个字段之间取“同时”。两者省略均默认 `["*"]`，显式空列表无效。建议把 QQ 和群号写成加引号的数字字符串。

**选择过程**：先选第一条匹配群和用户的 route，然后按它的 `use` 从左到右检查。每组从上往下找规则，组内没有命中才看下一组。执行首条命中的动作后结束。即使这条 route 的所有组都没命中，也会直接交还原生流程，不再尝试下一条 route。

例如 `use = ["common", "at_bot", "context_only"]`：发 `/ping` 在第一组结束；引用麦麦在第二组结束；普通消息在第三组加入上下文。若把无条件的 `context_only` 移到最前，后面的组就永远不会执行。

## 第二步：写可复用的规则组

在 **同一个** `[rule_groups]` 表下增加 `组名 = [规则一, 规则二, …]`。组名以英文字母开头，只含小写英文字母、数字、下划线。组之间的定义顺序不影响执行；每条 route 的 `use` 决定实际检查哪些组以及先后顺序。相同组可被多个 route 引用，修改它会影响全部引用方。

| 规则字段 | 默认值 | 含义 |
|---|---|---|
| `name` | 使用组名和序号 | 写能在日志里辨认的名字。日志也会记录所属组。 |
| `enabled` | `true` | `false` 暂停这一条，继续检查后面的规则。 |
| `condition` | `[]` | 列表内所有条件都要满足。省略或写空列表表示总是命中。 |
| `action` | `{ kind = "native" }` | 命中后执行。建议显式填写，尤其是无条件兜底规则。 |

**条件 `condition[].kind`**

| 值 | 判断方式 |
|---|---|
| `text_regex` | 需填 `pattern`，按 Python 正则 `search` 查找。全文本段匹配可用 `^…$`。推荐 TOML 单引号保留反斜杠，例如 `'^\s*/ping\s*$'`。 |
| `image` | 包含结构化图片段，适配器提供的转发正文内图片也算。 |
| `forward` | 包含转发消息或节点。只有转发 ID 时仍能识别转发，内部文本和图片则无法检查。 |
| `poke` | 所有可识别的戳一戳事件。 |
| `poke_except_bot` | 戳别人，或戳的目标无法确认。 |
| `interaction` | 需选择 `types`，列表内任一项满足即可。省略 `types` 默认为 `["at_bot", "reply_to_bot"]`。 |

`interaction.types` 可填 `nickname`（本条外层文本含主程序昵称或别名）、`at_bot`（外层真实 @ 指向当前 bot）、`reply_to_bot`（引用原作者是 bot）、`poke_bot`（结构化目标确认为 bot）、`message`（普通用户消息）。引用查询失败时仅该项不命中。如果适配器为引用附带了真实的外层 @，该段也会参与 @ 判断。

普通文本正则会按顺序拼接相邻文本段，按转发节点分别检查，不跨节点拼接，不匹配图片描述或 OCR。转发解析深度最多 64 层。**要表达“有图并含关键词”**，把两个条件放同一条规则。**要表达“有图或含关键词”**，写两条规则，动作相同即可。

**动作 `action.kind`**

| 值 | 结果与参数 |
|---|---|
| `block` | 终止后续处理。省略 `reply` 时静默；填写 `reply = "第一行\n第二行"` 时向原会话发送一次固定文本。发送失败仍拦截。 |
| `context_only` | 以原作者和消息 ID 追加一次到当前上下文，然后中止入站处理，本条不唤醒 Planner。追加失败仍拦截。运行时上下文不保证重启后保留。 |
| `native` | 放行给 MaiBot，由原生门控决定是否回复。 |
| `follow_up` | 需有 `interaction` 条件。主动互动开启窗口，同人后续普通追问也可进入原生互动流程。可设 `window_seconds`（默认 60）和 `max_active_seconds`（默认 300）。正整数秒，0 或省略使用默认；总上限不得小于单次时长。 |

窗口按规则、机器人账号、会话和发送者隔离。再次命中开窗条件时续期；窗口内的普通发言仅更新最近活跃时间。麦麦在该会话成功发言时，为仍在窗口且最近说过话的参与者续期，每轮受总上限约束。窗口只替代该条留步规则的互动条件；同条规则的其他条件仍要满足。有效的插件配置热更新、卸载或重启会清空窗口。

## 常见改法

下面的 `use` 可以直接配合完整示例。组名只作引用，具体动作以组内规则为准。

| 想要的效果 | route 怎么写 |
|---|---|
| 所有群和私聊都完全屏蔽某些 QQ | 第一条 route 填 `group = ["*"]`、`user = ["10001", "10002"]`、`use = ["block"]`。这些账号也不能触发 `/ping`。 |
| 私聊走原生 | `group = ["private"]`、`user = ["*"]`、`use = ["common", "native"]`。放在通配项之前。 |
| 指定群每条消息都要直接 @ 或引用，其他消息仅上下文 | `use = ["common", "at_bot", "context_only"]`。若要完全丢弃其余消息，末项换成 `"block"`。 |
| 普通消息原生门控，允许连续追问 | `use = ["common", "follow_up", "native"]`。多个群可共用这条 route。 |
| 其他群普通消息仅上下文，互动时允许连续追问 | 最后一条 route 填 `group = ["*"]`、`user = ["*"]`、`use = ["common", "follow_up", "context_only"]`。 |

`common` 同时存放通用过滤和固定回复命令。当前部署中先查过滤，最后查 `/ping`。新增关键词，可在 `common = [ … ]` 内 `/ping` 前插入下面一行：

~~~toml
  { name = "过滤测试口令", condition = [{ kind = "text_regex", pattern = '测试口令' }], action = { kind = "block" } },
~~~

**给现有策略加群**：直接编辑那条 route 的 `group`，不用复制整套规则。多行写法方便逐个注释：

~~~toml
group = [
  "20002",  # 示例原生门控群
  "20003",  # 新增的群
]
~~~

**只改某个群的开窗方式**：例如想让该群的引用走仅上下文，但直接 @ 可以原生放行，可在 `[rule_groups]` 下复制 `at_bot` 为 `mention_only`，将 `types` 缩为 `["at_bot"]`，再把该群 `use` 中的 `"at_bot"` 换成 `"mention_only"`。

## 保存、生效与升级

Docker 主机配置文件为 `/path/to/maibot/data/MaiMBot/plugins/local_watch-dog/config.toml`，对应容器内 `/MaiMBot/plugins/local_watch-dog/config.toml`。WebUI 的 route 表单支持编辑 `group`、`user`、`use`。动态命名的规则组和行尾群名注释请在原始 TOML 编辑器或文件中维护。

日常改规则只需保存配置。日志出现“Watch Dog 规则已更新”表示加载成功。若看到“配置校验失败”，先看错误中的字段和规则组名：检查 `use` 拼写、正则及窗口时长；非法热更新会保留上一份有效配置。最近的命中日志能看到组名、规则、动作和消息 ID。可以在测试会话发 `/ping` 验证固定回复，再用普通消息、直接 @、引用分别核对动作。

~~~sh
cd /path/to/maibot
docker compose logs --since 3m core | grep -i 'watch.dog'
~~~

从 v0.4 升级时，先备份完整插件目录（含运行时 config.toml），再将平铺的 rules 整理为 routes 与 rule_groups，连同 v0.5 代码一起替换后执行 `docker compose restart core`。确认日志显示 `Watch Dog v0.5.0 已加载` 和预期路由数。需要回退时恢复整份备份并重启。发布 ZIP 解压到插件目录根部，示例配置另存为 `config.example.toml`。历史工具 `migrate.py` 专用于 v0.3 → v0.4。

运行测试：`python -m unittest discover -s tests -v`。留步行为参考 gsskk/maibot-plugin-followup，许可见 LICENSE.followup。
