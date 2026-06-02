"""熔断器：三状态自动机，防止外部服务持续失败时拖垮整个系统。

状态转换：
  CLOSED → (连续失败 N 次) → OPEN → (超时后自动) → HALF_OPEN → (成功 1 次) → CLOSED
                                                              ↘ (失败) → OPEN

用法：
  breaker = CircuitBreaker("llm", failure_threshold=3, recovery_timeout=30)
  breaker.call(my_func)          # sync
  await breaker.call_async(...)  # async
  # 或手动管理：
  breaker.check()        # 熔断时抛异常
  breaker.succeed()      # 成功后调用
  breaker.fail()         # 失败后调用
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Callable

from src.logging_config import get_logger

logger = get_logger("circuit_breaker")


class CircuitState(Enum):
    """熔断器状态：closed=正常, open=熔断, half_open=试探恢复。"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerError(Exception):
    """熔断器打开时抛出的异常，表示请求被快速拒绝。"""
    pass


class CircuitBreaker:
    """熔断器：保护外部服务调用不被级联失败拖垮。

    Args:
        name: 熔断器名称（日志标识）
        failure_threshold: 连续失败次数达到此值后熔断
        recovery_timeout: 熔断后等待多少秒自动进入半开状态
        success_threshold: 半开状态下连续成功次数达到此值后恢复
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        """获取当前状态。OPEN 且超时时自动转为 HALF_OPEN。"""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    logger.info(f"Circuit '{self.name}' transitioning OPEN → HALF_OPEN")
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
            return self._state

    def check(self) -> None:
        """检查熔断器状态，非 CLOSED 时抛 CircuitBreakerError 快速失败。"""
        if self.state != CircuitState.CLOSED:
            logger.warning(f"Circuit '{self.name}' {self.state.value}, fast-failing")
            raise CircuitBreakerError(f"circuit '{self.name}' is {self.state.value}")

    def succeed(self) -> None:
        """报告一次调用成功。半开状态下连续成功达阈值后恢复为 CLOSED。"""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    logger.info(f"Circuit '{self.name}' transitioning HALF_OPEN → CLOSED ({self._success_count}/{self.success_threshold} successes)")
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            else:
                self._failure_count = 0

    def fail(self) -> None:
        """报告一次调用失败。达到阈值后触发熔断（OPEN）。"""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                logger.warning(f"Circuit '{self.name}' HALF_OPEN test failed ({self._failure_count}/{self.failure_threshold}), back to OPEN")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                logger.warning(f"Circuit '{self.name}' OPEN after {self._failure_count} consecutive failures")
                self._state = CircuitState.OPEN

    def call(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """同步执行 func，熔断时快速失败。成功自动 succed，失败自动 fail。"""
        self.check()
        try:
            result = func(*args, **kwargs)
            self.succeed()
            return result
        except CircuitBreakerError:
            raise
        except Exception:
            self.fail()
            raise

    async def call_async(self, func: Callable, *args: Any, **kwargs: Any) -> Any:
        """异步执行 func，熔断时快速失败。成功自动 succed，失败自动 fail。"""
        self.check()
        try:
            result = await func(*args, **kwargs)
            self.succeed()
            return result
        except CircuitBreakerError:
            raise
        except Exception:
            self.fail()
            raise
