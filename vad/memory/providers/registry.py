from __future__ import annotations

import inspect
from typing import Any, Callable

from vad.memory.config import MemoryProviderConfig, MemoryProviderKind
from vad.memory.provider import MemoryProviderProtocol
from vad.memory.providers.generic_endpoint import GenericEndpointMemoryProvider
from vad.memory.providers.graphiti import GraphitiMemoryProvider
from vad.memory.providers.local import LocalMemoryProvider
from vad.memory.providers.mem0 import Mem0MemoryProvider
from vad.memory.providers.null import NullMemoryProvider

CustomProviderFactory = Callable[[MemoryProviderConfig], MemoryProviderProtocol]

_CUSTOM_PROVIDER_REGISTRY: dict[str, CustomProviderFactory] = {}


def register_custom_provider(
    name: str,
    factory: CustomProviderFactory,
) -> None:
    """Register a user-supplied custom in-process memory provider factory."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("Custom memory provider name must be non-empty")
    if not callable(factory):
        raise TypeError("Custom memory provider factory must be callable")
    _CUSTOM_PROVIDER_REGISTRY[normalized] = factory


def unregister_custom_provider(name: str) -> None:
    """Remove a previously registered custom memory provider factory if present."""
    _CUSTOM_PROVIDER_REGISTRY.pop(name.strip(), None)


def list_custom_providers() -> list[str]:
    """Return sorted names of registered custom memory provider factories."""
    return sorted(_CUSTOM_PROVIDER_REGISTRY.keys())


def _invoke_custom_factory(
    factory: CustomProviderFactory,
    config: MemoryProviderConfig,
    transport_kwargs: dict[str, Any],
) -> MemoryProviderProtocol:
    if not transport_kwargs:
        return factory(config)
    sig = inspect.signature(factory)
    accepts_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if accepts_kwargs:
        return factory(config, **transport_kwargs)  # type: ignore[call-arg]
    supported = {
        key: val for key, val in transport_kwargs.items() if key in sig.parameters
    }
    if supported:
        return factory(config, **supported)  # type: ignore[call-arg]
    return factory(config)


def create_memory_provider(
    config: MemoryProviderConfig,
    **transport_kwargs: Any,
) -> MemoryProviderProtocol:
    """Instantiate a MemoryProviderProtocol implementation from MemoryProviderConfig."""
    custom_name = config.options.get("custom_provider")
    if custom_name is not None:
        key = str(custom_name).strip()
        if key not in _CUSTOM_PROVIDER_REGISTRY:
            raise KeyError(f"Custom memory provider {key!r} is not registered")
        return _invoke_custom_factory(
            _CUSTOM_PROVIDER_REGISTRY[key],
            config,
            transport_kwargs,
        )

    if config.provider.value in _CUSTOM_PROVIDER_REGISTRY:
        return _invoke_custom_factory(
            _CUSTOM_PROVIDER_REGISTRY[config.provider.value],
            config,
            transport_kwargs,
        )

    if config.provider == MemoryProviderKind.NULL:
        return NullMemoryProvider()

    if config.provider == MemoryProviderKind.LOCAL:
        return LocalMemoryProvider(local_path=config.local_path)

    if config.provider == MemoryProviderKind.CONDENSATE_CORE:
        from vad.memory.providers.condensate_core import CondensateCoreProvider

        return CondensateCoreProvider(config, **transport_kwargs)

    if config.provider == MemoryProviderKind.MEM0:
        return Mem0MemoryProvider(config, **transport_kwargs)

    if config.provider == MemoryProviderKind.GRAPHITI:
        return GraphitiMemoryProvider(config, **transport_kwargs)

    if config.provider in (
        MemoryProviderKind.CUSTOM_HTTP,
        MemoryProviderKind.CUSTOM_MCP,
    ):
        return GenericEndpointMemoryProvider(config, **transport_kwargs)

    raise ValueError(f"Unsupported memory provider kind: {config.provider}")
