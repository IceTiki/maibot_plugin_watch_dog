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
    config_version: Literal["0.4.0"] = ui("0.4.0", label="配置版本", disabled=True)
    enabled: bool = ui(True, label="启用 Watch Dog")


class FollowUpSettings(SettingsBase):
    __ui_label__ = "跟进窗口"
    window_seconds: int = ui(60, label="单次窗口（秒）")
    max_active_seconds: int = ui(300, label="本轮上限（秒）")

    @field_validator("window_seconds", "max_active_seconds", mode="before")
    @classmethod
    def positive_seconds(cls, value):
        if type(value) is not int or value <= 0:
            raise ValueError("窗口时长必须是正整数")
        return value

    @model_validator(mode="after")
    def valid_window(self):
        if self.max_active_seconds < self.window_seconds:
            raise ValueError("本轮上限不能短于单次窗口")
        return self


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
    window_seconds: int = ui(0, label="窗口覆盖（0 继承）")
    max_active_seconds: int = ui(0, label="上限覆盖（0 继承）")

    @field_validator("window_seconds", "max_active_seconds", mode="before")
    @classmethod
    def nonnegative_seconds(cls, value):
        if type(value) is not int or value < 0:
            raise ValueError("动作时长必须是非负整数，0 表示继承")
        return value

    @model_validator(mode="after")
    def action_parameters(self):
        if self.reply and self.kind != "block":
            raise ValueError("固定回复只支持 block 动作")
        if self.kind != "follow_up" and (self.window_seconds or self.max_active_seconds):
            raise ValueError("窗口参数只支持 follow_up 动作")
        return self


class RuleSettings(SettingsBase):
    name: str = ui("新规则", label="规则名称")
    enabled: bool = ui(True, label="启用此规则")
    group: list[str] = ui(default_factory=lambda: ["*"], label="群范围（* 含私聊；private 仅私聊）")
    user: list[str] = ui(default_factory=lambda: ["*"], label="发送者 QQ（* 表示全部）")
    condition: list[Condition] = ui(default_factory=list, label="额外条件（全部满足）")
    action: Action = ui(default_factory=Action, label="动作（在原始 TOML 中编辑）")

    @field_validator("name")
    @classmethod
    def named(cls, value):
        if not value.strip():
            raise ValueError("规则名称不能为空")
        return value

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

    @model_validator(mode="after")
    def followup_requires_interaction(self):
        if self.action.kind == "follow_up" and not any(c.kind == "interaction" for c in self.condition):
            raise ValueError("follow_up 规则必须含 interaction 条件")
        return self


class WatchDogConfig(SettingsBase):
    plugin: PluginSettings = Field(default_factory=PluginSettings)
    follow_up: FollowUpSettings = Field(default_factory=FollowUpSettings)
    rules: list[RuleSettings] = ui(
        default_factory=list, label="按顺序匹配的规则",
        hint="第一条完整命中后结束。动作 action 在原始 TOML 编辑，可填 block / follow_up / context_only / native。")

    @model_validator(mode="after")
    def effective_durations(self):
        for rule in self.rules:
            window = rule.action.window_seconds or self.follow_up.window_seconds
            maximum = rule.action.max_active_seconds or self.follow_up.max_active_seconds
            if maximum < window:
                raise ValueError(f"{rule.name}：本轮上限不能短于单次窗口")
        return self


def complete_rule_schema(schema):
    """Complete the SDK's shallow list schema. Action uses the raw TOML editor.

    Object fields omitted from this visual form survive existing-object edits.
    New rows default to disabled until their action is configured.
    """
    for section in schema.get("sections", {}).values():
        field = section.get("fields", {}).get("rules")
        if field is None:
            continue
        fields = field["item_fields"]
        fields.pop("action", None)
        fields["enabled"]["default"] = False
        fields["group"].update(item_type="string")
        fields["user"].update(item_type="string")
        fields["condition"].update(item_type="object", item_fields={
            "kind": {"type": "select", "label": "条件类型", "default": "interaction",
                     "choices": ["text_regex", "image", "forward", "poke", "poke_except_bot", "interaction"]},
            "pattern": {"type": "string", "label": "正则（仅 text_regex 使用）", "default": ""},
            "types": {"type": "select", "label": "互动类型（任一命中）", "multiple": True,
                      "default": ["at_bot", "reply_to_bot"],
                      "choices": ["nickname", "at_bot", "reply_to_bot", "poke_bot", "message"]},
        })
    return schema
