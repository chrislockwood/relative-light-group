"""Brightness calculation helpers for Relative Light Group."""

from __future__ import annotations

from statistics import median

from homeassistant.core import State

from .const import (
    BRIGHTNESS_STRATEGY_AVERAGE,
    BRIGHTNESS_STRATEGY_MAX,
    BRIGHTNESS_STRATEGY_MEDIAN,
    BRIGHTNESS_STRATEGY_MIN,
    DEFAULT_BRIGHTNESS_STRATEGY,
)
from .util import coerce_in

BRIGHTNESS_MAX = 255
BRIGHTNESS_MIN = 1


def brightness_values(states: list[State], attribute: str) -> list[int]:
    """Return normalized brightness values found in the given states."""
    values: list[int] = []
    for state in states:
        value = state.attributes.get(attribute)
        if value is None:
            continue
        values.append(coerce_in(int(value), BRIGHTNESS_MIN, BRIGHTNESS_MAX))
    return values


def representative_brightness(
    states: list[State],
    attribute: str,
    strategy: str = DEFAULT_BRIGHTNESS_STRATEGY,
) -> int | None:
    """Calculate the brightness value reported by the group."""
    values = brightness_values(states, attribute)
    if not values:
        return None

    if strategy == BRIGHTNESS_STRATEGY_MEDIAN:
        return int(median(values))
    if strategy == BRIGHTNESS_STRATEGY_MAX:
        return max(values)
    if strategy == BRIGHTNESS_STRATEGY_MIN:
        return min(values)

    return int(sum(values) / len(values))


def relative_brightness_map(
    states: list[State],
    current_group_brightness: int,
    target_group_brightness: int,
    attribute: str,
) -> dict[str, int]:
    """Calculate standard relative brightness targets for member lights."""
    if current_group_brightness <= 0:
        return {}

    brightness_change = target_group_brightness - current_group_brightness
    if brightness_change == 0:
        return {}

    if brightness_change > 0:
        denominator = BRIGHTNESS_MAX - current_group_brightness
    else:
        denominator = current_group_brightness
    if denominator <= 0:
        return {}

    brightness_change_factor = brightness_change / denominator
    brightness_map: dict[str, int] = {}

    for state in states:
        light_brightness = state.attributes.get(attribute)
        if light_brightness is None:
            brightness_map[state.entity_id] = target_group_brightness
            continue

        if brightness_change > 0:
            offset = brightness_change_factor * (BRIGHTNESS_MAX - light_brightness)
        else:
            offset = brightness_change_factor * light_brightness
        brightness_map[state.entity_id] = coerce_in(
            round(light_brightness + offset), BRIGHTNESS_MIN, BRIGHTNESS_MAX
        )

    return brightness_map


def base_relative_brightness_map(
    states: list[State],
    base_brightness: dict[str, int],
    target_group_brightness: int,
) -> dict[str, int]:
    """Calculate base-relative brightness targets for member lights."""
    base_values = [
        base_brightness[state.entity_id]
        for state in states
        if state.entity_id in base_brightness
    ]
    if not base_values:
        return {}

    base_group = sum(base_values) / len(base_values)
    if base_group <= 0:
        return {}

    direction = target_group_brightness - base_group
    brightness_map: dict[str, int] = {}

    if direction >= 0:
        max_headroom = BRIGHTNESS_MAX - base_group
        factor = direction / max_headroom if max_headroom > 0 else 0
        for state in states:
            base = base_brightness.get(state.entity_id)
            if base is None:
                continue
            new_value = base + factor * (BRIGHTNESS_MAX - base)
            brightness_map[state.entity_id] = coerce_in(
                round(new_value), BRIGHTNESS_MIN, BRIGHTNESS_MAX
            )
        return brightness_map

    factor = direction / base_group
    for state in states:
        base = base_brightness.get(state.entity_id)
        if base is None:
            continue
        new_value = base + factor * base
        brightness_map[state.entity_id] = coerce_in(
            round(new_value), BRIGHTNESS_MIN, BRIGHTNESS_MAX
        )

    return brightness_map


def group_entity_ids_by_brightness(
    brightness_map: dict[str, int],
) -> dict[int, list[str]]:
    """Group entity_ids by their target brightness value."""
    groups: dict[int, list[str]] = {}
    for entity_id, brightness in brightness_map.items():
        groups.setdefault(brightness, []).append(entity_id)
    return groups
