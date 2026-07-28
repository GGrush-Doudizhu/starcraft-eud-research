# EUD 变量池

> eudplib 源码：[armoha/eudplib](https://github.com/armoha/eudplib)  
> 致谢：感谢 Armoha 长期致力于维护 eudplib，并感谢他对我的帮助；同时感谢 eudplib 原作者 trgk。

`eud_vars.py` 提供一个在地图编译期间工作的 EUD 变量池。它允许多个运行时
生命周期完全不重叠的逻辑复用同一个 `EUDVariable` 对象，从而复用同一块
72 字节变量载荷空间。

该模块不分析触发器控制流，也不会判断变量是否真的已经停止使用。申请和释放只是
编译期 Python 操作，生命周期正确性必须由调用者保证。

## 目录

1. [导入](#导入)
2. [两种申请方式](#两种申请方式)
3. [`get()`：申请未知值变量](#get申请未知值变量)
4. [`init()`：申请已知初值变量](#init申请已知初值变量)
5. [初值只接受 `int`](#初值只接受int)
6. [`free()`：释放变量](#free释放变量)
7. [同时释放多个变量](#同时释放多个变量)
8. [已知值复用过程](#已知值复用过程)
9. [丢弃重置 Action 是严重错误](#丢弃重置action是严重错误)
10. [条件重置可能不安全](#条件重置可能不安全)
11. [变量可以永远不释放](#变量可以永远不释放)
12. [重复释放](#重复释放)
13. [运行时生命周期要求](#运行时生命周期要求)
14. [lvalue 标记](#lvalue标记)
15. [VTable 状态](#vtable状态)
16. [创建独立变量池](#创建独立变量池)
17. [查看统计信息](#查看统计信息)
18. [分配策略摘要](#分配策略摘要)

## 导入

一般代码使用共享实例 `eud_vars`：

```python
from eud_vars import eud_vars
```

公开接口由四个名称组成：

```python
from eud_vars import PoolVar, VarPool, VarStats, eud_vars
```

- `PoolVar`：由变量池创建的 `EUDVariable` 子类。
- `VarPool`：变量池。
- `VarStats`：不可变统计快照。
- `eud_vars`：工程共享变量池。

`PoolVar` 是真正的 `EUDVariable` 子类，不是代理包装：

```python
from eudplib import EUDVariable

value = eud_vars.get()
assert isinstance(value, EUDVariable)
```

因此它可以直接传给接受 `EUDVariable` 的 eudplib API。

## 两种申请方式

变量池有两种含义不同的申请接口：

| 接口 | 返回值是否已知 | 调用者是否需要初始化 |
|---|---|---|
| `get()` | 未知 | 需要 |
| `init(value)` | 已知为 `value` | 不需要 |

看到申请代码时，可以直接根据方法名判断是否需要手动初始化。

## `get()`：申请未知值变量

申请一个变量：

```python
a = eud_vars.get()
```

`get()` 严格按照以下优先级申请：

1. 上一次通过无参数 `free()` 释放的未知值变量。
2. 上一次通过 `free(n)` 释放的任意已知值变量。
3. 只有前两类都不存在时，才创建新的 `EUDVariable(0)`。

未知值变量没有可供 `init(n)` 利用的值匹配信息，因此最先消耗。已知值变量仍然
可能被将来的 `init(n)` 精确匹配，所以只有未知值空闲池为空时才会被 `get()`
取走。即使 `get()` 取得的是已知值变量，调用者也不能依赖该旧值，仍然必须在
第一次读取前手动初始化。

```python
a = eud_vars.get()

RawTrigger(
    actions=[
        a.SetNumber(0),
        # 其他业务 Action。
    ],
)
```

初始化应尽量合并进已有的触发器。仅仅为了初始化一个可复用变量而额外注册一个
`RawTrigger`是不值得的。

### 一次申请多个未知值变量

向 `get()` 传入正整数数量：

```python
a, b = eud_vars.get(2)
```

结果是包含指定数量不同变量的元组：

```python
variables = eud_vars.get(3)
assert isinstance(variables, tuple)
assert len(variables) == 3
```

调用形式决定返回类型：

```python
single = eud_vars.get()       # PoolVar
one_tuple = eud_vars.get(1)   # tuple[PoolVar]
many = eud_vars.get(3)        # tuple[PoolVar, PoolVar, PoolVar]
```

数量必须是正整数。`bool` 虽然是 Python 的 `int` 子类，但不会被接受：

```python
eud_vars.get(0)       # ValueError
eud_vars.get(-1)      # ValueError
eud_vars.get(True)    # TypeError
```

## `init()`：申请已知初值变量

没有可用于合并初始化 Action 的触发器时，可以申请具有已知初值的变量：

```python
counter = eud_vars.init(3)
```

变量池按以下顺序处理：

1. 查找已经通过 `free(3)` 释放的空闲变量。
2. 找到时直接复用该变量。
3. 找不到时创建 `EUDVariable(3)`。

新创建变量的初值直接写入变量载荷，不需要额外的初始化触发器。

### 一次申请多个已知初值变量

每个参数对应一个返回变量：

```python
zero, three, ten = eud_vars.init(0, 3, 10)
```

等价于分别申请：

```python
zero = eud_vars.init(0)
three = eud_vars.init(3)
ten = eud_vars.init(10)
```

单个参数返回一个 `PoolVar`，多个参数返回元组：

```python
single = eud_vars.init(0)
many = eud_vars.init(0, 3)
```

## 初值只接受`int`

`init()` 和带参数的 `free()` 只接受类型严格为 `int` 的值：

```python
value = eud_vars.init(3)
```

以下参数不会被接受：

```python
from eudplib import Forward

eud_vars.init(True)       # TypeError
eud_vars.init(Forward())  # TypeError
```

固定整数地址的 `EPD()` 结果本身是 `int`，因此可以使用：

```python
a = eud_vars.init(EPD(0x6509B0))
```

所有整数都会规范化为无符号 DWORD：

```python
negative = eud_vars.init(-1)
unsigned = 0xFFFFFFFF
```

`-1` 和 `0xFFFFFFFF` 对应相同的 DWORD 值，因此可以使用同一类已知值空闲
变量。

## `free()`：释放变量

`free()` 是 `PoolVar` 自身的方法，可以直接调用方法：

```python
a = eud_vars.get()

# use `a` to do something

a.free()
```

释放后，原来的 Python 名称仍然指向同一个对象，但该对象已经可能被变量池发给
其他调用者。释放后继续通过旧名称使用变量是错误行为。

### 未知值释放

不传参数时：

```python
result = value.free()
assert result is None
```

该变量进入未知值空闲池。以后 `get()` 可以复用它，但 `init(n)` 不会复用它，
因为它是未知值，调用者使用 `get()` 复用需手动初始化数值。

### 重置并释放

传入整数时：

```python
reset_action = value.free(3)
```

该调用会：

1. 返回 `value.SetNumber(3)`。
2. 在编译期将变量标记为空闲。
3. 记录该变量在下一生命周期前应当被重置为 `3`。

推荐直接将返回的 Action 放进已有业务触发器：

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

之后申请相同初值时可以复用：

```python
next_value = eud_vars.init(3)
```

如果没有其他申请抢先取走该空闲变量，`next_value` 就可能与 `value` 是同一个
对象和同一个 EUD 内存槽位。

## 同时释放多个变量

未知值释放可以逐个调用，也可以组成列表：

```python
a, b = eud_vars.get(2)

# 使用 a 和 b。

# 结束使用a
DoActions(a.free())

# 结束使用b
DoActions(b.free())

# 或者一起释放
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
        # 其他业务 Action。
        *reset_actions,
    ],
)
```

## 已知值复用过程

下面示例展示完整过程：

```python
old_value = eud_vars.get()

RawTrigger(
    actions=[
        old_value.SetNumber(100),
        # 使用 old_value 的其他 Action。
    ],
)

RawTrigger(
    actions=[
        # 当前生命周期的结束业务。
        old_value.free(7),
    ],
)

new_value = eud_vars.init(7)

# new_value 不需要额外初始化。
```

它仅在运行时满足以下顺序时才正确：

```text
旧生命周期最后一次使用
        ↓
执行 old_value.SetNumber(7)
        ↓
新生命周期第一次读取
```

Python 源码中的先后顺序不能代替运行时顺序证明。

## 丢弃重置Action是严重错误

下面代码在编译期会把变量登记为“已重置为 3”，但实际没有任何触发器执行返回的
Action：

```python
value.free(3)  # 错误：返回的 Action 被丢弃。

other = eud_vars.init(3)
```

带参数调用 `free()` 时，必须使用返回的Action。

## 条件重置可能不安全

下面的重置只在条件成立时执行：

```python
reset_action = value.free(0)

RawTrigger(
    conditions=some_condition,
    actions=reset_action,
)
```

只有当所有能够到达新生命周期的运行时路径都必定满足该条件时，后续
`eud_vars.init(0)` 才是安全的。否则某些路径可能绕过重置。

同样需要检查：

- 重置触发器是否可能晚于新生命周期执行。
- 某个分支是否仍然读取旧变量。
- 某个循环是否仍然持有变量地址。
- 周期触发器是否会在释放后继续执行。
- 变量地址是否已经保存进长期存在的对象。

## 变量可以永远不释放

变量池不会要求每个申请都必须释放：

```python
permanent = eud_vars.init(100)
```

如果 `permanent` 在整个游戏运行期间都可能被使用，就应当一直保持活动状态。
不调用 `free()` 是允许的行为。

模块没有 `check()` 方法，因为“尚未释放”不代表变量泄漏。变量池仍然会跟踪活动
状态，以防止同一个尚未释放的变量被再次分配，并正确处理重复释放。

## 重复释放

重复释放是允许的：

```python
value = eud_vars.get()
value.free()
value.free()
```

变量只会在空闲池中登记一次，不会因为重复释放而被同时申请给多个调用者。

重复带相同值释放时，每次调用都会返回一个重置 Action，变量仍然保留该已知值：

```python
first_branch_action = value.free(3)
second_branch_action = value.free(3)

next_value = eud_vars.init(3)
```

这允许用户把两个 Action 分别放进不同的运行时分支。调用者仍须保证所有能够到达
新生命周期的路径都执行过相应的重置。

如果重复释放提供了不同值，池无法根据 Python 源码顺序推断运行时最后执行的是
哪一个分支，因此会保守地将变量降级为未知值：

```python
value.free(3)
value.free(7)

next_value = eud_vars.init(3)  # 不会把 value 当成已知值 3或7。
```

无参数和带参数释放混合时同样降级为未知值：

```python
value.free(3)
value.free()
```

一旦变量已经是未知值，后续重复调用 `free(n)` 不能重新把它升级为已知值。

### 重新申请后的旧别名

变量被重新申请后，旧的 Python 名称仍然能够访问同一个对象：

```python
old_name = eud_vars.get()
old_name.free()

new_name = eud_vars.get()
assert old_name is new_name
```

此时通过 `old_name.free()` 不属于重复释放，而是会释放 `new_name` 当前正在使用
的新生命周期。变量池无法区分同一对象的两个 Python 别名，因此调用者必须停止
使用已经释放的旧名称。

同理，在生成多个分支的释放代码期间，不应在两次释放调用之间重新申请可能取得
该变量的对象：

```python
old_name.free()
new_name = eud_vars.get()  # 可能取得 old_name 对应的同一个对象。
old_name.free()            # 会释放 new_name 的新生命周期。
```

## 运行时生命周期要求

释放变量前必须确认：

1. 旧变量不会作为返回值离开当前区域。
2. 旧变量没有保存在长期对象、数组或结构体中。
3. 所有运行时分支都永久停止访问它。
4. 所有包含它的运行时循环都已经退出。
5. 周期触发器不会继续读取或修改它。
6. 新生命周期不会早于释放或重置逻辑执行。
7. `free(value)` 返回的 Action 会在所有必要路径执行。

变量池只相信调用者提供的生命周期证明，不会自动验证这些条件。

## lvalue标记

eudplib 的 lvalue/rvalue 标记是编译期 Python 状态，不是游戏运行时变量值。

表达式产生的临时 `EUDVariable` 可以被标记为 rvalue。eudplib 在确认该对象只是
一次性表达式结果时，可能直接覆盖并复用它，从而减少额外临时变量。变量池返回的
对象却代表一个新的、稳定的业务生命周期，不能继续被当成可牺牲的表达式临时值。

因此每次通过 `get()` 或 `init()` 激活变量时，变量池都会调用：

```python
variable.makeL()
```

这只会清除对象的编译期 rvalue 标记：

- 不生成触发器或 Action。
- 不修改游戏运行时数值。
- 不清理 VTable。
- 不增加载荷空间。

在 `free()` 时恢复 lvalue 没有必要，因为释放后调用者不应再使用该生命周期；
只要在下一次申请时恢复即可。

## VTable状态

重新申请时调用的 `makeL()` 不会在运行时重置完整 VTable 状态，例如：

- destination
- modifier
- mask
- next pointer

最适合复用的是通过以下操作直接读写数值的变量：

- `SetNumber`
- `AddNumber`
- `SubtractNumber`
- 数值比较和条件

如果变量参与了以下操作，需要额外证明残留 VTable 状态不会影响新生命周期：

- `SetDest`
- `QueueAssignTo`
- `QueueAddTo`
- `QueueSubtractTo`
- `GetVTable`
- 复杂 `VProc` 链

带参数的 `free(value)` 只重置变量数值，不重置上述其他状态。

## 创建独立变量池

独立子系统可以拥有自己的池：

```python
from src.game.eud_vars import VarPool

first_pool = VarPool()
second_pool = VarPool()

first = first_pool.get()
second = second_pool.init(3)
```

不同池之间不会复用变量。每个 `PoolVar` 会记住自己的所属池，因此：

```python
first.free()
second.free(3)
```

会自动归还到正确的池。

## 查看统计信息

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

字段含义：

| 字段 | 含义 |
|---|---|
| `total` | 当前池创建过的变量总数 |
| `active` | 已申请且尚未释放的变量数 |
| `available` | 所有空闲变量数 |
| `unknown` | 数值未知的空闲变量数 |
| `initialized` | 带已知重置值的空闲变量数 |
| `acquisitions` | 历史申请次数 |
| `reuses` | 历史复用次数 |
| `peak_active` | 历史最大同时活动变量数 |

`stats` 只是诊断和观察工具，不要求 `active` 最终变成零。

## 分配策略摘要

`get()` 的严格选择顺序：

1. 由无参数 `free()` 产生的数值未知空闲变量。
2. 由 `free(n)` 产生的任意已知值空闲变量。
3. 前两类都不存在时，新建 `EUDVariable(0)`。

优先消耗未知值变量，可以尽量为未来的 `init(value)` 保留精确匹配机会。
只要任意一种空闲变量仍然存在，`get()` 就不会创建新变量。

`init(value)` 的选择顺序：

1. 通过 `free(value)` 释放的精确匹配变量。
2. 新建 `EUDVariable(value)`。

`init(value)` 不会使用数值未知的空闲变量，因为在没有额外运行时 Action 的情况
下，无法保证未知变量具有指定初值。
