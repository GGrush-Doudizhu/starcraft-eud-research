# EUDIf 控制流机制与优化实践

> 本文作者: GGrush.
> 写作时间: 2026.07.25

> eudplib 源码：[armoha/eudplib](https://github.com/armoha/eudplib)  
> 致谢：感谢 Armoha 长期致力于维护 eudplib，并感谢他对我的帮助；同时感谢 eudplib 原作者 trgk。

## 摘要

`EUDIf`、`EUDIfNot`、`EUDElseIf` 与 `EUDElse` 为 eudplib 提供了接近高级语言的运行时分支语法，但这些结构最终仍会展开为星际争霸触发器及其 `nextptr` 跳转关系。相同的业务逻辑如果选择了不同的条件表达式或分支方向，生成结果可能具有明显不同的运行时开销。

本文从 `EUDBranch` 的底层结构出发，说明 `EUDIf` 系列控制结构的工作原理，建立一套便于比较的成本记号，并给出单分支、`if-else`、`else-if` 链和循环提前退出的优化方法。本文同时讨论直接使用 `RawTrigger`、将 reset 动作合并到业务触发器，以及把 reset 迁移到公共触发器等进阶方案的收益、限制和安全条件。

本文的实现分析以 eudplib 0.80.6 为基准。

## 目录

1. [核心结论](#1-核心结论)
2. [成本模型与适用范围](#2-成本模型与适用范围)
3. [EUDBranch 的底层结构](#3-eudbranch-的底层结构)
4. [EUDIf 与 EUDIfNot 的真实关系](#4-eudif-与-eudifnot-的真实关系)
5. [单分支的最优写法](#5-单分支的最优写法)
6. [如何选择 branch 条件](#6-如何选择-branch-条件)
7. [将 reset 合并到业务触发器](#7-将-reset-合并到业务触发器)
8. [将 reset 迁移到公共触发器](#8-将-reset-迁移到公共触发器)
9. [循环中的提前退出](#9-循环中的提前退出)
10. [if-else 结构](#10-if-else-结构)
11. [else-if 链](#11-else-if-链)
12. [常见误区](#12-常见误区)
13. [推荐的优化流程](#13-推荐的优化流程)
14. [总结](#14-总结)
15. [Armoha's Comments](#15-armohas-comments)

---

## 1. 核心结论

阅读具体展开过程之前，可以先记住以下原则：

本文将实际写入 branch 触发器、由 `EUDBranch` 检查的 Condition 称为 **branch 条件**。它与“业务分支体应当何时执行”的业务条件并不总是同一个表达式。

1. **能够直接表达为一个原生触发器的简单条件动作，优先使用一个 `RawTrigger`。**  
   这是触发器数量和运行时路径都最短的形式。

2. **使用 `EUDIf` 系列结构时，应让交给 `EUDBranch` 的 branch 条件尽可能经常为假。**  
   条件为假时只需完成条件检查并沿默认 `nextptr` 前进；条件为真时还要执行修改和恢复触发器链的动作。

3. **选择 `EUDIf` 还是 `EUDIfNot`，取决于 branch 条件与业务分支之间的逻辑关系。**  
   `EUDIf(C)` 在 `C` 为真时执行分支体；`EUDIfNot(C)` 在 `C` 为假时执行分支体。不要仅凭接口名称判断性能。

4. **reset 合并是一项有前提的底层优化。**  
   只有当分支命中路径上存在无条件、必定到达且仍有 Action 槽位的 `RawTrigger` 时，才能安全地把 reset 动作合并进去。

5. **先保证逻辑正确和代码可维护，再针对热点路径优化。**  
   手写 `nextptr` 会绕过高级控制结构的安全性。没有性能数据时，不应为了理论上的一个触发器差异大规模改写业务代码。

---

## 2. 成本模型与适用范围

为了描述不同执行路径，本文使用以下记号：

| 记号 | 含义 |
| --- | --- |
| `1C` | 检查一个触发器中的条件，但条件不成立，因此不执行其 Action |
| `1T` | 一个触发器条件成立并执行其 Action；无条件触发器也计为 `1T` |
| `1A` | 在一个原本就会执行的触发器中新增一个 Action |

这些记号用于比较控制流结构，不是精确的 CPU 周期或绝对耗时：

- `1T` 已包含该触发器的条件判定及 Action 执行，不再额外叠加 `1C`。
- 不同 Condition 和 Action 的实际成本可能不同。

因此，本文的成本表适合回答“哪种结构通常更短”，不能替代对实际地图的编译结果检查和运行时性能测试。

---

## 3. EUDBranch 的底层结构

### 3.1 基本展开

对于不超过 16 个条件的一次分支，`EUDBranch(C, ontrue, onfalse)` 的核心结构可以简化为：

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

其中：

- `branch` 默认指向 `onfalse`。
- 当 `C` 为假时，`branch` 不执行 Action，直接沿默认指针进入 `onfalse`。
- 当 `C` 为真时，`branch` 把自己的 `nextptr` 改为 `reset`。
- `reset` 随后把 `branch.nextptr` 恢复为 `onfalse`，再进入 `ontrue`。

可以把两条路径概括为：

```text
C 为假：branch --默认 nextptr--> onfalse

C 为真：branch --修改 nextptr--> reset --恢复 branch--> ontrue
```

`Forward` 和 `NextTrigger` 主要用于解析触发器地址与连接关系；标签本身不等于额外执行一个完整触发器。

### 3.2 为什么条件为假通常更便宜

branch 条件 `C` 为假时，分支只产生一次失败的条件检查，即 `1C`。

branch 条件 `C` 为真时：

1. `branch` 成功执行 `SetNextPtr(branch, reset)`；
2. `reset` 成功执行 `SetNextPtr(branch, onfalse)`。

仅分支框架本身就会产生 `2T`。因此，设计运行时分支时最重要的原则是：

> 在不改变业务语义的前提下，让传给 `EUDBranch` 的 branch 条件尽可能经常为假。

这里强调的是 branch 条件，不是“业务代码是否经常执行”。借助 `EUDIfNot`，完全可以让 branch 条件大多数时候为假，同时让业务代码大多数时候执行。

---

## 4. EUDIf 与 EUDIfNot 的真实关系

相关调用关系可以概括为：

```text
EUDIf(C)       -> EUDJumpIfNot(C, next_elseif)
EUDIfNot(C)    -> EUDJumpIf(C, next_elseif)
EUDJumpIf(...) -> EUDBranch(...)
```

因此：

| 写法 | 分支体执行条件 | `EUDBranch` 检查的 branch 条件 |
| --- | --- | --- |
| `EUDIf()(C)` | `C` 为真 | `C` |
| `EUDIfNot()(C)` | `C` 为假 | `C` |

二者使用的是同一套 `EUDBranch` 机制，区别只在于 `ontrue` 和 `onfalse` 分别连接到分支体还是分支出口。

### 4.1 EUDIf 的简化展开

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

# 用户代码
do_something()

onfalse << NextTrigger()
```

- `C` 为假：`1C`，跳过用户代码。
- `C` 为真：分支框架为 `2T`，随后执行用户代码。

### 4.2 EUDIfNot 的简化展开

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

# 用户代码
do_something()

ontrue << NextTrigger()
```

- `C` 为假：`1C`，执行用户代码。
- `C` 为真：分支框架为 `2T`，跳过用户代码。

---

## 5. 单分支的最优写法

### 5.1 简单条件与简单动作

假设需求是：当 P1 在 `loc` 中至少拥有 1 个 Terran Marine 时，增加 1 点矿物。

如果条件和动作可以直接放入同一个触发器，最紧凑的实现是：

```python
RawTrigger(
    conditions=Bring(P1, AtLeast, 1, "Terran Marine", "loc"),
    actions=SetResources(P1, Add, 1, Ore),
)
```

运行时成本为：

| 执行路径 | 成本 |
| --- | --- |
| 条件成立，执行动作 | `1T` |
| 条件不成立 | `1C` |

使用普通 `EUDIf`：

```python
if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "loc")):
    DoActions(SetResources(P1, Add, 1, Ore))
EUDEndIf()
```

在用户代码生成一个触发器的前提下：

| 执行路径 | 成本 |
| --- | --- |
| 条件成立，执行动作 | `3T`：branch、reset、用户触发器 |
| 条件不成立 | `1C` |

因此，对于“一个条件触发一组可容纳于同一原生触发器的 Action”这一类需求，直接使用一个 `RawTrigger` 通常是最佳方案。

### 5.2 用互补条件改写

令：

```python
marine_present_condition = Bring(
    P1, AtLeast, 1, "Terran Marine", "loc"
)
marine_absent_condition = Bring(
    P1, Exactly, 0, "Terran Marine", "loc"
)
```

在该例中，`marine_absent_condition` 是 `marine_present_condition` 的逻辑补集。相同业务逻辑也可以写成：

```python
if EUDIfNot()(marine_absent_condition):
    DoActions(SetResources(P1, Add, 1, Ore))
EUDEndIf()
```

成本变为：

| 业务状态 | branch 条件 `marine_absent_condition` | 成本 |
| --- | --- | --- |
| 存在 Marine，执行动作 | 假 | `1C + 1T` |
| 不存在 Marine，跳过动作 | 真 | `2T` |

三种写法的对比如下：

| 写法 | 执行业务动作 | 跳过业务动作 |
| --- | ---: | ---: |
| 单个 `RawTrigger(conditions=marine_present_condition, ...)` | `1T` | `1C` |
| `EUDIf()(marine_present_condition)` | `3T` | `1C` |
| `EUDIfNot()(marine_absent_condition)` | `1C + 1T` | `2T` |

这里不存在脱离场景的“`EUDIf` 永远优于 `EUDIfNot`”或相反结论。应根据状态分布选择 branch 条件：

- `marine_present_condition` 很少成立：使用 `EUDIf()(marine_present_condition)`，让该 branch 条件大多数时候为假。
- `marine_present_condition` 经常成立，且存在便宜、准确的互补条件 `marine_absent_condition`：使用 `EUDIfNot()(marine_absent_condition)`，让互补条件大多数时候为假。
- 条件和动作能合入一个 `RawTrigger`：优先直接合并，通常无需在前两种结构之间权衡。

如果互补表达式需要更多 Condition、额外变量或前置计算，节省的分支成本可能被条件准备成本抵消。

---

## 6. 如何选择 branch 条件

### 6.1 低频事件：例如区域增益

玩家大部分时间不在增益区域内，进入区域只是低频事件：

```python
if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "buff_zone")):
    apply_buff()
EUDEndIf()
```

branch 条件 `Bring(..., AtLeast, 1, ...)` 在大多数游戏帧为假，因此常见路径只承担失败条件检查。

### 6.2 高频事件：例如倒计时更新

假设计时器在绝大多数游戏帧都非零，此时需要持续更新游戏状态：

```python
if EUDIfNot()(timer.Exactly(0)):
    game.update()
EUDEndIf()
```

业务代码是高频路径，但 branch 条件 `timer.Exactly(0)` 仍然是低频成立条件：

- `timer >= 1`：条件失败，以 `1C` 进入高频业务代码；
- `timer == 0`：条件成功，通过 branch 和 reset 跳过业务代码。

这说明优化目标不是“让业务分支少执行”，而是“让 `EUDBranch` 的 branch 条件尽可能少成立”。

### 6.3 决策步骤

选择 `EUDIf` 或 `EUDIfNot` 时，可以按以下顺序判断：

1. 明确业务分支体何时执行，将对应表达式记为 `body_condition`。
2. 找出 `body_condition` 的严格互补表达式，记为 `complement_condition`。
3. 估计两种状态在真实运行中的出现频率。
4. 比较 `body_condition` 与 `complement_condition` 的条件数量、求值方式和准备成本。
5. 选择更经常为假的表达式作为 branch 条件：
   - 使用 `EUDIf()(body_condition)`；或
   - 使用 `EUDIfNot()(complement_condition)`。
6. 编译并检查热点路径生成的触发器，再决定是否值得手写结构。

---

## 7. 将 reset 合并到业务触发器

### 7.1 优化原理

普通 `EUDIf(C)` 在 `C` 为真时依次执行：

```text
branch -> reset -> 用户代码
```

如果分支体中存在一个无条件、在命中路径上必定到达，并且会在 branch 再次被访问前执行的 `RawTrigger`，可以把：

```python
SetNextPtr(branch, onfalse)
```

加入该触发器的 Action 列表，从而让独立的 reset 触发器消失。这个触发器可以位于业务代码的开头、中间或末尾；关键在于所有相关路径都会在再次访问 branch 前执行它。

```text
branch -> 业务代码 -> 某个必达触发器（同时执行 reset）-> 后续业务代码
```

### 7.2 可执行示例

原始高级结构：

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

手动展开并合并 reset：

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
        SetNextPtr(branch, onfalse),  # 恢复 branch 的默认 nextptr
        SetResources(P1, Add, 1, Ore),
        count.AddNumber(1),
    ]
)

f_simpleprint(count)

onfalse << NextTrigger()
```

分支框架的路径成本由：

| 条件状态 | 合并前 | 合并后 |
| --- | ---: | ---: |
| `C` 为假 | `1C` | `1C` |
| `C` 为真 | `2T`，随后进入用户代码 | `1T`，随后进入用户代码 |

合并后并没有消除 reset 这个动作，而是消除了仅用于执行 reset 的独立触发器。

### 7.3 安全条件

只有同时满足以下条件，reset 合并才是安全的：

1. **目标 `RawTrigger` 在条件命中路径上必定执行。**
2. **目标触发器自身无条件，或能保证其条件必定成立。**  
   如果目标触发器条件失败，reset Action 不会执行，`branch.nextptr` 将保持在命中路径。
3. **在 branch 再次被访问前，reset 一定已经完成。**
4. **目标触发器仍有 Action 槽位。**  
   原生触发器最多容纳 64 个 Action；达到上限时无法直接追加。

以下情况不适合直接合并：

- 分支体只有高度封装函数，无法确认其中是否存在可安全插入 Action 的必达触发器；
- 计划合并 reset 的业务触发器带有可能失败的 Condition；
- 分支体在执行 reset 前可能跳转离开；

### 7.4 为什么 EUDIfNot 通常不能合并到自身分支体

在 `EUDIfNot(C)` 中：

- `C` 为假时，沿默认路径进入分支体，不会执行 reset；
- `C` 为真时，执行 branch 和 reset，然后跳过分支体。

reset 所在路径与用户分支体所在路径不同，因此通常无法把 reset 合并到这个分支体中。若存在 `else`，则 branch 条件 `C` 为真时会进入另一侧分支，reset 有机会合并到那一侧任意一个满足“无条件、必达、及时恢复”要求的业务触发器。

---

## 8. 将 reset 迁移到公共触发器

如果命中路径上没有可合并的业务触发器，但附近存在每个游戏帧都必定执行的公共触发器，可以考虑把 reset 动作迁移过去：

```python
branch = Forward()
onfalse = Forward()
ontrue = Forward()
global_tick = EUDVariable()

# 每帧必定执行，并且在 branch 之前运行
RawTrigger(
    actions=[
        global_tick.AddNumber(1),  # 该公共触发器原有的工作
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

这种方法把“仅在 branch 条件成立时执行一个独立 reset 触发器”改为“每帧在现有公共触发器中多执行一个 reset Action”：

| 方案 | 条件成功路径 | 条件失败路径 |
| --- | --- | --- |
| 独立 reset | 执行额外 reset 触发器 | 不执行 reset |
| 公共触发器 reset | 省去一个独立触发器 | 每帧额外执行 `1A` |

是否值得取决于 branch 条件成立概率、公共触发器频率以及 Action 的实际成本。更重要的是，迁移必须满足严格的时序条件：

- 公共 reset 必须在 branch 下一次被访问前执行；
- 不能存在绕过公共触发器但重新进入 branch 的路径；

这是一项需要通过生成结果和实际热点数据验证的优化，不应作为默认模板。

---

## 9. 循环中的提前退出

### 9.1 使用 Continue 降低嵌套

遍历单位时，经常需要排除不符合条件的对象：

```python
for cunit in EUDLoopCUnit():
    if EUDIfNot()(cunit.order == EncodeUnitOrder("Die")):
        if EUDIf()(cunit.unitType == EncodeUnit("Terran Marine")):
            do_something()
        EUDEndIf()
    EUDEndIf()
```

与嵌套 `EUDIf` 相比，提前 `continue` 更能突出主流程，避免过多的缩进和嵌套。

```python
for cunit in EUDLoopCUnit():
    EUDContinueIf(cunit.order == EncodeUnitOrder("Die"))
    EUDContinueIfNot(cunit.unitType == EncodeUnit("Terran Marine"))

    # 只处理存活的 Terran Marine
    do_something()
```

### 9.2 与 EUDIf 的等价关系

控制结构可以按“剩余循环体是否执行”理解：

```python
EUDContinueIf(C)
```

等价于：

```python
if EUDIfNot()(C):
    # 剩余循环体
    ...
EUDEndIf()
```

而：

```python
EUDContinueIfNot(C)
```

等价于：

```python
if EUDIf()(C):
    # 剩余循环体
    ...
EUDEndIf()
```

对应的底层跳转为：

| 接口 | branch 条件为真 | branch 条件为假 |
| --- | --- | --- |
| `EUDContinueIf(C)` | 执行 branch/reset 并 continue | 以失败检查进入剩余循环体 |
| `EUDContinueIfNot(C)` | 执行 branch/reset 并进入剩余循环体 | 以失败检查 continue |

因此，仍应尽量让传入接口的 branch 条件经常为假。

### 9.3 死亡单位过滤

如果遍历对象大多数仍然存活，则：

```python
EUDContinueIf(cunit.order == EncodeUnitOrder("Die"))
# or
EUDContinueIf(cunit.order == 0)
```

通常优于为了表达相同逻辑而构造一个经常为真的互补条件（`EUDContinueIfNot(cunit.order >= 1)`）。
死亡条件（`cunit.order == 0`）大多数时候为假，常见路径只需一次失败检查并继续处理单位。

### 9.4 单位类型过滤

筛选 Protoss Zealot：

```python
EUDContinueIfNot(
    cunit.unitType == EncodeUnit("Protoss Zealot")
)
```

当 Zealot 在遍历对象中占少数时：

- 大多数对象不等于 Zealot；
- branch 条件判断失败；
- 直接 continue。

即使 Zealot 占多数，也不应轻易把一次等值比较改成两个范围判断：

```python
unit_id = cunit.unitType
EUDContinueIf(unit_id <= EncodeUnit("Protoss Zealot") - 1)
EUDContinueIf(unit_id >= EncodeUnit("Protoss Zealot") + 1)
```

这种改写会增加条件分支、静态触发器数量和维护成本，收益未必能够抵消开销。若某一种单位确实占绝大多数，更有效的方案通常是在数据组织阶段使用专门的 `UnitGroup` 或其他集合，只遍历目标单位：

```python
for unit in zealots.cploop:
    do_something()
```

减少候选对象数量，往往比微调每个对象上的分支更有价值。

### 9.5 EUDContinueIfNot 的 reset 合并机会

`EUDContinueIfNot(C)` 在 `C` 为真时进入剩余循环体，其结构与 `EUDIf(C)` 本质上是一致的。如果剩余循环体中存在任意一个无条件、必达，并且会在下一次访问 branch 前执行的触发器，可以考虑把 reset 合并进去。

### 9.6 完整示例：筛选存活的 Protoss Zealot

目标是在 `EUDLoopCUnit` 中排除死亡单位和非 Protoss Zealot，只让存活的 Protoss Zealot 进入业务处理。

#### 可读性优先的写法

```python
for cunit in EUDLoopCUnit():
    EUDContinueIf(cunit.order == EncodeUnitOrder("Die"))
    EUDContinueIfNot(
        cunit.unitType == EncodeUnit("Protoss Zealot")
    )

    process_alive_zealot(cunit)
```

这段代码表达的逻辑为：只有同时满足“未死亡”和“单位类型为 Protoss Zealot”的对象才会执行 `process_alive_zealot`。

手动优化后的结果:

`process_alive_zealot` 代表项目中的实际业务函数；`alive_zealot_count` 则用于示范一个原本就需要执行、可容纳 reset Action 的业务触发器。

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
        MemoryXEPD(CurrentPlayer, Exactly, EncodeUnitOrder("Die") << 8, 0xFF00)
    )

    # CurrentPlayer 从 order 所在 dword 移动到 unitType 所在 dword。
    DoActions(SetMemory(0x6509B0, Add, 0x64 // 4 - 0x4D // 4))

    type_branch = Forward()
    process_zealot = Forward()
    skip_non_zealot = Forward()

    type_branch << RawTrigger(
        nextptr=skip_non_zealot,
        conditions=MemoryXEPD(CurrentPlayer, Exactly, EncodeUnit("Protoss Zealot"), 0xFF),
        actions=SetNextPtr(type_branch, process_zealot),
    )

    process_zealot << NextTrigger()

    # 这是业务代码中原本就需要执行的无条件、必达 RawTrigger。
    RawTrigger(
        actions=[
            alive_zealot_count.AddNumber(1),
            SetNextPtr(type_branch, skip_non_zealot),  # reset is here
        ]
    )

    process_alive_zealot(cunit)

    skip_non_zealot << NextTrigger()
```

这段展开代码的关键点如下：

- 死亡单位由 `EUDContinueIf` 提前排除。
- `CurrentPlayer` 随后移动到 `unitType` 字段所在的 dword，使用 `0xFF` 掩码读取单位类型。
- 非 Zealot 使 `type_branch` 条件失败，沿默认 `nextptr` 直接到达 `skip_non_zealot`。
- Zealot 使 `type_branch` 条件成功，进入 `process_zealot` 对应的业务路径。
- `SetNextPtr(type_branch, skip_non_zealot)` 被合并到统计存活 Zealot 的业务触发器中，因此不再需要独立的 reset 触发器。
- reset 不必位于业务代码的第一个触发器，它可以在业务代码中的任何位置，只要它是一定到达的即可。

---

## 10. if-else 结构

### 10.1 标准写法

```python
if EUDIf()(Bring(P1, AtLeast, 1, "Terran Marine", "loc")):
    f_simpleprint("1 marine")
if EUDElse()():
    f_simpleprint("0 marine")
EUDEndIf()
```

简化后的结构为：

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

# true 分支
do_on_true()
SetNextTrigger(end)

onfalse << NextTrigger()

# false 分支
do_on_false()

end << NextTrigger()
```

`if-else` 没有改变基础 branch/reset 机制，只增加了分支结束后的汇合点：

- true 分支执行完后跳到 `end`，避免落入 false 分支；
- false 分支执行完后自然到达 `end`。

### 10.2 EUDIfNot + EUDElse 是否有意义

`EUDIfNot(C) + EUDElse` 看起来只是对调了两个对立分支，似乎没有必要，但它仍然具有实际用途。虽然交换两个分支体后可以改写为 `EUDIf(C) + EUDElse`，使二者在表达能力上等价，但选择时仍应考虑：

- 哪种写法更贴近业务语义；
- 哪个表达式更适合作为经常失败的 branch 条件；
- reset 应合并到 branch 条件为真时进入的哪一个分支。

无论使用 `EUDIf` 还是 `EUDIfNot`，`EUDBranch` 检查的仍然是传入的 `C`，其成功路径仍承担 branch/reset 成本。

### 10.3 计时器案例

原始逻辑：

```python
if EUDIf()(timer.AtLeast(1)):
    RawTrigger(actions=timer.SubtractNumber(1))
if EUDElse()():
    RawTrigger(actions=timer.SetNumber(24))
EUDEndIf()
```

假设计时器绝大多数时候非零，可以把低频成立的 `timer.Exactly(0)` 作为 branch 条件，并交换两侧业务：

```python
if EUDIf()(timer.Exactly(0)):
    RawTrigger(actions=timer.SetNumber(24))
if EUDElse()():
    RawTrigger(actions=timer.SubtractNumber(1))
EUDEndIf()
```

由于 branch 条件为真的分支包含一个无条件且必达的 `RawTrigger`，可以进一步合并 reset：

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

该结构同时完成了两项优化：

1. `timer.Exactly(0)` 在正常倒计时期间经常失败；
2. 低频成功路径将 reset 与 `timer.SetNumber(24)` 合入同一触发器。

---

## 11. else-if 链

### 11.1 生成机制

典型结构：

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

会形成按顺序检查的分支链：

```text
检查 C1
├─ 真：执行 body1，跳到 end
└─ 假：检查 C2
   ├─ 真：执行 body2，跳到 end
   └─ 假：检查 C3
      ├─ 真：执行 body3，跳到 end
      └─ 假：执行 default_body
```

每个 `EUDIf` 或 `EUDElseIf` 条件都通过 `EUDBranch` 实现。以单组条件为例：

- 当前条件为假：付出 `1C`，继续检查下一分支；
- 当前条件为真：付出该分支的 branch/reset 成本，执行对应分支体，然后跳到公共 `end`；
- 越靠后的分支，只有在前面所有条件都失败后才会被检查。

### 11.2 排序原则

`else-if` 链的优化不能只看单个条件，还要考虑到达概率：

- 把常见分支放在前面，可以减少后续条件检查；
- 把常见状态作为 `else`，可以让前置 branch 条件经常失败，但每次都要检查所有前置条件；
- 条件计算成本差异很大时，应优先执行便宜且具有高排除率的条件；

### 11.3 reset 合并

普通 `EUDElseIf()(C)` 在 `C` 为真时进入当前分支体。如果该分支体中存在任意一个无条件、必达，并且会在下一次访问 branch 前执行的 `RawTrigger`，可以像 `EUDIf(C)` 一样评估 reset 合并。

---

## 12. 常见误区

### 12.1 只根据 API 名称判断快慢

错误观点：

> `EUDIfNot` 一定比 `EUDIf` 快，或 `EUDIf` 一定比 `EUDIfNot` 快。

正确判断：

> 比较传给 `EUDBranch` 的 branch 条件在真实运行中成立与不成立的频率，以及条件本身的生成成本。

### 12.2 把“分支体高频”误认为“条件必须高频成功”

`EUDIfNot(low_frequency_condition)` 可以让分支体在大多数时候执行，同时让 branch 条件在大多数时候判断失败。业务分支频率与 branch 条件成立概率不是同一个概念。

### 12.3 把 reset 合并进带条件的触发器

目标触发器一旦条件失败，reset Action 就不会执行，下一次进入 branch 时可能沿错误路径跳转。这类错误往往具有状态相关性，很难调试。

### 12.4 忽略 Action 上限

向已有 `RawTrigger` 追加 `SetNextPtr` 会占用一个 Action 槽位。达到 64 个 Action 时，必须拆分结构，不能继续合并；不过在大多数常规分支中，Action 数量通常不会达到上限。

### 12.5 只减少运行时触发器，不考虑静态体积

把一个等值判断改成多个范围判断，可能降低某条路径的成功概率，却增加静态触发器数量、内存占用和代码复杂度。优化必须同时考虑运行时频率与生成体积。

### 12.6 在没有数据时过早手写 nextptr

高级控制结构更易读，也更不容易破坏触发器链。只有热点路径、生成结果和收益都明确时，手写展开才具有足够价值。

---

## 13. 推荐的优化流程

### 第一步：编写清晰、正确的高级结构

先使用 `EUDIf`、`EUDIfNot`、`EUDElseIf`、`EUDElse` 和提前退出接口表达业务逻辑。

### 第二步：识别热点

重点关注：

- 每帧执行的循环；
- 大量单位或对象上的筛选；
- 多层嵌套条件；

### 第三步：确定 branch 条件

分析：

- 条件成功概率；
- 严格互补条件是否存在；
- 条件数量与求值成本；
- 条件准备是否生成额外触发器。

尽量让 branch 条件在热点路径中失败。

### 第四步：优先进行结构性优化

按通常收益从高到低评估：

1. 减少遍历对象数量；
2. 合并简单条件与 Action 到一个 `RawTrigger`；
3. 使用提前退出减少不必要的后续工作；
4. 调整可安全重排的条件顺序；
5. 选择合适的 `EUDIf`/`EUDIfNot` 方向；
6. 最后才考虑手写 branch/reset。

### 第五步：检查 reset 合并条件

确认目标触发器：

- 无条件、必达；
- 有可用 Action 槽位；
- 在 branch 再次进入前完成 reset。

### 第六步：验证生成结果

至少检查：

- `branch.nextptr` 的默认目标；
- 条件成功时的临时目标；
- reset 执行位置；
- true、false 和所有提前退出路径；
- 循环下一次迭代前的指针状态；

---

## 14. 总结

`EUDIf` 的性能差异来自 `EUDBranch` 对触发器链的动态修改：

- branch 条件不成立时，沿默认 `nextptr` 前进；
- branch 条件成立时，需要修改 branch、执行 reset，再进入目标路径。

由此可以得到一套统一的优化方法：

1. 简单条件动作尽量合并为一个 `RawTrigger`；
2. 让 branch 条件尽可能经常失败；
3. 根据业务逻辑选择 `EUDIf`、`EUDIfNot` 或提前跳转接口；
4. 优先减少遍历量和无效工作；
5. 只在安全条件明确、热点收益可信时合并或迁移 reset。

---

## 15. Armoha's Comments

### Bring/Command Performance

I think Bring isn't appropriate example here because it is most offending factor for performance that calculates not only single owner and unit type but counts whole 12players×(all+completed)×(228units+3groups) = 5544 entries if the location isn't cached one.

If Bring/Command is in consideration, collecting same location checks to prevent recounting could be major part.

### Profiling During Development

You can always write test eps file and import eudplib functions in eps to measure with epTrace. When debug: 1, 1 tracing trigger is added on each lines of eps, so it's actually better write function to be profiled in eudplib rather than eps for accurate comparison.

```eps
EUDLoopN()(99999);
const thisDoesntAddAnyTrigger = Db(1);
const butItAddsFalsePositiveResult = EUDArray(1);
const soIfYouWantToMeasureFunction = function () {};
const youWouldRatherWriteEudplib = StringBuffer(1);
importedEudplibFunc();
EUDEndLoopN();
```

no one prevents you to measure the performance of each functions during the development. it doesn't include any eps code in output map. it's a test during the development. (making current project code to library and testing it on child test edd).

you can use EP_SetDebugMode (can't remember correct name) and EUDTracedFunc too

### Constant-Condition Optimization

eps automatically collapses constant conditions into single trigger but eudplib currently lacks such optimization and you need to do it by yourself for now

### Multiple Conditions and Short-Circuiting

I think it should mention combinations of multiple conditions and eager/short-circuiting (plain list, EUDAnd, EUDOr, EUDSCAnd, EUDSCOr). It is easy to putting them into single list but it eagerly evaluates every conditions and then check the final result, no short-circuiting.

```text
if (const && const && var && const)
-> EUDIf()(EUDSCAnd()([const, const]([var, const])())
```

It's related to not just performance but side-effect of conditional expressions (e.g. eudfunc calls)

it is python dsl limitation so we need to make every conditions into lambda (very ugly) or compile bytecodes of decorated func to triggers (add stage)

### Condition Ordering in Hot Loops

if there're multiple conditions on hot loop, the order of conditions matter too. it's better to cull as many unit as possible in earlier condition.

e.g. to check unit type, owner, and aliveness, owner in whole unit loop for territory guard unit (usually around 60):

owner and aliveness (order != 0) use same epd address

assuming 1200 units and 60 territory guard unit and 150 units owned by target player: checks unit type -> owner -> aliveness (cull 1640 units in unit type check -> check owner and order id of remaining unit)

- loop invariant code motion xP

### Switch-Based Dispatch

EUD/EPDSwitch could be worth mentioned

```text
n EUDIf/ElseIf checks : generates 2n triggers, runs 1T 1C in best case (first EUDIfNot) or runs 2nT nC 2nA in worst case
switch with n cases : generates and run log2(n) triggers (8 cases = 3 triggers) for converting variable to jump table address offset
computed goto to jump table would be fastest but hard to write xP
or don't write triggers for every cases. generalize it in single expression if possible
```
