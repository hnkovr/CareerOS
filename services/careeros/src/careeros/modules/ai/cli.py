"""``careeros ai ...`` commands."""

from __future__ import annotations

import asyncio
import json

import typer

from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.schemas import BundleRequest, GenerateRequest

app = typer.Typer(help="AI gateway: providers, prompts, bundles")


@app.command()
def providers() -> None:
    """List configured providers."""
    for info in build_ai_service().provider_infos():
        flag = "configured" if info.configured else "missing key"
        print(f"{info.name:12s} {info.kind:18s} {info.default_model:30s} {flag}")


@app.command()
def prompts() -> None:
    """List prompts (library + vault overlay)."""
    for p in build_ai_service().prompt_infos():
        print(f"{p.id:32s} v{p.version:<3d} {p.area:12s} {p.source:8s} inputs={','.join(p.inputs)}")


@app.command()
def render(
    prompt_id: str, var: list[str] = typer.Option([], "--var", help="key=value (JSON values ok)")
) -> None:
    """Render a prompt with variables and print it (Mode B text)."""
    inputs = {}
    for kv in var:
        k, _, v = kv.partition("=")
        try:
            inputs[k] = json.loads(v)
        except json.JSONDecodeError:
            inputs[k] = v
    out = asyncio.run(build_ai_service().bundle(BundleRequest(prompt_id=prompt_id, inputs=inputs)))
    print(out.text)


@app.command()
def ping(
    provider: str | None = typer.Option(None, "--provider"),
    prompt: str = "Reply with the single word OK.",
) -> None:
    """Send a tiny request to a provider to verify credentials."""
    svc = build_ai_service()
    prov = svc.providers.get(provider)

    async def _go() -> None:
        resp = await prov.generate(GenerateRequest(prompt=prompt, max_tokens=16))
        print(
            f"{resp.provider}/{resp.model}: {resp.text.strip()} "
            f"({resp.usage.total} tokens, {resp.latency_ms} ms)"
        )

    asyncio.run(_go())
