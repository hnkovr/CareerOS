"""Prompt registry: shipped library (``career/prompts``) overlaid by the vault's ``prompts/``."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import ValidationError

from careeros.modules.vault.schema import Prompt
from careeros.modules.vault.yamlio import load_yaml


class PromptNotFound(KeyError):
    pass


class PromptRenderError(ValueError):
    pass


@dataclass(frozen=True)
class LoadedPrompt:
    prompt: Prompt
    source: str  # library | vault
    path: Path


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_id: str
    version: int
    system: str | None
    user: str
    output_schema: str | None
    provider_preferences: tuple[str, ...]


def _env() -> Environment:
    env = Environment(
        undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, autoescape=False
    )
    env.filters["bullets"] = lambda items: "\n".join(f"- {i}" for i in items)
    return env


class PromptRegistry:
    def __init__(self, library_dir: Path, vault_prompts_dir: Path | None = None) -> None:
        self.library_dir = Path(library_dir)
        self.vault_prompts_dir = Path(vault_prompts_dir) if vault_prompts_dir else None
        self._env = _env()
        self._cache: dict[str, LoadedPrompt] | None = None

    def _load_dir(self, root: Path, source: str, into: dict[str, LoadedPrompt]) -> None:
        if not root.exists():
            return
        for path in sorted(root.rglob("*.yaml")):
            raw = load_yaml(path)
            if not isinstance(raw, dict):
                continue
            raw.setdefault("id", path.stem)
            raw.setdefault("area", path.parent.name)
            try:
                prompt = Prompt.model_validate(raw)
            except ValidationError as exc:
                raise PromptRenderError(f"{path}: {exc}") from exc
            into[prompt.id] = LoadedPrompt(prompt, source, path)

    def all(self) -> dict[str, LoadedPrompt]:
        if self._cache is None:
            loaded: dict[str, LoadedPrompt] = {}
            self._load_dir(self.library_dir, "library", loaded)
            if self.vault_prompts_dir:
                self._load_dir(self.vault_prompts_dir, "vault", loaded)
            self._cache = loaded
        return self._cache

    def reload(self) -> None:
        self._cache = None

    def get(self, prompt_id: str) -> LoadedPrompt:
        try:
            return self.all()[prompt_id]
        except KeyError as exc:
            raise PromptNotFound(prompt_id) from exc

    def render(self, prompt_id: str, **variables: object) -> RenderedPrompt:
        loaded = self.get(prompt_id)
        p = loaded.prompt
        missing = [name for name in p.inputs if name not in variables]
        if missing:
            raise PromptRenderError(f"prompt '{prompt_id}' missing inputs: {', '.join(missing)}")
        try:
            user = self._env.from_string(p.template).render(**variables).strip()
            system = (
                self._env.from_string(p.system).render(**variables).strip() if p.system else None
            )
        except TemplateError as exc:
            raise PromptRenderError(f"prompt '{prompt_id}': {exc}") from exc
        return RenderedPrompt(
            prompt_id=p.id,
            version=p.version,
            system=system,
            user=user,
            output_schema=p.output_schema,
            provider_preferences=tuple(p.provider_preferences),
        )
