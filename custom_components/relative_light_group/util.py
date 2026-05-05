"""Utility functions to combine state attributes from multiple entities."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from itertools import groupby
from math import atan2, cos, degrees, radians, sin
from typing import Any

from homeassistant.core import State

_LOGGER = logging.getLogger(__name__)


def find_state_attributes(states: list[State], key: str) -> Iterator[Any]:
    """Find attributes with matching key from states."""
    for state in states:
        if (value := state.attributes.get(key)) is not None:
            yield value


def mean_int(*args: Any) -> int:
    """Return the mean of the supplied values."""
    return int(sum(args) / len(args))


def mean_tuple(*args: Any) -> tuple[float | Any, ...]:
    """Return the mean values along the columns of the supplied values."""
    return tuple(sum(x) / len(x) for x in zip(*args, strict=False))


def mean_circle(*args: Any) -> tuple[float | Any, ...]:
    """Return the circular mean of hue and arithmetic mean of saturation from HS color tuples."""
    if not args:
        return ()

    hues, saturations = zip(*args, strict=False)

    sum_x = sum(cos(radians(h)) for h in hues)
    sum_y = sum(sin(radians(h)) for h in hues)

    mean_angle = degrees(atan2(sum_y, sum_x)) % 360

    saturation = sum(saturations) / len(saturations)

    return (mean_angle, saturation)


def reduce_attribute(
    states: list[State],
    key: str,
    default: Any | None = None,
    reduce: Callable[..., Any] = mean_int,
) -> Any:
    """Find the first attribute matching key from states.

    If none are found, return default.
    """
    attrs = list(find_state_attributes(states, key))

    if not attrs:
        return default

    if len(attrs) == 1:
        return attrs[0]

    return reduce(*attrs)


def coerce_in(value: float | int, minimum: float | int, maximum: float | int) -> int:
    """Coerce value into the range [minimum, maximum]."""
    return int(max(minimum, min(value, maximum)))


# #region agent log
_AGENT_DEBUG_LOG = "/Users/felipe/Repos/relative-light-group/.cursor/debug-71ea80.log"


def agent_debug_log(
    location: str, message: str, data: dict[str, Any], hypothesis_id: str
) -> None:
    """Append one NDJSON line for debug-mode analysis."""
    line = (
        json.dumps(
            {
                "sessionId": "71ea80",
                "timestamp": int(time.time() * 1000),
                "location": location,
                "message": message,
                "data": data,
                "hypothesisId": hypothesis_id,
            },
            default=str,
        )
        + "\n"
    )
    try:
        with open(_AGENT_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        _LOGGER.info("RLG_DEBUG_NDJSON %s", line.strip())


# #endregion
