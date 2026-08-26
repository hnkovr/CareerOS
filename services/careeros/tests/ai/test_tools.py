"""ADR-014 tool loop: gateway-owned turns, tool execution, validation retries, ledger; and the
adapters' wire-format mappings (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from careeros.core.config import Settings
from careeros.modules.ai.prompts import PromptRegistry
from careeros.modules.ai.provider import AIOutputInvalid, AIUnavailable
from careeros.modules.ai.providers.anthropic_provider import AnthropicProvider
from careeros.modules.ai.providers.fake import FakeProvider
from careeros.modules.ai.providers.openai_compat import OpenAICompatibleProvider
from careeros.modules.ai.registry import ProviderRegistry
from careeros.modules.ai.schemas import ChatMessage, ToolCall, ToolChatRequest, ToolSpec, Usage
from careeros.modules.ai.service import AIService
from tests.conftest import CAREER_DIR


class Answer(BaseModel):
    answer: str = Field(min_length=1)
    derived_from: list[str] = Field(default_factory=list)


ECHO = ToolSpec(name="echo", description="echo", input_schema={"type": "object"})


@pytest.fixture
def prompts(tmp_path: Path) -> PromptRegistry:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "asktool.yaml").write_text(
        "id: asktool\nversion: 1\narea: test\npurpose: t\ninputs: [q]\noutput_schema: Answer\n"
        "system: Use tools.\ntemplate: |\n  Q: {{ q }}\n"
    )
    return PromptRegistry(CAREER_DIR / "prompts", tmp_path / "p")


def _service(settings: Settings, prompts: PromptRegistry, fake: FakeProvider) -> AIService:
    return AIService(settings, ProviderRegistry({"fake": fake}, "fake", []), prompts)


def _turns(*items: dict[str, Any]):  # type: ignore[no-untyped-def]
    it = iter(items)
    return lambda req: next(it)


async def test_with_tools_executes_calls_and_feeds_results_back(
    settings: Settings, prompts: PromptRegistry
) -> None:
    fake = FakeProvider(
        tool_responder=_turns(
            {"tool_calls": [{"name": "echo", "arguments": {"x": 1}}]},
            {"text": 'Here you go: {"answer": "got 1", "derived_from": ["f1"]}'},
        )
    )
    executed: list[ToolCall] = []

    async def execute(call: ToolCall) -> str:
        executed.append(call)
        return json.dumps({"echo": call.arguments})

    res = await _service(settings, prompts, fake).with_tools(
        "asktool", {"q": "hi"}, [ECHO], execute, Answer
    )
    assert res.data.answer == "got 1" and res.turns == 2
    assert [c.name for c in executed] == ["echo"] and executed[0].arguments == {"x": 1}
    assert len(res.steps) == 1 and res.steps[0].ok and '"echo"' in res.steps[0].result_preview
    # the second turn saw the assistant's tool call and the tool result, in order
    msgs = fake.tool_requests[-1].messages
    assert [m.role for m in msgs] == ["user", "assistant", "tool"]
    assert msgs[1].tool_calls[0].name == "echo" and msgs[2].tool_call_id == msgs[1].tool_calls[0].id
    assert fake.tool_requests[-1].tools[0].name == "echo"
    assert res.response.usage.input_tokens > 0  # usage is summed over turns


async def test_with_tools_tool_errors_go_back_to_the_model(
    settings: Settings, prompts: PromptRegistry
) -> None:
    fake = FakeProvider(
        tool_responder=_turns(
            {"tool_calls": [{"name": "boom", "arguments": {}}]},
            {"text": '{"answer": "recovered"}'},
        )
    )

    async def execute(call: ToolCall) -> str:
        raise KeyError(f"unknown tool {call.name}")

    res = await _service(settings, prompts, fake).with_tools(
        "asktool", {"q": "hi"}, [ECHO], execute, Answer
    )
    assert res.data.answer == "recovered"
    assert res.steps[0].ok is False and "KeyError" in res.steps[0].result_preview
    assert "error" in (fake.tool_requests[-1].messages[-1].content or "")


async def test_with_tools_retries_invalid_final_then_gives_up(
    settings: Settings, prompts: PromptRegistry
) -> None:
    fake = FakeProvider(tool_responder=lambda req: {"text": "not json at all"})

    async def execute(call: ToolCall) -> str:
        return "{}"

    with pytest.raises(AIOutputInvalid) as exc:
        await _service(settings, prompts, fake).with_tools(
            "asktool", {"q": "hi"}, [ECHO], execute, Answer
        )
    assert len(exc.value.errors) == settings.ai_structured_max_retries + 1
    assert "rejected by the validator" in (fake.tool_requests[-1].messages[-1].content or "")

    # a valid answer after one rejection succeeds and reports the retry
    fake = FakeProvider(tool_responder=_turns({"text": "nope"}, {"text": '{"answer": "ok"}'}))
    res = await _service(settings, prompts, fake).with_tools(
        "asktool", {"q": "hi"}, [ECHO], execute, Answer
    )
    assert res.data.answer == "ok" and res.turns == 2


async def test_with_tools_max_steps_and_no_tool_support(
    settings: Settings, prompts: PromptRegistry
) -> None:
    fake = FakeProvider(tool_responder=lambda req: {"tool_calls": [{"name": "echo"}]})

    async def execute(call: ToolCall) -> str:
        return "{}"

    with pytest.raises(AIOutputInvalid, match="no final answer after 2 turns"):
        await _service(settings, prompts, fake).with_tools(
            "asktool", {"q": "hi"}, [ECHO], execute, Answer, max_steps=2
        )
    with pytest.raises(AIUnavailable):  # FakeProvider without a tool responder = no tool support
        await _service(settings, prompts, FakeProvider()).with_tools(
            "asktool", {"q": "hi"}, [ECHO], execute, Answer
        )


# ----------------------------------------------------------------------------- adapters

CONVO = [
    ChatMessage(role="user", content="hi"),
    ChatMessage(
        role="assistant",
        content="looking",
        tool_calls=[
            ToolCall(id="c1", name="echo", arguments={"x": 1}),
            ToolCall(id="c2", name="echo"),
        ],
    ),
    ChatMessage(role="tool", content='{"a":1}', tool_call_id="c1"),
    ChatMessage(role="tool", content='{"b":2}', tool_call_id="c2"),
    ChatMessage(role="assistant", tool_calls=[ToolCall(id="c3", name="echo")]),
    ChatMessage(role="tool", content="{}", tool_call_id="c3"),
]


def test_anthropic_tool_mapping() -> None:
    msgs = AnthropicProvider.tool_messages(CONVO)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[1]["content"][0] == {"type": "text", "text": "looking"}
    assert msgs[1]["content"][1]["type"] == "tool_use" and msgs[1]["content"][1]["id"] == "c1"
    # two consecutive tool results share one user turn (roles must alternate)
    results = msgs[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["c1", "c2"]
    assert msgs[3]["content"] == [{"type": "tool_use", "id": "c3", "name": "echo", "input": {}}]
    assert AnthropicProvider.tool_specs([ECHO])[0]["input_schema"] == {"type": "object"}

    msg = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="calling"),
            SimpleNamespace(type="tool_use", id="t1", name="echo", input={"x": 2}),
        ],
        model="claude-x",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        stop_reason="tool_use",
    )
    turn = AnthropicProvider.parse_tool_turn(msg, provider="anthropic", latency_ms=3)
    assert turn.text == "calling" and turn.tool_calls[0].arguments == {"x": 2}
    assert turn.response.usage.total == 15 and turn.response.stop_reason == "tool_use"


def test_openai_tool_mapping() -> None:
    msgs = OpenAICompatibleProvider.tool_messages("sys", CONVO)
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["tool_calls"][0]["function"]["name"] == "echo"
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"x": 1}
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"a":1}'}
    assert msgs[5]["content"] is None and msgs[5]["tool_calls"][0]["id"] == "c3"
    spec = OpenAICompatibleProvider.tool_specs([ECHO])[0]
    assert spec["type"] == "function" and spec["function"]["parameters"] == {"type": "object"}

    completion = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            function=SimpleNamespace(name="echo", arguments='{"x": 3}'),
                        ),
                        SimpleNamespace(
                            id="call_2", function=SimpleNamespace(name="echo", arguments="{bad")
                        ),
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        model="gpt-x",
    )
    turn = OpenAICompatibleProvider.parse_tool_turn(
        completion, provider="openai", model="gpt-x", latency_ms=1, usage=Usage()
    )
    assert turn.text == "" and turn.tool_calls[0].arguments == {"x": 3}
    assert turn.tool_calls[1].arguments == {"_raw": "{bad"}  # malformed arguments never crash


async def test_fake_provider_tool_turn_shapes() -> None:
    fake = FakeProvider(tool_responder=lambda req: "plain text answer")
    req = ToolChatRequest(messages=[ChatMessage(role="user", content="q")], tools=[ECHO])
    turn = await fake.chat_with_tools(req)
    assert turn.text == "plain text answer" and not turn.tool_calls
    assert turn.response.stop_reason == "end_turn"
