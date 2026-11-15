from __future__ import annotations

import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("circuit_breaker")


class OperationCircuitBreaker:
    """In-memory Circuit Breaker pengaman transaksi terhadap kegagalan kritis disk/storage."""

    def __init__(self) -> None:
        self._locked_operations: dict[str, dict[str, Any]] = {}
        self._locked_packages: dict[int, str] = {}  # paket_id -> operation_id
        self._lock = threading.Lock()

    def record_failure(
        self,
        operation_id: str,
        error: Exception,
        paket_id: Optional[int] = None,
        reason: Optional[str] = None
    ) -> None:
        """Kunci operation_id dan paket_id saat terjadi kegagalan fatal storage."""
        with self._lock:
            op_id = str(operation_id).strip()
            err_msg = str(error).lower()
            lock_reason = reason or ("disk_full_safety_lock" if "full" in err_msg else "critical_storage_error")

            self._locked_operations[op_id] = {
                "error": type(error).__name__,
                "detail": str(error),
                "reason": lock_reason,
                "paket_id": paket_id,
            }
            if paket_id is not None:
                self._locked_packages[int(paket_id)] = op_id

            logger.error("Circuit breaker ACTIVATED for operation %s (reason: %s): %s", op_id, lock_reason, error)

    def is_locked(self, operation_id: str) -> bool:
        """Cek apakah operation_id terkunci."""
        with self._lock:
            return str(operation_id).strip() in self._locked_operations

    def is_package_locked(self, paket_id: int) -> bool:
        """Cek apakah paket_id terkunci."""
        with self._lock:
            return int(paket_id) in self._locked_packages

    def get_lock_reason(self, operation_id: str) -> Optional[str]:
        """Ambil alasan penguncian operation_id."""
        with self._lock:
            info = self._locked_operations.get(str(operation_id).strip())
            return info.get("reason") if info else None

    def release_lock(self, operation_id: str) -> bool:
        """Pelepasan terkendali setelah intervensi operator / recovery."""
        with self._lock:
            op_id = str(operation_id).strip()
            info = self._locked_operations.pop(op_id, None)
            if info:
                pkg_id = info.get("paket_id")
                if pkg_id is not None and self._locked_packages.get(pkg_id) == op_id:
                    self._locked_packages.pop(pkg_id, None)
                logger.info("Circuit breaker RELEASED for operation %s", op_id)
                return True
            return False

    def clear(self) -> None:
        """Hapus seluruh kunci (hanya untuk keperluan pengujian / restart)."""
        with self._lock:
            self._locked_operations.clear()
            self._locked_packages.clear()

