"""Compile-time allocation and explicit reuse of EUD variables.

Every used `EUDVariable` reserves a fixed payload slot that
cannot be released at game runtime. This module lets callers reuse a slot when
they can prove that two runtime lifetimes never overlap.

The pool offers two acquisition contracts:

``get()``
    Returns a variable with an unspecified runtime value. The caller must
    initialize it before its first runtime use.

``init(value)``
    Returns a variable with a specified integer. The
    pool reuses a slot previously released with the same promised reset value,
    or creates ``EUDVariable(value)`` when no matching slot is available.

Acquisition and release are compile-time Python operations. They do not inspect
or validate generated trigger control flow. In particular, ``var.free(value)``
marks the slot as reusable immediately and returns a reset `Action`; the caller
must place that action on every runtime path leading to the next lifetime.

See ``eud_vars.md`` for the complete lifecycle contract and usage examples.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import overload

from eudplib import Action, EUDVariable


__all__ = [
    "PoolVar",
    "VarPool",
    "VarStats",
    "eud_vars",
]


_MISSING = object()
_UNKNOWN = object()


def _dword(value: object) -> int:
    """Validate and normalize a pool value to an unsigned DWORD."""

    if type(value) is not int:
        raise TypeError("an initialized pool value must be an int")
    return value & 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class VarStats:
    """Immutable allocation statistics for a variable pool."""

    total: int  # Total number of variables created by the pool.
    active: int  # Number of variables currently acquired.
    available: int  # Total number of released variables available for reuse.
    unknown: int  # Number of available variables with unspecified values.
    initialized: int  # Number of available variables with known reset values.
    acquisitions: int  # Total number of successful acquisition operations.
    reuses: int  # Number of acquisitions satisfied by released variables.
    peak_active: int  # Highest number of simultaneously active variables.


class PoolVar(EUDVariable):
    """An ``EUDVariable`` owned by a :class:`VarPool`.

    Instances are real ``EUDVariable`` subclasses rather than wrappers, so
    eudplib APIs recognize and operate on them normally. Callers should obtain
    instances from :meth:`VarPool.get` or :meth:`VarPool.init` instead of
    constructing them directly.
    """

    __slots__ = ("_pool",)

    def __init__(self, pool: VarPool, value: int = 0) -> None:
        super().__init__(value)
        self._pool = pool

    @overload
    def free(self) -> None:
        """Release this variable without an specified value."""

    @overload
    def free(self, value: int) -> Action:
        """Release this variable and return an action that resets its value."""

    def free(self, value: object = _MISSING) -> Action | None:
        """Release this variable for a later non-overlapping lifetime.

        With no argument, the variable enters the unknown-value free pool and
        this method returns ``None``.

        With an integer argument, this method returns ``SetNumber(value)`` and
        records the normalized DWORD as the value promised for the next
        lifetime. The caller must insert the returned action into runtime code
        that always executes after the old lifetime and before the new one.

        Args:
            value: Optional integer value to establish before the next use.

        Returns:
            ``None`` when no value is supplied, otherwise a reset action.

        Raises:
            TypeError: If ``value`` is supplied but is not exactly an ``int``.
        """

        if value is _MISSING:
            self._pool._release(self)
            return None

        normalized = _dword(value)
        action = self.SetNumber(normalized)
        self._pool._release(self, normalized)
        return action


class VarPool:
    """Allocate and explicitly reuse managed EUD variables during compilation.

    The pool tracks compile-time ownership and availability only. It does not
    require every acquired variable to be released, and permanent acquisitions
    are valid. A variable becomes reusable only after its ``free()`` method is
    called.
    """

    __slots__ = (
        "_acquisitions",  # Total number of successful acquisitions.
        "_active",  # Variables in active lifetimes.
        "_available_by_value",  # Released variables grouped by reset value.
        "_available_unknown",  # Released variables with unspecified values.
        "_owned",  # All variables created by this pool.
        "_peak_active",  # Highest recorded number of active variables.
        "_released_values",  # Recorded state of every released variable.
        "_reuses",  # Acquisitions served from released variables.
        "_variables",  # Variables in their original creation order.
    )

    def __init__(self) -> None:
        self._acquisitions = 0
        self._active: set[PoolVar] = set()
        self._available_by_value: dict[int, list[PoolVar]] = {}
        self._available_unknown: list[PoolVar] = []
        self._owned: set[PoolVar] = set()
        self._peak_active = 0
        self._released_values: dict[PoolVar, object] = {}
        self._reuses = 0
        self._variables: list[PoolVar] = []

    @property
    def stats(self) -> VarStats:
        """Return a snapshot of allocation and reuse statistics."""

        unknown = len(self._available_unknown)
        initialized = sum(
            len(variables) for variables in self._available_by_value.values()
        )
        return VarStats(
            total=len(self._variables),
            active=len(self._active),
            available=unknown + initialized,
            unknown=unknown,
            initialized=initialized,
            acquisitions=self._acquisitions,
            reuses=self._reuses,
            peak_active=self._peak_active,
        )

    @overload
    def get(self) -> PoolVar:
        """Acquire one variable whose runtime value is unspecified."""

    @overload
    def get(self, count: int) -> tuple[PoolVar, ...]:
        """Acquire ``count`` variables whose runtime values are unspecified."""

    def get(self, count: object = _MISSING) -> PoolVar | tuple[PoolVar, ...]:
        """Acquire one or more variables that require manual initialization.

        ``get()`` returns one variable. ``get(count)`` returns a tuple of
        exactly ``count`` distinct active variables, including when ``count``
        is one.

        Allocation follows a strict priority order: an unknown-value released
        slot, a released slot carrying any known value, and finally a newly
        created variable. Consuming unknown-value slots first preserves exact
        value matches for future `init` calls.

        Args:
            count: Optional positive integer number of variables to acquire.

        Raises:
            TypeError: If ``count`` is not exactly an ``int``.
            ValueError: If ``count`` is not positive.
        """

        if count is _MISSING:
            return self._get_one()
        if type(count) is not int:
            raise TypeError("variable count must be an int")
        if count <= 0:
            raise ValueError("variable count must be positive")
        return tuple(self._get_one() for _ in range(count))

    @overload
    def init(self, value: int) -> PoolVar:
        """Acquire one variable with ``value``."""

    @overload
    def init(self, value: int, *values: int) -> tuple[PoolVar, ...]:
        """Acquire variables with corresponding values."""

    def init(self, value: int, *values: int) -> PoolVar | tuple[PoolVar, ...]:
        """Acquire one or more variables with known values.

        Every value must be exactly an ``int`` and is normalized to an unsigned
        DWORD. For each value, a matching released slot is reused when
        available; otherwise a new ``EUDVariable(value)`` slot is created.

        A single argument returns one variable. Multiple arguments return a
        tuple with one variable per argument.
        """

        normalized = tuple(_dword(item) for item in (value, *values))
        variables = tuple(self._init_one(item) for item in normalized)
        if len(variables) == 1:
            return variables[0]
        return variables

    def owns(self, variable: object) -> bool:
        """Return whether this pool created ``variable``."""

        return isinstance(variable, PoolVar) and variable in self._owned

    def in_use(self, variable: object) -> bool:
        """Return whether this pool currently considers ``variable`` active."""

        return isinstance(variable, PoolVar) and variable in self._active

    def _create(self, value: int) -> PoolVar:
        variable = PoolVar(self, value)
        self._variables.append(variable)
        self._owned.add(variable)
        return variable

    def _activate(self, variable: PoolVar, *, reused: bool) -> PoolVar:
        # A previous lifetime may have marked the object as an expendable
        # expression rvalue. Every acquired pool variable must instead begin
        # its new lifetime as a stable lvalue.
        variable.makeL()
        self._released_values.pop(variable, None)
        self._active.add(variable)
        self._acquisitions += 1
        if reused:
            self._reuses += 1
        self._peak_active = max(self._peak_active, len(self._active))
        return variable

    def _get_one(self) -> PoolVar:
        # Unknown-value slots have no value-matching benefit, so consume them
        # before sacrificing a slot reserved for a future init(value) match.
        if self._available_unknown:
            return self._activate(self._available_unknown.pop(), reused=True)

        # get() permits manual initialization, so any remaining known-value
        # slot is still preferable to allocating another 72-byte variable.
        if self._available_by_value:
            value = next(reversed(self._available_by_value))
            variables = self._available_by_value[value]
            variable = variables.pop()
            if not variables:
                del self._available_by_value[value]
            return self._activate(variable, reused=True)

        # Allocate only when no released slot of either category remains.
        return self._activate(self._create(0), reused=False)

    def _init_one(self, value: int) -> PoolVar:
        variables = self._available_by_value.get(value)
        if variables:
            variable = variables.pop()
            if not variables:
                del self._available_by_value[value]
            return self._activate(variable, reused=True)

        return self._activate(self._create(value), reused=False)

    def _release(self, variable: PoolVar, value: object = _MISSING) -> None:
        if variable not in self._owned or variable._pool is not self:
            raise ValueError("variable does not belong to this pool")

        released_value = _UNKNOWN if value is _MISSING else value

        if variable in self._active:
            self._active.remove(variable)
            self._released_values[variable] = released_value
            if released_value is _UNKNOWN:
                self._available_unknown.append(variable)
            else:
                self._available_by_value.setdefault(released_value, []).append(variable)
            return

        previous_value = self._released_values[variable]
        if previous_value is _UNKNOWN or previous_value == released_value:
            return

        if released_value is _UNKNOWN:
            self._remove_initialized(variable, previous_value)
            self._available_unknown.append(variable)
            self._released_values[variable] = _UNKNOWN
            return

        # Different reset values on repeated releases make the runtime result
        # path-dependent, so the pool must conservatively forget the value.
        self._remove_initialized(variable, previous_value)
        self._available_unknown.append(variable)
        self._released_values[variable] = _UNKNOWN

    def _remove_initialized(self, variable: PoolVar, value: object) -> None:
        variables = self._available_by_value[value]
        for index, candidate in enumerate(variables):
            if candidate is variable:
                variables.pop(index)
                break
        if not variables:
            del self._available_by_value[value]


eud_vars = VarPool()
"""Shared variable pool for game modules."""
