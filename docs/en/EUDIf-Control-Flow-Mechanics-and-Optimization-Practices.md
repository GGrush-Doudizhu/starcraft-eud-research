# EUDIf Control-Flow Mechanics and Optimization Practices

> Author: GGrush  
> Written: 2026-07-25

> eudplib source: [armoha/eudplib](https://github.com/armoha/eudplib)  
> Acknowledgments: Special thanks to Armoha for his long-standing dedication to maintaining eudplib and for his help. Thanks also to trgk, the original author of eudplib.

## Abstract

`EUDIf`, `EUDIfNot`, `EUDElseIf`, and `EUDElse` give eudplib a runtime-branch syntax similar to that of a high-level language. These structures are ultimately expanded into StarCraft triggers and `nextptr` links. Two implementations of the same business logic can therefore have noticeably different runtime costs when they use different condition expressions or branch orientations.

This document starts with the internal structure of `EUDBranch`, explains how the `EUDIf` family works, defines a compact notation for comparing costs, and presents optimization methods for single branches, `if-else` structures, `else-if` chains, and early exits from loops. It also discusses the benefits, constraints, and safety requirements of advanced techniques such as using `RawTrigger` directly, merging a reset Action into a business-logic trigger, and moving reset work into a shared trigger.

The implementation analysis in this document is based on eudplib 0.80.6.

## Contents

1. [Key Takeaways](#1-key-takeaways)
2. [Cost Model and Scope](#2-cost-model-and-scope)
3. [The Internal Structure of EUDBranch](#3-the-internal-structure-of-eudbranch)
4. [The Actual Relationship Between EUDIf and EUDIfNot](#4-the-actual-relationship-between-eudif-and-eudifnot)
5. [The Optimal Form of a Single Branch](#5-the-optimal-form-of-a-single-branch)
6. [Choosing a Branch Condition](#6-choosing-a-branch-condition)
7. [Merging Reset into a Business-Logic Trigger](#7-merging-reset-into-a-business-logic-trigger)
8. [Moving Reset into a Shared Trigger](#8-moving-reset-into-a-shared-trigger)
9. [Early Exits in Loops](#9-early-exits-in-loops)
10. [The if-else Structure](#10-the-if-else-structure)
11. [else-if Chains](#11-else-if-chains)
12. [Common Misconceptions](#12-common-misconceptions)
13. [Recommended Optimization Workflow](#13-recommended-optimization-workflow)
14. [Conclusion](#14-conclusion)

---

## 1. Key Takeaways

Keep the following principles in mind before examining the generated structures in detail.

This document uses **branch condition** to mean the Condition that is actually written into the branch trigger and evaluated by `EUDBranch`. It is not always the same expression as the business condition that determines when the branch body should run.

1. **When a simple condition and its Actions can be expressed by one native trigger, prefer a single `RawTrigger`.**  
   This form minimizes both the trigger count and the runtime path length.

2. **When using the `EUDIf` family, choose a branch condition that is false as often as possible.**  
   A false condition only needs to be checked before execution follows the default `nextptr`. A true condition also requires Actions that modify and restore the trigger chain.

3. **The choice between `EUDIf` and `EUDIfNot` depends on the logical relationship between the branch condition and the business branch.**  
   `EUDIf(C)` executes its body when `C` is true, whereas `EUDIfNot(C)` executes its body when `C` is false. Do not infer performance from the API name alone.

4. **Merging reset is a low-level optimization with strict prerequisites.**  
   It is safe only when the taken path contains a `RawTrigger` that is unconditional, guaranteed to be reached, and has an available Action slot.

5. **Establish correctness and maintainability before optimizing hot paths.**  
   Hand-written `nextptr` logic bypasses the safeguards of high-level control structures. Without performance data, do not rewrite large amounts of business logic merely to eliminate one theoretical trigger execution.

---

## 2. Cost Model and Scope

This document uses the following notation to describe different runtime paths:

| Symbol | Meaning |
| --- | --- |
| `1C` | Check the Conditions of one trigger, find them false, and therefore execute none of its Actions |
| `1T` | Evaluate a trigger successfully and execute its Actions; an unconditional trigger also counts as `1T` |
| `1A` | Add one Action to a trigger that would already execute |

These symbols compare control-flow structures; they do not represent exact CPU cycles or absolute execution time.

- `1T` already includes the trigger's condition evaluation and Action execution, so no additional `1C` is added.
- Different Conditions and Actions can have different actual costs.

The cost tables are therefore useful for answering “which structure usually has a shorter path,” but they do not replace inspecting the generated triggers and profiling the actual map.

---

## 3. The Internal Structure of EUDBranch

### 3.1 Basic Expansion

For a branch containing no more than 16 Conditions, the essential structure of `EUDBranch(C, ontrue, onfalse)` can be simplified as follows:

```python
branch = Forward()
reset = Forward()

branch << RawTrigger(
    nextptr=onfalse,
    conditions=C,
    actions=SetNextPtr(branch, reset),
)

reset << RawTrigger(
    nextptr=ontrue,
    actions=SetNextPtr(branch, onfalse),
)
```

In this structure:

- `branch` points to `onfalse` by default.
- When `C` is false, `branch` executes no Action and proceeds directly to `onfalse` through its default pointer.
- When `C` is true, `branch` changes its own `nextptr` to `reset`.
- `reset` then restores `branch.nextptr` to `onfalse` before proceeding to `ontrue`.

The two paths can be summarized as:

```text
C is false: branch --default nextptr--> onfalse

C is true:  branch --change nextptr--> reset --restore branch--> ontrue
```

`Forward` and `NextTrigger` primarily resolve trigger addresses and links. A label does not itself represent the execution of an additional full trigger.

### 3.2 Why a False Condition Is Usually Cheaper

When branch condition `C` is false, the branch performs one failed condition check: `1C`.

When branch condition `C` is true:

1. `branch` successfully executes `SetNextPtr(branch, reset)`.
2. `reset` successfully executes `SetNextPtr(branch, onfalse)`.

The branch framework alone therefore costs `2T`. This leads to the most important design rule for runtime branches:

> Without changing the business semantics, choose a branch condition that is false as often as possible.

The emphasis here is on the branch condition, not on how often the business code runs. With `EUDIfNot`, the branch condition can be false most of the time while the business code still runs most of the time.

---

## 4. The Actual Relationship Between EUDIf and EUDIfNot

The relevant call chain can be summarized as:

```text
EUDIf(C)       -> EUDJumpIfNot(C, next_elseif)
EUDIfNot(C)    -> EUDJumpIf(C, next_elseif)
EUDJumpIf(...) -> EUDBranch(...)
```

Therefore:

| Form | Condition for executing the body | Branch condition checked by `EUDBranch` |
| --- | --- | --- |
| `EUDIf()(C)` | `C` is true | `C` |
| `EUDIfNot()(C)` | `C` is false | `C` |

Both forms use the same `EUDBranch` mechanism. The only difference is whether `ontrue` or `onfalse` is connected to the branch body or the branch exit.

### 4.1 Simplified Expansion of EUDIf

```python
branch = Forward()
onfalse = Forward()
ontrue = Forward()
reset = Forward()

branch << RawTrigger(
    nextptr=onfalse,
    conditions=C,
    actions=SetNextPtr(branch, reset),
)

reset << RawTrigger(
    nextptr=ontrue,
    actions=SetNextPtr(branch, onfalse),
)

ontrue << NextTrigger()

# User code
do_something()

onfalse << NextTrigger()
```

- `C` is false: `1C`, and the user code is skipped.
- `C` is true: the branch framework costs `2T`, after which the user code runs.

### 4.2 Simplified Expansion of EUDIfNot

```python
branch = Forward()
onfalse = Forward()
ontrue = Forward()
reset = Forward()

branch << RawTrigger(
    nextptr=onfalse,
    conditions=C,
    actions=SetNextPtr(branch, reset),
)

reset << RawTrigger(
    nextptr=ontrue,
    actions=SetNextPtr(branch, onfalse),
)

onfalse << NextTrigger()

# User code
do_something()

ontrue << NextTrigger()
```

- `C` is false: `1C`, and the user code runs.
- `C` is true: the branch framework costs `2T`, and the user code is skipped.

---

## 5. The Optimal Form of a Single Branch

### 5.1 A Simple Condition and Simple Actions

Suppose P1 should gain one mineral whenever at least one Terran Marine is present in `loc`.

If the Condition and Action can be placed in the same trigger, the most compact implementation is:

```python
RawTrigger(
    conditions=Bring(P1, AtLeast, 1, "Terran Marine", "loc"),
    actions=SetResources(P1, Add, 1, Ore),
)
```

Its runtime cost is:

| Execution path | Cost |
| --- | --- |
| Condition is true; execute the Action | `1T` |
| Condition is false | `1C` |

The ordinary `EUDIf` form is:

```python
if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "loc")):
    DoActions(SetResources(P1, Add, 1, Ore))
EUDEndIf()
```

Assuming the user code generates one trigger:

| Execution path | Cost |
| --- | --- |
| Condition is true; execute the Action | `3T`: branch, reset, and the user trigger |
| Condition is false | `1C` |

For the general case of “one Condition executes a set of Actions that fits in one native trigger,” a single `RawTrigger` is usually the best implementation.

### 5.2 Rewriting with a Complementary Condition

Define:

```python
marine_present_condition = Bring(
    P1, AtLeast, 1, "Terran Marine", "loc"
)
marine_absent_condition = Bring(
    P1, Exactly, 0, "Terran Marine", "loc"
)
```

In this example, `marine_absent_condition` is the logical complement of `marine_present_condition`. The same business logic can be written as:

```python
if EUDIfNot()(marine_absent_condition):
    DoActions(SetResources(P1, Add, 1, Ore))
EUDEndIf()
```

The cost becomes:

| Business state | Branch condition `marine_absent_condition` | Cost |
| --- | --- | --- |
| A Marine is present; execute the Action | False | `1C + 1T` |
| No Marine is present; skip the Action | True | `2T` |

The three forms compare as follows:

| Form | Execute the business Action | Skip the business Action |
| --- | ---: | ---: |
| One `RawTrigger(conditions=marine_present_condition, ...)` | `1T` | `1C` |
| `EUDIf()(marine_present_condition)` | `3T` | `1C` |
| `EUDIfNot()(marine_absent_condition)` | `1C + 1T` | `2T` |

Neither `EUDIf` nor `EUDIfNot` is universally faster. Select the branch condition according to the actual state distribution:

- If `marine_present_condition` is rarely true, use `EUDIf()(marine_present_condition)` so that the branch condition is false most of the time.
- If `marine_present_condition` is frequently true and a cheap, exact complement such as `marine_absent_condition` exists, use `EUDIfNot()(marine_absent_condition)` so that the complementary condition is false most of the time.
- If the Condition and Actions fit in one `RawTrigger`, merge them directly instead of choosing between the two high-level forms.

If the complementary expression requires more Conditions, extra variables, or preliminary computation, its preparation cost may outweigh the saved branch overhead.

---

## 6. Choosing a Branch Condition

### 6.1 Infrequent Event: Area Buff

Suppose the player spends most of the game outside a buff area and enters it only occasionally:

```python
if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "buff_zone")):
    apply_buff()
EUDEndIf()
```

The branch condition `Bring(..., AtLeast, 1, ...)` is false during most game frames, so the common path pays only for a failed condition check.

### 6.2 Frequent Event: Countdown Update

Suppose a timer is nonzero during most game frames and the game state must be updated while it remains nonzero:

```python
if EUDIfNot()(timer.Exactly(0)):
    game.update()
EUDEndIf()
```

The business code is on the frequent path, but the branch condition `timer.Exactly(0)` is still rarely true:

- `timer >= 1`: the condition fails, and `1C` leads into the frequently executed business code.
- `timer == 0`: the condition succeeds, and branch/reset skips the business code.

The optimization target is therefore not “execute the business branch less often,” but “make the `EUDBranch` branch condition true less often.”

### 6.3 Decision Process

Use the following process to choose between `EUDIf` and `EUDIfNot`:

1. Identify when the business body should execute and call that expression `body_condition`.
2. Find its exact logical complement and call that expression `complement_condition`.
3. Estimate how often the two states occur at runtime.
4. Compare the number of Conditions, evaluation method, and preparation cost of `body_condition` and `complement_condition`.
5. Use the expression that is false more often as the branch condition:
   - `EUDIf()(body_condition)`; or
   - `EUDIfNot()(complement_condition)`.
6. Compile and inspect the generated triggers on the hot path before deciding whether a hand-written structure is justified.

---

## 7. Merging Reset into a Business-Logic Trigger

### 7.1 Principle

When `C` is true, an ordinary `EUDIf(C)` executes:

```text
branch -> reset -> user code
```

Suppose the branch body contains a `RawTrigger` that is unconditional, guaranteed to be reached on the taken path, and guaranteed to execute before `branch` is visited again. The following Action:

```python
SetNextPtr(branch, onfalse)
```

can be added to that trigger's Action list, eliminating the standalone reset trigger. The target trigger may appear at the beginning, in the middle, or at the end of the business code. What matters is that every relevant path reaches it before `branch` is visited again.

```text
branch -> business code -> guaranteed trigger (also performs reset)
       -> remaining business code
```

### 7.2 Executable Example

Original high-level structure:

```python
count = EUDVariable()

if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "loc")):
    RawTrigger(
        actions=[
            SetResources(P1, Add, 1, Ore),
            count.AddNumber(1),
        ]
    )
    f_simpleprint(count)
EUDEndIf()
```

Manual expansion with reset merged:

```python
branch = Forward()
onfalse = Forward()
body = Forward()
count = EUDVariable()

branch << RawTrigger(
    nextptr=onfalse,
    conditions=Bring(P1, AtLeast, 1, "Terran Marine", "loc"),
    actions=SetNextPtr(branch, body),
)

body << RawTrigger(
    actions=[
        SetNextPtr(branch, onfalse),  # Restore branch's default nextptr
        SetResources(P1, Add, 1, Ore),
        count.AddNumber(1),
    ]
)

f_simpleprint(count)

onfalse << NextTrigger()
```

The branch-framework cost changes as follows:

| Condition state | Before merging | After merging |
| --- | ---: | ---: |
| `C` is false | `1C` | `1C` |
| `C` is true | `2T`, then enter user code | `1T`, then enter user code |

The merge does not eliminate the reset Action. It eliminates the standalone trigger whose only purpose was to execute that Action.

### 7.3 Safety Requirements

Reset merging is safe only when all of the following conditions hold:

1. **The target `RawTrigger` is guaranteed to execute on every taken path.**
2. **The target trigger is unconditional, or its Conditions are guaranteed to be true.**  
   If those Conditions fail, the reset Action does not execute and `branch.nextptr` remains connected to the taken path.
3. **Reset completes before `branch` can be visited again.**
4. **The target trigger has an available Action slot.**  
   A native trigger can contain at most 64 Actions.

Do not merge reset directly when:

- the body consists only of highly encapsulated functions and no suitable guaranteed trigger can be identified;
- the intended business trigger has Conditions that may fail; or
- the body can jump away before reset executes.

### 7.4 Why EUDIfNot Usually Cannot Merge Reset into Its Own Body

In `EUDIfNot(C)`:

- when `C` is false, execution follows the default path into the body without executing reset;
- when `C` is true, branch and reset execute, after which the body is skipped.

The reset path and the user-body path are therefore different, so reset usually cannot be merged into that body. If an `else` branch exists, a true branch condition enters the other branch, and reset may be merged into any business trigger on that side that is unconditional, guaranteed to be reached, and timely enough to restore the pointer.

---

## 8. Moving Reset into a Shared Trigger

If the taken path contains no suitable business trigger but a nearby shared trigger is guaranteed to execute every game frame, the reset Action can sometimes be moved into that trigger:

```python
branch = Forward()
onfalse = Forward()
ontrue = Forward()
global_tick = EUDVariable()

# Guaranteed to execute every frame before branch
RawTrigger(
    actions=[
        global_tick.AddNumber(1),  # Existing work of the shared trigger
        SetNextPtr(branch, onfalse),
    ]
)

branch << RawTrigger(
    nextptr=onfalse,
    conditions=timer.Exactly(0),
    actions=SetNextPtr(branch, ontrue),
)

onfalse << NextTrigger()
game.update()
ontrue << NextTrigger()
```

This changes “execute a standalone reset trigger only when the branch condition is true” into “execute one extra reset Action every frame in an existing shared trigger”:

| Strategy | Path where the condition is true | Path where the condition is false |
| --- | --- | --- |
| Standalone reset | Execute an extra reset trigger | Do not execute reset |
| Reset in a shared trigger | Eliminate one standalone trigger | Execute an extra `1A` every frame |

The trade-off depends on how often the branch condition is true, how frequently the shared trigger runs, and the actual cost of the Action. More importantly, the move must satisfy strict ordering requirements:

- the shared reset must execute before the next visit to `branch`; and
- no path may bypass the shared trigger and then re-enter `branch`.

This optimization must be validated against the generated result and actual hot-path data. It should not be treated as a default template.

---

## 9. Early Exits in Loops

### 9.1 Reducing Nesting with Continue

When iterating over units, code often needs to reject objects that do not meet the required criteria:

```python
for cunit in EUDLoopCUnit():
    if EUDIfNot()(cunit.order == EncodeUnitOrder("Die")):
        if EUDIf()(cunit.unitType == EncodeUnit("Terran Marine")):
            do_something()
        EUDEndIf()
    EUDEndIf()
```

Compared with nested `EUDIf` blocks, early `continue` operations keep the main path visible and avoid excessive indentation:

```python
for cunit in EUDLoopCUnit():
    EUDContinueIf(cunit.order == EncodeUnitOrder("Die"))
    EUDContinueIfNot(cunit.unitType == EncodeUnit("Terran Marine"))

    # Process only live Terran Marines
    do_something()
```

### 9.2 Relationship to EUDIf

These structures can be understood in terms of whether the remainder of the loop body executes.

```python
EUDContinueIf(C)
```

is equivalent to:

```python
if EUDIfNot()(C):
    # Remaining loop body
    ...
EUDEndIf()
```

Whereas:

```python
EUDContinueIfNot(C)
```

is equivalent to:

```python
if EUDIf()(C):
    # Remaining loop body
    ...
EUDEndIf()
```

The corresponding low-level paths are:

| API | Branch condition is true | Branch condition is false |
| --- | --- | --- |
| `EUDContinueIf(C)` | Execute branch/reset and continue | Enter the remaining loop body after a failed check |
| `EUDContinueIfNot(C)` | Execute branch/reset and enter the remaining loop body | Continue after a failed check |

The branch condition passed to either API should therefore still be false as often as possible.

### 9.3 Filtering Dead Units

If most objects in the iteration are still alive:

```python
EUDContinueIf(cunit.order == EncodeUnitOrder("Die"))
# or
EUDContinueIf(cunit.order == 0)
```

This is usually better than expressing the same logic with a complementary condition that is true most of the time: `EUDContinueIfNot(cunit.order >= 1)`.

The death condition, `cunit.order == 0`, is false most of the time. The common path therefore performs one failed check and continues processing the unit.

### 9.4 Filtering by Unit Type

To select Protoss Zealots:

```python
EUDContinueIfNot(
    cunit.unitType == EncodeUnit("Protoss Zealot")
)
```

When Zealots are a minority of the iterated units:

- most units are not Zealots;
- the branch condition fails; and
- execution continues directly to the next iteration.

Even when Zealots are the majority, do not casually replace one equality test with two range tests:

```python
unit_id = cunit.unitType
EUDContinueIf(unit_id <= EncodeUnit("Protoss Zealot") - 1)
EUDContinueIf(unit_id >= EncodeUnit("Protoss Zealot") + 1)
```

This rewrite adds conditional branches, static triggers, and maintenance overhead. Its benefit may not compensate for those costs. If one unit type genuinely dominates the data set, a better solution is usually to organize the data with a dedicated `UnitGroup` or another collection and iterate only over the target units:

```python
for unit in zealots.cploop:
    do_something()
```

Reducing the number of candidate objects is often more valuable than micro-optimizing a branch for every candidate.

### 9.5 Opportunity to Merge Reset for EUDContinueIfNot

When `C` is true, `EUDContinueIfNot(C)` enters the remainder of the loop body. Its structure is fundamentally the same as `EUDIf(C)`. If that remaining body contains any unconditional trigger that is guaranteed to be reached before the next visit to `branch`, reset may be merged into it.

### 9.6 Complete Example: Selecting Live Protoss Zealots

The goal is to reject dead units and non-Zealots inside `EUDLoopCUnit`, allowing only live Protoss Zealots to enter the business logic.

#### Readability-First Form

```python
for cunit in EUDLoopCUnit():
    EUDContinueIf(cunit.order == EncodeUnitOrder("Die"))
    EUDContinueIfNot(
        cunit.unitType == EncodeUnit("Protoss Zealot")
    )

    process_alive_zealot(cunit)
```

This code expresses one rule: `process_alive_zealot` runs only for objects that are both alive and of type Protoss Zealot.

The manually optimized result is shown below.

`process_alive_zealot` represents the project's actual business function. `alive_zealot_count` demonstrates an existing business trigger that has room for the reset Action.

```python
CURRENT_PLAYER_ADDR = 0x6509B0
CUNIT_ORDER_DWORD_OFFSET = 0x4D // 4
CUNIT_UNIT_TYPE_DWORD_OFFSET = 0x64 // 4

alive_zealot_count = EUDVariable()

for cunit in EUDLoopCUnit():
    # CurrentPlayer = EPD(cunit) + CUNIT_ORDER_DWORD_OFFSET
    VProc(cunit, [
        SetMemory(0x6509B0, SetTo, 0x4D // 4),
        cunit.QueueAddTo(EPD(0x6509B0)),
    ])

    EUDContinueIf(  # cunit.order == EncodeUnitOrder("Die")
        MemoryXEPD(
            CurrentPlayer,
            Exactly,
            EncodeUnitOrder("Die") << 8,
            0xFF00,
        )
    )

    # Move CurrentPlayer from the dword containing order
    # to the dword containing unitType.
    DoActions(SetMemory(0x6509B0, Add, 0x64 // 4 - 0x4D // 4))

    type_branch = Forward()
    process_zealot = Forward()
    skip_non_zealot = Forward()

    type_branch << RawTrigger(
        nextptr=skip_non_zealot,
        conditions=MemoryXEPD(
            CurrentPlayer,
            Exactly,
            EncodeUnit("Protoss Zealot"),
            0xFF,
        ),
        actions=SetNextPtr(type_branch, process_zealot),
    )

    process_zealot << NextTrigger()

    # An unconditional, guaranteed RawTrigger that already belongs
    # to the business logic.
    RawTrigger(
        actions=[
            alive_zealot_count.AddNumber(1),
            SetNextPtr(type_branch, skip_non_zealot),  # reset is here
        ]
    )

    process_alive_zealot(cunit)

    skip_non_zealot << NextTrigger()
```

The important details are:

- `EUDContinueIf` rejects dead units early.
- `CurrentPlayer` is then moved to the dword containing `unitType`, and the unit type is read with a `0xFF` mask.
- For a non-Zealot, the `type_branch` condition fails and the default `nextptr` leads directly to `skip_non_zealot`.
- For a Zealot, the `type_branch` condition succeeds and execution enters the business path at `process_zealot`.
- `SetNextPtr(type_branch, skip_non_zealot)` is merged into the business trigger that counts live Zealots, so no standalone reset trigger is needed.
- Reset does not have to be in the first trigger of the business code. It may appear anywhere in the body, provided that it is guaranteed to execute before `type_branch` is visited again.

---

## 10. The if-else Structure

### 10.1 Standard Form

```python
if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "loc")):
    f_simpleprint("1 marine")
if EUDElse()():
    f_simpleprint("0 marine")
EUDEndIf()
```

The simplified structure is:

```python
branch = Forward()
onfalse = Forward()
ontrue = Forward()
reset = Forward()
end = Forward()

branch << RawTrigger(
    nextptr=onfalse,
    conditions=C,
    actions=SetNextPtr(branch, reset),
)

reset << RawTrigger(
    nextptr=ontrue,
    actions=SetNextPtr(branch, onfalse),
)

ontrue << NextTrigger()

# True branch
do_on_true()
SetNextTrigger(end)

onfalse << NextTrigger()

# False branch
do_on_false()

end << NextTrigger()
```

`if-else` does not change the basic branch/reset mechanism. It only adds a join point after the two bodies:

- after the true body finishes, execution jumps to `end` to avoid falling through into the false body;
- after the false body finishes, execution reaches `end` naturally.

### 10.2 Is EUDIfNot + EUDElse Meaningful?

`EUDIfNot(C) + EUDElse` may appear to do nothing more than swap two complementary branches, but it still has practical value. Swapping the bodies allows the structure to be rewritten as `EUDIf(C) + EUDElse`, so the two forms are equally expressive. The choice should nevertheless consider:

- which form expresses the business semantics more clearly;
- which expression is a better branch condition because it is false more often; and
- which body is entered when the branch condition is true and can therefore receive the merged reset.

Whether the code uses `EUDIf` or `EUDIfNot`, `EUDBranch` still checks the supplied `C`, and its successful path still pays the branch/reset cost.

### 10.3 Timer Example

Original logic:

```python
if EUDIf()(timer.AtLeast(1)):
    RawTrigger(actions=timer.SubtractNumber(1))
if EUDElse()():
    RawTrigger(actions=timer.SetNumber(24))
EUDEndIf()
```

If the timer is nonzero most of the time, use the rarely true `timer.Exactly(0)` as the branch condition and swap the two business bodies:

```python
if EUDIf()(timer.Exactly(0)):
    RawTrigger(actions=timer.SetNumber(24))
if EUDElse()():
    RawTrigger(actions=timer.SubtractNumber(1))
EUDEndIf()
```

Because the body entered when the branch condition is true contains an unconditional, guaranteed `RawTrigger`, reset can also be merged:

```python
branch = Forward()
onfalse = Forward()
ontrue = Forward()
end = Forward()

branch << RawTrigger(
    nextptr=onfalse,
    conditions=timer.Exactly(0),
    actions=SetNextPtr(branch, ontrue),
)

ontrue << RawTrigger(
    nextptr=end,
    actions=[
        SetNextPtr(branch, onfalse),
        timer.SetNumber(24),
    ],
)

onfalse << RawTrigger(
    actions=timer.SubtractNumber(1),
)

end << NextTrigger()
```

This structure applies two optimizations:

1. `timer.Exactly(0)` fails during ordinary countdown frames.
2. The infrequent successful path combines reset and `timer.SetNumber(24)` in one trigger.

---

## 11. else-if Chains

### 11.1 Generated Structure

A typical chain is:

```python
if EUDIf()(C1):
    body1()
if EUDElseIf()(C2):
    body2()
if EUDElseIf()(C3):
    body3()
if EUDElse()():
    default_body()
EUDEndIf()
```

It produces an ordered sequence of checks:

```text
Check C1
├─ True:  execute body1, then jump to end
└─ False: check C2
   ├─ True:  execute body2, then jump to end
   └─ False: check C3
      ├─ True:  execute body3, then jump to end
      └─ False: execute default_body
```

Every `EUDIf` or `EUDElseIf` Condition is implemented through `EUDBranch`. For one group of Conditions:

- if the current condition is false, pay `1C` and check the next branch;
- if the current condition is true, pay that branch's branch/reset cost, execute its body, and jump to the shared `end`; and
- a later branch is checked only after every earlier condition has failed.

### 11.2 Ordering Principles

Optimizing an `else-if` chain requires more than considering each condition in isolation; reach probability also matters:

- placing a common branch earlier can avoid later checks;
- placing the common state in `else` keeps the preceding branch conditions false more often, but requires checking all of them;
- when condition costs differ significantly, prefer cheap conditions with high rejection power.

### 11.3 Reset Merging

An ordinary `EUDElseIf()(C)` enters its body when `C` is true. If that body contains any unconditional, guaranteed `RawTrigger` that executes before the next visit to `branch`, reset may be merged in the same way as for `EUDIf(C)`.

---

## 12. Common Misconceptions

### 12.1 Judging Performance from the API Name Alone

Incorrect:

> `EUDIfNot` is always faster than `EUDIf`, or `EUDIf` is always faster than `EUDIfNot`.

Correct:

> Compare how often the branch condition passed to `EUDBranch` is true or false at runtime, as well as the cost of generating and evaluating that condition.

### 12.2 Confusing a Frequently Executed Body with a Frequently True Condition

`EUDIfNot(low_frequency_condition)` can execute its body most of the time while its branch condition fails most of the time. Body frequency and branch-condition truth probability are not the same concept.

### 12.3 Merging Reset into a Conditional Trigger

If the target trigger's Conditions fail, the reset Action does not execute. The next visit to `branch` may then follow the wrong path. Such bugs are state-dependent and often difficult to diagnose.

### 12.4 Ignoring the Action Limit

Appending `SetNextPtr` to an existing `RawTrigger` consumes one Action slot. A trigger that has reached the 64-Action limit must be split and cannot accept the merged Action. In ordinary branches, however, reaching that limit is uncommon.

### 12.5 Reducing Runtime Triggers While Ignoring Static Size

Replacing one equality check with multiple range checks may reduce the success probability of one path, but it also adds conditional branches, static triggers, memory usage, and maintenance complexity. Optimization must account for both runtime frequency and generated size.

### 12.6 Hand-Writing nextptr Too Early

High-level control structures are easier to read and less likely to corrupt the trigger chain. A manual expansion is worthwhile only when the hot path, generated result, and expected benefit are all clear.

---

## 13. Recommended Optimization Workflow

### Step 1: Write Clear, Correct High-Level Control Flow

Start with `EUDIf`, `EUDIfNot`, `EUDElseIf`, `EUDElse`, and early-exit APIs to express the business logic.

### Step 2: Identify Hot Paths

Pay particular attention to:

- loops that run every frame;
- filters applied to large numbers of units or objects; and
- deeply nested conditions.

### Step 3: Choose the Branch Condition

Analyze:

- the probability that the condition is true;
- whether an exact complementary condition exists;
- the number and evaluation cost of the Conditions; and
- whether preparing the condition generates extra triggers.

Choose a branch condition that is false on the hot path as often as possible.

### Step 4: Prefer Structural Optimizations

Evaluate the following options, generally in this order:

1. Reduce the number of objects being iterated.
2. Merge simple Conditions and Actions into one `RawTrigger`.
3. Use early exits to avoid unnecessary downstream work.
4. Reorder conditions when doing so is semantically safe.
5. Choose the appropriate `EUDIf`/`EUDIfNot` orientation.
6. Consider hand-written branch/reset logic only after the preceding options.

### Step 5: Verify the Requirements for Reset Merging

Confirm that the target trigger:

- is unconditional and guaranteed to be reached;
- has an available Action slot; and
- performs reset before `branch` can be entered again.

### Step 6: Verify the Generated Result

At minimum, inspect:

- the default target of `branch.nextptr`;
- the temporary target used when the condition is true;
- where reset executes;
- the true path, false path, and every early-exit path; and
- pointer state before the next loop iteration.

---

## 14. Conclusion

The performance differences among `EUDIf` forms arise from how `EUDBranch` modifies the trigger chain:

- when the branch condition is false, execution follows the default `nextptr`;
- when the branch condition is true, execution modifies `branch`, performs reset, and then enters the target path.

This leads to a consistent optimization strategy:

1. Merge simple Conditions and Actions into one `RawTrigger` whenever possible.
2. Choose a branch condition that is false as often as possible.
3. Select `EUDIf`, `EUDIfNot`, or an early-jump API according to the business logic.
4. Reduce iteration volume and unnecessary work before applying micro-optimizations.
5. Merge or relocate reset only when the safety conditions are clear and the hot-path benefit is credible.
