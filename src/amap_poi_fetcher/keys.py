"""多 key 池：轮换、失效标记、全灭检测。

每个 key 三态：
- ACTIVE   可用
- COOLDOWN 配额耗尽（DAILY_QUERY_OVER_LIMIT），次日恢复，本次运行不再使用
- DEAD     无效 key（INVALID_USER_KEY 等），永久弃用

全部不可用抛 AllKeysExhausted，由调度层落盘进度后优雅退出。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class KeyState(str, Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    DEAD = "dead"


class AllKeysExhausted(RuntimeError):
    """所有 key 均不可用（配额耗尽或失效）。"""


@dataclass
class KeyPool:
    """按序轮换的 key 池。线程不安全（本工具单线程调度，无需锁）。"""

    keys: list[str]
    index: int = 0
    states: dict[str, KeyState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("key 列表为空")
        for k in self.keys:
            self.states.setdefault(k, KeyState.ACTIVE)

    @staticmethod
    def mask(key: str) -> str:
        """日志用脱敏：只露前 5 位。"""
        return key[:5] + "..." if len(key) > 5 else "***"

    def active_keys(self) -> list[str]:
        return [k for k in self.keys if self.states[k] == KeyState.ACTIVE]

    def current(self) -> str:
        """返回当前可用 key；当前位不可用时自动向后寻找。全部不可用抛 AllKeysExhausted。"""
        for _ in range(len(self.keys)):
            key = self.keys[self.index % len(self.keys)]
            if self.states[key] == KeyState.ACTIVE:
                return key
            self.index += 1
        raise AllKeysExhausted(
            f"所有 key 均不可用（共 {len(self.keys)} 个："
            f"{sum(1 for s in self.states.values() if s == KeyState.COOLDOWN)} 个配额耗尽，"
            f"{sum(1 for s in self.states.values() if s == KeyState.DEAD)} 个失效）"
        )

    def rotate(self) -> str:
        """主动切换到下一个可用 key。"""
        self.index += 1
        return self.current()

    def mark_dead(self, key: str) -> None:
        self.states[key] = KeyState.DEAD

    def mark_cooldown(self, key: str) -> None:
        self.states[key] = KeyState.COOLDOWN

    # ---- 状态持久化（断点续跑） ----
    def to_dict(self) -> dict:
        return {"index": self.index, "states": {k: s.value for k, s in self.states.items()}}

    def restore(self, data: dict) -> None:
        """从 state.json 恢复 key 状态（keys 列表以本次配置为准）。"""
        self.index = int(data.get("index", 0))
        for k, v in (data.get("states") or {}).items():
            if k in self.states:
                self.states[k] = KeyState(v)
