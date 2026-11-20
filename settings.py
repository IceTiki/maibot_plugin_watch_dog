"""Typed TOML settings and plugin-owned WebUI schema."""
from typing import Literal
from maibot_sdk import Field, PluginConfigBase
from pydantic import ConfigDict, ValidationError, field_validator, model_validator
import re

Interaction = Literal["nickname", "at_bot", "reply_to_bot", "poke_bot", "message"]


def ui(default=None, *, label, hint="", default_factory=None, **extra):
    value = {"default_factory": default_factory} if default_factory else {"default": default}
    return Field(**value, description=hint or label,
                 json_schema_extra={"label": label, "hint": hint or label, **extra})


class SettingsBase(PluginConfigBase):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PluginSettings(SettingsBase):
    __ui_label__ = "插件"
    config_version: Literal["0.5.0"] = ui("0.5.0", label="配置版本", disabled=True)
    enabled: bool = ui(True, label="启用 Watch Dog")


class Condition(SettingsBase):
    kind: Literal["text_regex", "image", "forward", "poke", "poke_except_bot", "interaction"] = ui(
        "interaction", label="条件类型")
    pattern: str = ui("", label="正则表达式", hint="仅 text_regex 使用，执行 search")
    types: list[Interaction] = ui(default_factory=lambda: ["at_bot", "reply_to_bot"],
                                label="互动类型（任一命中）")

    @model_validator(mode="after")
    def validate_condition(self):
        if self.kind == "text_regex":
            if not self.pattern:
                raise ValueError("正则表达式不能为空")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"非法正则：{exc}") from exc
        if self.kind == "interaction" and not self.types:
            raise ValueError("互动类型列表不能为空")
        return self


class Action(SettingsBase):
    kind: Literal["block", "follow_up", "context_only", "native"] = ui("native", label="动作")
    reply: str = ui("", label="固定回复")
    window_seconds: int = ui(0, label="窗口覆盖（0 用默认值）")
    max_active_seconds: int = ui(0, label="上限覆盖（0 用默认值）")

    @field_validator("window_seconds", "max_active_seconds", mode="before")
    @classmethod
    def nonnegative_seconds(cls, value):
        if type(value) is not int or value < 0:
            raise ValueError("动作时长必须是非负整数，0 表示使用默认值")
        return value

    @model_validator(mode="after")
    def action_parameters(self):
        if self.reply and self.kind != "block":
            raise ValueError("固定回复只支持 block 动作")
        if self.kind != "follow_up" and (self.window_seconds or self.max_active_seconds):
            raise ValueError("窗口参数只支持 follow_up 动作")
        return self


class RuleSettings(SettingsBase):
    name: str | None = ui(None, label="规则名称")
    enabled: bool = ui(True, label="启用此规则")
    condition: list[Condition] = ui(default_factory=list, label="额外条件（全部满足）")
    action: Action = ui(default_factory=Action, label="动作（在原始 TOML 中编辑）")

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if value is not None and not value.strip():
            raise ValueError("规则名称不能为空")
        return value

    @model_validator(mode="after")
    def followup_requires_interaction(self):
        if self.action.kind == "follow_up" and not any(
            c.kind == "interaction" for c in self.condition
        ):
            raise ValueError("follow_up 规则必须含 interaction 条件")
        return self


class RouteSettings(SettingsBase):
    group: list[str] = ui(
        default_factory=lambda: ["*"], label="群范围（* 含私聊；private 仅私聊）"
    )
    user: list[str] = ui(default_factory=lambda: ["*"], label="发送者 QQ（* 表示全部）")
    use: list[str] = ui(default_factory=list, label="按顺序使用的规则组")

    @field_validator("group", "user", mode="before")
    @classmethod
    def selectors(cls, value, info):
        if not isinstance(value, list) or not value:
            raise ValueError("群/用户列表不能为空；请明确填写 *")
        allowed = {"*", "private"} if info.field_name == "group" else {"*"}
        result = []
        for item in value:
            if type(item) not in (int, str):
                raise ValueError("ID 必须是字符串或整数")
            item = str(item)
            if item not in allowed and not (item.isascii() and item.isdigit()):
                raise ValueError(f"非法范围值：{item}")
            result.append(item)
        return result

    @field_validator("use")
    @classmethod
    def valid_use(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("use 至少需要一个规则组")
        return value


class WatchDogConfig(SettingsBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    routes: list[RouteSettings] = ui(
        default_factory=list,
        label="按顺序匹配的路由",
        hint="选中第一条匹配的路由，按 use 顺序检查规则组。规则组在原始 TOML 编辑。",
    )
    rule_groups: dict[str, list[RuleSettings]] = ui(
        default_factory=dict, label="规则组"
    )

    @model_validator(mode="after")
    def validate_groups(self):
        for key, rules in self.rule_groups.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
                raise ValueError(f"规则组名须使用英文小写、数字或下划线：{key}")
            for rule in rules:
                if (rule.action.window_seconds or 60) > (
                    rule.action.max_active_seconds or 300
                ):
                    raise ValueError(f"{key}/{rule.name}：本轮上限不能短于单次窗口")
        for i, route in enumerate(self.routes, 1):
            for key in route.use:
                if key not in self.rule_groups:
                    raise ValueError(f"第 {i} 条 route 引用了未定义的规则组：{key}")
        return self


def complete_rule_schema(schema: dict) -> dict:
    """Expose ordered selectors; dynamic rule definitions use the raw TOML editor."""
    for section in schema.get("sections", {}).values():
        fields = section.get("fields", {})
        fields.pop("rule_groups", None)
        if "routes" in fields:
            for key in ("group", "user", "use"):
                fields["routes"]["item_fields"][key]["item_type"] = "string"
    return schema
