"""Services for the Relative Light Group integration.

Public services:
- relative_light_group.set_options
- relative_light_group.add_lights
- relative_light_group.remove_lights
- relative_light_group.set_lights

Validation helpers (also used by the config flow) are exposed at module level.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_ENTITIES
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    service,
)

from .const import (
    BRIGHTNESS_STRATEGIES,
    CONF_ALL,
    CONF_BRIGHTNESS_STRATEGY,
    CONF_DEBOUNCE_ENABLED,
    CONF_DEBOUNCE_TIME,
    CONF_HIDE_MEMBERS,
    CONF_MEMBER_DIAGNOSTICS,
    CONF_REMEMBER_BRIGHTNESS,
    CONF_REMEMBER_ON_STATE,
    CONF_RESTORE_INDIVIDUAL_BRIGHTNESS,
    DOMAIN,
)
from .visibility import (
    restore_member_visibility_if_hidden_by_integration,
    set_member_visibility,
)

_LOGGER = logging.getLogger(__name__)

LIGHT_DOMAIN = "light"

SERVICE_SET_OPTIONS = "set_options"
SERVICE_ADD_LIGHTS = "add_lights"
SERVICE_REMOVE_LIGHTS = "remove_lights"
SERVICE_SET_LIGHTS = "set_lights"

DATA_LOCKS = "locks"

OPTION_KEYS: tuple[str, ...] = (
    CONF_ALL,
    CONF_HIDE_MEMBERS,
    CONF_REMEMBER_ON_STATE,
    CONF_RESTORE_INDIVIDUAL_BRIGHTNESS,
    CONF_REMEMBER_BRIGHTNESS,
    CONF_BRIGHTNESS_STRATEGY,
    CONF_MEMBER_DIAGNOSTICS,
    CONF_DEBOUNCE_ENABLED,
    CONF_DEBOUNCE_TIME,
)

SET_OPTIONS_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Optional(CONF_ALL): cv.boolean,
        vol.Optional(CONF_HIDE_MEMBERS): cv.boolean,
        vol.Optional(CONF_REMEMBER_ON_STATE): cv.boolean,
        vol.Optional(CONF_RESTORE_INDIVIDUAL_BRIGHTNESS): cv.boolean,
        vol.Optional(CONF_REMEMBER_BRIGHTNESS): cv.boolean,
        vol.Optional(CONF_BRIGHTNESS_STRATEGY): vol.In(BRIGHTNESS_STRATEGIES),
        vol.Optional(CONF_MEMBER_DIAGNOSTICS): cv.boolean,
        vol.Optional(CONF_DEBOUNCE_ENABLED): cv.boolean,
        vol.Optional(CONF_DEBOUNCE_TIME): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=10000)
        ),
    }
)

MEMBER_LIST_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(CONF_ENTITIES): vol.All(
            cv.ensure_list, [cv.entity_id], vol.Length(min=1)
        ),
    }
)


def _get_lock(hass: HomeAssistant, entry_id: str) -> asyncio.Lock:
    """Return (creating if needed) an asyncio.Lock dedicated to a config entry."""
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_LOCKS, {}
    )
    if entry_id not in locks:
        locks[entry_id] = asyncio.Lock()
    return locks[entry_id]


@callback
def _entity_relative_light_group_entry(
    hass: HomeAssistant, entity_id: str
) -> ConfigEntry | None:
    """Return the relative_light_group config entry that owns the entity, if any."""
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id)
    if reg_entry is None or reg_entry.config_entry_id is None:
        return None
    config_entry = hass.config_entries.async_get_entry(reg_entry.config_entry_id)
    if config_entry is None or config_entry.domain != DOMAIN:
        return None
    return config_entry


@callback
def validate_member_entities(
    hass: HomeAssistant,
    entity_ids: list[Any],
    own_entity_ids: set[str] | None = None,
) -> list[str]:
    """Validate proposed member entity_ids.

    Rules enforced:
    - element is a string formatted as `light.<object>`,
    - entity exists either in the entity registry or in `hass.states`,
    - entity is not the target group itself (`own_entity_ids`),
    - entity does not belong to another relative_light_group (anti-circularity),
    - duplicates are removed (first occurrence kept).

    Raises `ServiceValidationError` with a translation key on the first failure.
    Returns the deduplicated, validated list preserving caller's order.
    """
    own = own_entity_ids or set()
    seen: set[str] = set()
    deduped: list[str] = []

    for raw in entity_ids:
        if not isinstance(raw, str) or not raw:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_entity",
                translation_placeholders={"entity_id": str(raw)},
            )
        eid = raw
        if eid in seen:
            continue
        seen.add(eid)

        domain, _, _ = eid.partition(".")
        if domain != LIGHT_DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_domain",
                translation_placeholders={"entity_id": eid},
            )

        if eid in own:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="self_reference",
                translation_placeholders={"entity_id": eid},
            )

        registry = er.async_get(hass)
        if registry.async_get(eid) is None and hass.states.get(eid) is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entity",
                translation_placeholders={"entity_id": eid},
            )

        if _entity_relative_light_group_entry(hass, eid) is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="nested_relative_group",
                translation_placeholders={"entity_id": eid},
            )

        deduped.append(eid)

    return deduped


async def _async_resolve_target_entries(
    hass: HomeAssistant, call: ServiceCall
) -> list[ConfigEntry]:
    """Resolve target entity_ids to LOADED relative_light_group config entries."""
    entity_ids = await service.async_extract_entity_ids(call, expand_group=False)
    if not entity_ids:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_target",
        )

    entries: dict[str, ConfigEntry] = {}
    invalid: list[str] = []
    for eid in entity_ids:
        config_entry = _entity_relative_light_group_entry(hass, eid)
        if config_entry is None:
            invalid.append(eid)
            continue
        if config_entry.state is not ConfigEntryState.LOADED:
            invalid.append(eid)
            continue
        entries[config_entry.entry_id] = config_entry

    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_valid_target",
            translation_placeholders={
                "entities": ", ".join(sorted(invalid)) or "(none)"
            },
        )

    if invalid:
        _LOGGER.debug(
            "Ignoring entity_ids not bound to a loaded relative_light_group: %s",
            invalid,
        )

    return list(entries.values())


@callback
def _own_entity_ids_for_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> set[str]:
    registry = er.async_get(hass)
    return {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def _async_apply_member_change(
    hass: HomeAssistant,
    entry: ConfigEntry,
    new_members: list[str],
) -> None:
    """Persist new members, adjust hide_members deltas, and reload the entry."""
    old_members = list(entry.options.get(CONF_ENTITIES, []))
    if new_members == old_members:
        return

    new_options = {**entry.options, CONF_ENTITIES: new_members}
    hass.config_entries.async_update_entry(entry, options=new_options)

    hide = bool(new_options.get(CONF_HIDE_MEMBERS, False))
    added = [eid for eid in new_members if eid not in old_members]
    removed = [eid for eid in old_members if eid not in new_members]

    if hide and added:
        set_member_visibility(hass, added, er.RegistryEntryHider.INTEGRATION)
    if removed:
        restore_member_visibility_if_hidden_by_integration(hass, removed)

    await hass.config_entries.async_reload(entry.entry_id)


async def _async_apply_options_change(
    hass: HomeAssistant, entry: ConfigEntry, changes: dict[str, Any]
) -> None:
    """Merge `changes` into entry.options, adjust hide_members, reload the entry."""
    new_options = {**entry.options, **changes}
    if new_options == dict(entry.options):
        return

    old_hide = bool(entry.options.get(CONF_HIDE_MEMBERS, False))
    new_hide = bool(new_options.get(CONF_HIDE_MEMBERS, False))

    hass.config_entries.async_update_entry(entry, options=new_options)

    if old_hide != new_hide:
        members: list[str] = list(new_options.get(CONF_ENTITIES, []))
        if new_hide:
            set_member_visibility(hass, members, er.RegistryEntryHider.INTEGRATION)
        else:
            restore_member_visibility_if_hidden_by_integration(hass, members)

    await hass.config_entries.async_reload(entry.entry_id)


def _extract_option_changes(call: ServiceCall) -> dict[str, Any]:
    """Pull only known option keys from `call.data`, dropping target/extra fields."""
    return {key: call.data[key] for key in OPTION_KEYS if key in call.data}


# --- Service handlers ---

async def _async_handle_set_options(call: ServiceCall) -> None:
    hass = call.hass
    changes = _extract_option_changes(call)
    if not changes:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_options",
        )

    entries = await _async_resolve_target_entries(hass, call)

    for entry in entries:
        async with _get_lock(hass, entry.entry_id):
            await _async_apply_options_change(hass, entry, changes)


async def _async_handle_add_lights(call: ServiceCall) -> None:
    hass = call.hass
    raw_entities: list[Any] = list(call.data.get(CONF_ENTITIES, []))

    entries = await _async_resolve_target_entries(hass, call)
    for entry in entries:
        async with _get_lock(hass, entry.entry_id):
            own_eids = _own_entity_ids_for_entry(hass, entry)
            new_members_validated = validate_member_entities(
                hass, raw_entities, own_eids
            )
            current = list(entry.options.get(CONF_ENTITIES, []))
            current_set = set(current)
            merged = list(current)
            for eid in new_members_validated:
                if eid not in current_set:
                    merged.append(eid)
                    current_set.add(eid)
            await _async_apply_member_change(hass, entry, merged)


async def _async_handle_remove_lights(call: ServiceCall) -> None:
    hass = call.hass
    raw_entities: list[Any] = list(call.data.get(CONF_ENTITIES, []))

    to_remove: list[str] = [eid for eid in raw_entities if isinstance(eid, str)]
    if not to_remove:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_entities",
            translation_placeholders={"error": "empty list"},
        )
    to_remove_set = set(to_remove)

    entries = await _async_resolve_target_entries(hass, call)
    for entry in entries:
        async with _get_lock(hass, entry.entry_id):
            current = list(entry.options.get(CONF_ENTITIES, []))
            new_members = [eid for eid in current if eid not in to_remove_set]
            ignored = [eid for eid in to_remove if eid not in current]
            if ignored:
                _LOGGER.debug(
                    "remove_lights: ignored %s (not in group %s)",
                    ignored,
                    entry.title,
                )
            if new_members == current:
                continue
            if not new_members:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="empty_group",
                    translation_placeholders={"group": entry.title},
                )
            await _async_apply_member_change(hass, entry, new_members)


async def _async_handle_set_lights(call: ServiceCall) -> None:
    hass = call.hass
    raw_entities: list[Any] = list(call.data.get(CONF_ENTITIES, []))

    entries = await _async_resolve_target_entries(hass, call)
    for entry in entries:
        async with _get_lock(hass, entry.entry_id):
            own_eids = _own_entity_ids_for_entry(hass, entry)
            new_members = validate_member_entities(hass, raw_entities, own_eids)
            if not new_members:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="empty_group",
                    translation_placeholders={"group": entry.title},
                )
            await _async_apply_member_change(hass, entry, new_members)


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register Relative Light Group services (idempotent)."""
    if hass.services.has_service(DOMAIN, SERVICE_SET_OPTIONS):
        return

    hass.services.async_register(
        DOMAIN, SERVICE_SET_OPTIONS, _async_handle_set_options, schema=SET_OPTIONS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_LIGHTS, _async_handle_add_lights, schema=MEMBER_LIST_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_LIGHTS,
        _async_handle_remove_lights,
        schema=MEMBER_LIST_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_LIGHTS, _async_handle_set_lights, schema=MEMBER_LIST_SCHEMA
    )
