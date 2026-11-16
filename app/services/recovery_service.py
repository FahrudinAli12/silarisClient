from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.paket_receiving_operation import PaketReceivingOperation, TERMINAL_PHASES

logger = logging.getLogger("recovery_service")

# Transisi resmi startup recovery Tahap 3.2
STARTUP_RECOVERY_TRANSITIONS: dict[str, str] = {
    "claiming": "claim_held_reconciliation",
    "claim_uncertain": "claim_held_reconciliation",
    "claim_verified": "claim_held_reconciliation",
    "local_committed": "awaiting_reauth",
}

# Terminal & protected states yang tidak boleh diubah saat recovery
PROTECTED_RECOVERY_STATES: set[str] = set(TERMINAL_PHASES) | {
    "awaiting_reauth",
    "claim_held_reconciliation",
}


def compute_startup_recovery_state(initial_state: str) -> str:
    """Fungsi deterministik untuk transisi status recovery saat backend restart."""
    if initial_state in STARTUP_RECOVERY_TRANSITIONS:
        return STARTUP_RECOVERY_TRANSITIONS[initial_state]
    if initial_state in PROTECTED_RECOVERY_STATES:
        return initial_state
    return initial_state


def execute_startup_recovery(db_session: Session) -> dict[str, Any]:
    """
    Eksekusi pemulihan status operasi penerimaan paket saat backend restart.
    - Menjamin tidak ada INSERT baru.
    - Menjamin operation_id tidak berubah.
    - Menjamin status terminal dan terproteksi (conflict, auth_blocked_user) tidak tertimpa.
    - Mengembalikan rekapitulasi jumlah operasi yang ditransisikan.
    """
    recap: dict[str, int] = {
        "transitioned_to_held": 0,
        "transitioned_to_awaiting_reauth": 0,
        "protected_unchanged": 0,
        "total_evaluated": 0,
    }

    try:
        operations = db_session.query(PaketReceivingOperation).all()
        recap["total_evaluated"] = len(operations)

        for op in operations:
            current_phase = str(op.lifecycle_phase).strip()

            if current_phase in PROTECTED_RECOVERY_STATES:
                recap["protected_unchanged"] += 1
                continue

            target_phase = STARTUP_RECOVERY_TRANSITIONS.get(current_phase)
            if target_phase:
                logger.info(
                    "Startup Recovery: Transisi operation %s dari '%s' ke '%s'",
                    op.operation_id, current_phase, target_phase
                )
                op.lifecycle_phase = target_phase
                if target_phase == "claim_held_reconciliation":
                    op.sync_state = "claim_held_reconciliation"
                    recap["transitioned_to_held"] += 1
                elif target_phase == "awaiting_reauth":
                    op.sync_state = "awaiting_reauth"
                    recap["transitioned_to_awaiting_reauth"] += 1

        db_session.commit()
    except Exception as exc:
        db_session.rollback()
        logger.error("Gagal menjalankan startup recovery: %s", exc)
        raise

    return recap

