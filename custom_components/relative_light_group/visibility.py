"""Entity registry visibility helpers for Relative Light Group members."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


def set_member_visibility(
    hass: HomeAssistant,
    entity_ids: list[str],
    hidden_by: er.RegistryEntryHider | None,
) -> None:
    """Set registry visibility for member entities that can be resolved."""
    registry = er.async_get(hass)
    for entity_id in entity_ids:
        resolved = er.async_resolve_entity_id(registry, entity_id)
        if not resolved or resolved not in registry.entities:
            continue
        registry.async_update_entity(resolved, hidden_by=hidden_by)


def restore_member_visibility_if_hidden_by_integration(
    hass: HomeAssistant,
    entity_ids: list[str],
) -> None:
    """Unhide member entities only when this integration hid them."""
    registry = er.async_get(hass)
    for entity_id in entity_ids:
        resolved = er.async_resolve_entity_id(registry, entity_id)
        if not resolved or resolved not in registry.entities:
            continue
        registry_entry = registry.async_get(resolved)
        if (
            registry_entry
            and registry_entry.hidden_by == er.RegistryEntryHider.INTEGRATION
        ):
            registry.async_update_entity(resolved, hidden_by=None)
