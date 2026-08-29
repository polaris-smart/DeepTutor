"""Tests for the Volcengine Agent Plan provider presets.

Agent Plan keys only authenticate against /api/plan/v3 (the plain ark
gateway rejects them), so every preset below must carry the plan base URL.
"""

from deeptutor.services.config.embedding_endpoint import (
    EMBEDDING_PROVIDER_ALIASES,
    EMBEDDING_PROVIDER_DEFAULT_ENDPOINTS,
)
from deeptutor.services.config.provider_runtime import (
    EMBEDDING_PROVIDERS,
    IMAGEGEN_PROVIDERS,
    VIDEOGEN_PROVIDERS,
    _canonical_embedding_provider_name,
    _canonical_generation_provider,
)
from deeptutor.services.provider_registry import (
    PROVIDERS,
    canonical_provider_name,
    find_by_name,
)

PLAN_BASE = "https://ark.cn-beijing.volces.com/api/plan/v3"


def _spec(name: str):
    return next(s for s in PROVIDERS if s.name == name)


def test_llm_registry_has_agent_plan_with_plan_base_url() -> None:
    spec = find_by_name("volcengine_agent_plan")
    assert spec is not None
    assert spec.default_api_base == PLAN_BASE
    assert spec.backend == "openai_compat"
    assert spec.is_gateway is True
    assert spec.thinking_style == "thinking_type"


def test_llm_agent_plan_outranks_plain_volcengine_on_base_detection() -> None:
    # Order is priority: the plan spec must precede "volcengine", whose
    # "volces" keyword would otherwise swallow plan URLs.
    assert PROVIDERS.index(_spec("volcengine_agent_plan")) < PROVIDERS.index(
        _spec("volcengine")
    )


def test_llm_agent_plan_aliases_resolve() -> None:
    assert canonical_provider_name("volcengine-agent-plan") == "volcengine_agent_plan"
    assert canonical_provider_name("volcengineagentplan") == "volcengine_agent_plan"
    assert canonical_provider_name("volcengine_plan") == "volcengine_agent_plan"
    assert canonical_provider_name("ark_plan") == "volcengine_agent_plan"


def test_embedding_provider_preset() -> None:
    spec = EMBEDDING_PROVIDERS["volcengine_agent_plan"]
    assert spec.default_api_base == EMBEDDING_PROVIDER_DEFAULT_ENDPOINTS[
        "volcengine_agent_plan"
    ]
    assert spec.default_api_base == f"{PLAN_BASE}/embeddings"
    assert spec.default_model == "doubao-embedding-vision"
    assert spec.default_dim == 2048
    assert spec.multimodal is True


def test_embedding_canonical_binding_resolution() -> None:
    assert _canonical_embedding_provider_name("volcengine_agent_plan") == (
        "volcengine_agent_plan"
    )
    assert _canonical_embedding_provider_name("volcengine-plan") == (
        "volcengine_agent_plan"
    )
    assert _canonical_embedding_provider_name("ark_plan") == "volcengine_agent_plan"
    # Unknown names must not silently fall back to a wrong provider.
    assert _canonical_embedding_provider_name("volcengine") is None


def test_embedding_alias_table_covers_plan_spelling() -> None:
    assert EMBEDDING_PROVIDER_ALIASES["volcengine_plan"] == "volcengine_agent_plan"


def test_imagegen_provider_preset() -> None:
    spec = IMAGEGEN_PROVIDERS["volcengine_agent_plan"]
    assert spec.default_api_base == PLAN_BASE
    assert spec.default_model == "doubao-seedream-5.0-lite"
    assert spec.adapter == "openai_compat"


def test_videogen_provider_preset() -> None:
    spec = VIDEOGEN_PROVIDERS["volcengine_agent_plan"]
    assert spec.default_api_base == PLAN_BASE
    # Hyphenated spelling: the dotted "2.0-fast" name is rejected by Ark.
    assert spec.default_model == "doubao-seedance-2-0-fast"
    assert spec.adapter == "async_task"


def test_generation_canonical_resolution() -> None:
    assert _canonical_generation_provider(
        "volcengine-agent-plan", IMAGEGEN_PROVIDERS
    ) == "volcengine_agent_plan"
    assert _canonical_generation_provider(
        "volcengine_plan", VIDEOGEN_PROVIDERS
    ) == "volcengine_agent_plan"
    assert _canonical_generation_provider("ark_plan", VIDEOGEN_PROVIDERS) == (
        "volcengine_agent_plan"
    )
