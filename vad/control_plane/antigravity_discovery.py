from __future__ import annotations

from pydantic import BaseModel, Field

from vad.control_plane.generic_mcp_package import GenericMCPPackage, build_generic_mcp_package
from vad.control_plane.plugins import PluginTargetClient


class AntigravityDiscovery(BaseModel):
    target_client: PluginTargetClient
    first_class_surface_found: bool
    verified_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    checked_sources: tuple[str, ...]
    evidence_summary: str = Field(min_length=1)
    fallback_package: GenericMCPPackage


def discover_antigravity_integration() -> AntigravityDiscovery:
    return AntigravityDiscovery(
        target_client=PluginTargetClient.ANTIGRAVITY,
        first_class_surface_found=False,
        verified_on="2026-07-03",
        checked_sources=(
            "Official Antigravity product site",
            "Public Antigravity documentation and news search for MCP/config/plugin surfaces",
            "Local VAD repository integration guides and client registry",
        ),
        evidence_summary=(
            "No current public Antigravity documentation was found for a stable local MCP, "
            "plugin, or config file surface. Use the generic stdio MCP package until "
            "Antigravity documents a first-class local integration contract."
        ),
        fallback_package=build_generic_mcp_package(),
    )
