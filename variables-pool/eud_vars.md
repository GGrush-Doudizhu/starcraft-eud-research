# EUD Variable Pool

> eudplib source: [armoha/eudplib](https://github.com/armoha/eudplib)  
> Acknowledgments: Special thanks to Armoha for his long-standing dedication to maintaining eudplib and for his help. Thanks also to trgk, the original author of eudplib.

`eud_vars.py` provides an EUD variable pool that operates while a map is being compiled. It allows logic with completely non-overlapping runtime lifetimes to reuse the same `EUDVariable` object and, consequently, the same 72-byte variable payload slot.

The module does not analyze trigger control flow or determine whether a variable has truly stopped being used. Acquisition and release are compile-time Python operations; callers are responsible for proving that every lifetime is correct.

## Contents

1. [Importing](#importing)
2. [Two Acquisition Methods](#two-acquisition-methods)
3. [`get()`: Acquiring a Variable with an Unknown Value](#get-acquiring-a-variable-with-an-unknown-value)
4. [`init()`: Acquiring a Variable with a Known Initial Value](#init-acquiring-a-variable-with-a-known-initial-value)
5. [Initial Values Must Be `int`](#initial-values-must-be-int)
6. [`free()`: Releasing a Variable](#free-releasing-a-variable)
7. [Releasing Multiple Variables](#releasing-multiple-variables)
8. [Known-Value Reuse Lifecycle](#known-value-reuse-lifecycle)
9. [Discarding a Reset Action Is a Serious Error](#discarding-a-reset-action-is-a-serious-error)
10. [Conditional Reset May Be Unsafe](#conditional-reset-may-be-unsafe)
11. [Variables May Remain Permanently Allocated](#variables-may-remain-permanently-allocated)
12. [Repeated Releases](#repeated-releases)
13. [Runtime Lifetime Requirements](#runtime-lifetime-requirements)
14. [The lvalue Marker](#the-lvalue-marker)
15. [VTable State](#vtable-state)
16. [Creating an Independent Variable Pool](#creating-an-independent-variable-pool)
17. [Viewing Statistics](#viewing-statistics)
18. [Allocation Strategy Summary](#allocation-strategy-summary)

## Importing

Most code should use the shared `eud_vars` instance:

```python
from eud_vars import eud_vars
```

The public API contains four names:

```python
from eud_vars import PoolVar, VarPool, VarStats, eud_vars
```

- `PoolVar`: an `EUDVariable` subclass created by the pool.
- `VarPool`: the variable pool.
- `VarStats`: an immutable statistics snapshot.
- `eud_vars`: the shared pool for the project.

`PoolVar` is a real `EUDVariable` subclass, not a proxy wrapper:

```python
from eudplib import EUDVariable

value = eud_vars.get()
assert isinstance(value, EUDVariable)
```

It can therefore be passed directly to eudplib APIs that accept an `EUDVariable`.

## Two Acquisition Methods

The pool provides two acquisition methods with different contracts:

| API | Is the returned value known? | Must the caller initialize it? |
|---|---|---|
| `get()` | Unknown | Yes |
| `init(value)` | Known to be `value` | No |

The method name makes it possible to determine immediately whether manual initialization is required.

## `get()`: Acquiring a Variable with an Unknown Value

Acquire one variable:

```python
a = eud_vars.get()
```

`get()` follows this strict priority order:

1. An unknown-value variable previously released with `free()` and no argument.
2. Any known-value variable previously released with `free(n)`.
3. A new `EUDVariable(0)`, but only when neither of the preceding categories is available.

An unknown-value variable carries no value-matching information that could benefit a future `init(n)`, so unknown slots are consumed first. A known-value variable may still be an exact match for a future `init(n)` and is therefore taken by `get()` only when the unknown-value pool is empty. Even when `get()` obtains a known-value slot, the caller must not rely on its old value and must initialize it manually before the first read.

```python
a = eud_vars.get()

RawTrigger(
    actions=[
        a.SetNumber(0),
        # Other business Actions.
    ],
)
```

Initialization should be merged into an existing trigger whenever possible. Registering an additional `RawTrigger` solely to initialize a reusable variable is not worthwhile.

### Acquiring Multiple Unknown-Value Variables at Once

Pass a positive integer count to `get()`:

```python
a, b = eud_vars.get(2)
```

The result is a tuple containing the requested number of distinct variables:

```python
variables = eud_vars.get(3)
assert isinstance(variables, tuple)
assert len(variables) == 3
```

The call form determines the return type:

```python
single = eud_vars.get()       # PoolVar
one_tuple = eud_vars.get(1)   # tuple[PoolVar]
many = eud_vars.get(3)        # tuple[PoolVar, PoolVar, PoolVar]
```

The count must be a positive integer. Although `bool` is a subclass of Python's `int`, it is not accepted:

```python
eud_vars.get(0)       # ValueError
eud_vars.get(-1)      # ValueError
eud_vars.get(True)    # TypeError
```

## `init()`: Acquiring a Variable with a Known Initial Value

When no existing trigger is available to carry an initialization Action, acquire a variable with a known initial value:

```python
counter = eud_vars.init(3)
```

The pool proceeds as follows:

1. Look for an available variable previously released with `free(3)`.
2. Reuse that variable if found.
3. Otherwise, create `EUDVariable(3)`.

The initial value of a newly created variable is written directly into its payload, so no additional initialization trigger is required.

### Acquiring Multiple Known Initial Values at Once

Each argument corresponds to one returned variable:

```python
zero, three, ten = eud_vars.init(0, 3, 10)
```

This is equivalent to acquiring them separately:

```python
zero = eud_vars.init(0)
three = eud_vars.init(3)
ten = eud_vars.init(10)
```

One argument returns a single `PoolVar`; multiple arguments return a tuple:

```python
single = eud_vars.init(0)
many = eud_vars.init(0, 3)
```

## Initial Values Must Be `int`

`init()` and `free()` with an argument accept only values whose exact type is `int`:

```python
value = eud_vars.init(3)
```

The following arguments are rejected:

```python
from eudplib import Forward

eud_vars.init(True)       # TypeError
eud_vars.init(Forward())  # TypeError
```

The result of `EPD()` for a constant integer address is itself an `int`, so it may be used:

```python
a = eud_vars.init(EPD(0x6509B0))
```

Every integer is normalized to an unsigned DWORD:

```python
negative = eud_vars.init(-1)
unsigned = 0xFFFFFFFF
```

`-1` and `0xFFFFFFFF` represent the same DWORD value and can therefore use the same class of known-value free variables.

## `free()`: Releasing a Variable

`free()` is a method of `PoolVar` and can be called directly:

```python
a = eud_vars.get()

# use `a` to do something

a.free()
```

After release, the original Python name still refers to the same object, but the pool may already have issued that object to another caller. Continuing to use the variable through its old name after release is an error.

### Releasing with an Unknown Value

With no argument:

```python
result = value.free()
assert result is None
```

The variable enters the unknown-value free pool. A later `get()` may reuse it, but `init(n)` will not, because its value is unknown. A caller that reuses it through `get()` must initialize it manually.

### Resetting and Releasing

With an integer argument:

```python
reset_action = value.free(3)
```

This call:

1. Returns `value.SetNumber(3)`.
2. Marks the variable as available at compile time.
3. Records that the variable must be reset to `3` before its next lifetime.

The recommended approach is to place the returned Action directly in an existing business trigger:

```python
value = eud_vars.get()

# use `value` to do something

RawTrigger(
    actions=[
        SetSwitch("Work Complete", SetTo),
        value.free(3),
    ],
)
```

A later acquisition with the same initial value can reuse it:

```python
next_value = eud_vars.init(3)
```

If no intervening acquisition takes the available variable first, `next_value` may be the same object and the same EUD memory slot as `value`.

## Releasing Multiple Variables

Unknown-value releases may be called one at a time or grouped:

```python
a, b = eud_vars.get(2)

# Use a and b.

# Finish using a.
DoActions(a.free())

# Finish using b.
DoActions(b.free())

# Or release them together.
DoActions(
    a.free(),
    b.free(),
)
```

```python
first, second, third = eud_vars.get(3)

reset_actions = [
    first.free(0),
    second.free(3),
    third.free(10),
]

RawTrigger(
    actions=[
        # Other business Actions.
        *reset_actions,
    ],
)
```

## Known-Value Reuse Lifecycle

The following example demonstrates the complete process:

```python
old_value = eud_vars.get()

RawTrigger(
    actions=[
        old_value.SetNumber(100),
        # Other Actions that use old_value.
    ],
)

RawTrigger(
    actions=[
        # End-of-lifetime business logic.
        old_value.free(7),
    ],
)

new_value = eud_vars.init(7)

# new_value requires no additional initialization.
```

It is correct only if runtime execution follows this order:

```text
Last use in the old lifetime
        ↓
Execute old_value.SetNumber(7)
        ↓
First read in the new lifetime
```

Source order in Python is not a substitute for proving the runtime order.

## Discarding a Reset Action Is a Serious Error

The following code records at compile time that the variable has been “reset to 3,” but no trigger actually executes the returned Action:

```python
value.free(3)  # Error: the returned Action is discarded.

other = eud_vars.init(3)
```

The Action returned by `free()` with an argument must be used.

## Conditional Reset May Be Unsafe

The following reset executes only when the condition is true:

```python
reset_action = value.free(0)

RawTrigger(
    conditions=some_condition,
    actions=reset_action,
)
```

A later `eud_vars.init(0)` is safe only if every runtime path that can reach the new lifetime is guaranteed to satisfy that condition. Otherwise, some paths may bypass the reset.

Also verify that:

- the reset trigger cannot execute after the new lifetime begins;
- no branch still reads the old variable;
- no loop still holds the variable's address;
- no periodic trigger continues to run after the release; and
- the variable's address has not been stored in a long-lived object.

## Variables May Remain Permanently Allocated

The pool does not require every acquisition to be released:

```python
permanent = eud_vars.init(100)
```

If `permanent` may be used throughout the entire game, it should remain active. Not calling `free()` is valid.

The module has no `check()` method because “not yet released” does not imply a variable leak. The pool still tracks active state so that a variable that has not been released cannot be allocated again, and so that repeated releases are handled correctly.

## Repeated Releases

Repeated releases are allowed:

```python
value = eud_vars.get()
value.free()
value.free()
```

The variable is registered only once in the free pool and cannot be issued to multiple callers simultaneously because of repeated releases.

Repeated releases with the same value each return a reset Action, while the variable retains that known reset value:

```python
first_branch_action = value.free(3)
second_branch_action = value.free(3)

next_value = eud_vars.init(3)
```

This allows the two Actions to be placed in different runtime branches. The caller must still guarantee that every path capable of reaching the new lifetime executes the corresponding reset.

If repeated releases provide different values, the pool cannot infer from Python source order which branch will execute last at runtime. It therefore conservatively downgrades the variable to an unknown value:

```python
value.free(3)
value.free(7)

next_value = eud_vars.init(3)  # value is not treated as known 3 or 7.
```

Mixing releases with and without arguments also downgrades the variable to unknown:

```python
value.free(3)
value.free()
```

Once a variable is unknown, later repeated calls to `free(n)` cannot promote it back to a known value.

### Old Aliases After Reacquisition

After a variable is acquired again, its old Python name can still access the same object:

```python
old_name = eud_vars.get()
old_name.free()

new_name = eud_vars.get()
assert old_name is new_name
```

At this point, calling `old_name.free()` is not a repeated release: it releases the new lifetime currently in use through `new_name`. The pool cannot distinguish two Python aliases of the same object, so callers must stop using an old name after releasing it.

Similarly, while generating release code for multiple branches, do not reacquire a potentially matching variable between two release calls:

```python
old_name.free()
new_name = eud_vars.get()  # May acquire the same object as old_name.
old_name.free()            # Releases the new lifetime of new_name.
```

## Runtime Lifetime Requirements

Before releasing a variable, verify that:

1. The old variable does not leave the current region as a return value.
2. The old variable is not stored in a long-lived object, array, or structure.
3. Every runtime branch has permanently stopped accessing it.
4. Every runtime loop containing it has exited.
5. No periodic trigger will continue to read or modify it.
6. The new lifetime cannot begin before the release or reset logic executes.
7. The Action returned by `free(value)` executes on every required path.

The pool trusts the lifetime proof supplied by the caller and does not validate these conditions automatically.

## The lvalue Marker

The eudplib lvalue/rvalue marker is compile-time Python state, not a variable value at game runtime.

A temporary `EUDVariable` produced by an expression may be marked as an rvalue. When eudplib knows that an object is only a one-use expression result, it may overwrite and reuse that object to reduce the number of additional temporary variables. An object returned by the pool represents a new, stable business lifetime and must not continue to be treated as a disposable expression temporary.

The pool therefore calls the following every time `get()` or `init()` activates a variable:

```python
variable.makeL()
```

This only clears the object's compile-time rvalue marker:

- It generates no trigger or Action.
- It does not modify the value at game runtime.
- It does not clear the VTable.
- It does not increase payload size.

Restoring lvalue state during `free()` is unnecessary because the caller must not continue using that lifetime after release. Restoring it at the next acquisition is sufficient.

## VTable State

The `makeL()` call made during reacquisition does not reset the complete VTable state at runtime, including:

- destination
- modifier
- mask
- next pointer

Variables read and written directly with the following operations are the best candidates for reuse:

- `SetNumber`
- `AddNumber`
- `SubtractNumber`
- numeric comparisons and Conditions

If a variable participates in any of the following operations, additional proof is required that residual VTable state cannot affect its next lifetime:

- `SetDest`
- `QueueAssignTo`
- `QueueAddTo`
- `QueueSubtractTo`
- `GetVTable`
- complex `VProc` chains

`free(value)` resets only the variable's value, not any of the other state listed above.

## Creating an Independent Variable Pool

An independent subsystem may own its own pool:

```python
from src.game.eud_vars import VarPool

first_pool = VarPool()
second_pool = VarPool()

first = first_pool.get()
second = second_pool.init(3)
```

Variables are not reused across different pools. Each `PoolVar` remembers its owning pool, so:

```python
first.free()
second.free(3)
```

automatically returns each variable to the correct pool.

## Viewing Statistics

```python
value = eud_vars.get()
snapshot = eud_vars.stats

print(snapshot.total)
print(snapshot.active)
print(snapshot.available)
print(snapshot.unknown)
print(snapshot.initialized)
print(snapshot.acquisitions)
print(snapshot.reuses)
print(snapshot.peak_active)
```

Field meanings:

| Field | Meaning |
|---|---|
| `total` | Total number of variables ever created by the pool |
| `active` | Number of acquired variables not yet released |
| `available` | Total number of available variables |
| `unknown` | Number of available variables with unknown values |
| `initialized` | Number of available variables with known reset values |
| `acquisitions` | Historical acquisition count |
| `reuses` | Historical reuse count |
| `peak_active` | Highest number of simultaneously active variables |

`stats` is only a diagnostic and observation tool; it does not require `active` to reach zero.

## Allocation Strategy Summary

The strict selection order for `get()` is:

1. An unknown-value available variable produced by `free()` with no argument.
2. Any known-value available variable produced by `free(n)`.
3. A new `EUDVariable(0)` when neither category is available.

Consuming unknown-value variables first preserves exact-match opportunities for future `init(value)` calls. As long as any available variable exists in either category, `get()` does not create a new variable.

The selection order for `init(value)` is:

1. An exact-match variable released by `free(value)`.
2. A new `EUDVariable(value)`.

`init(value)` does not use an unknown-value available variable because, without an additional runtime Action, the pool cannot guarantee that an unknown variable has the requested initial value.
