from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


# =============================================================================
# DEFINISI TERPUSAT LIFECYCLE PHASE PENERIMAAN PAKET
# =============================================================================
ACTIVE_PHASES: frozenset[str] = frozenset({"claiming", "claim_verified", "local_committed"})
RECONCILIATION_PHASES: frozenset[str] = frozenset({"claim_uncertain", "claim_held_reconciliation", "awaiting_reauth"})
TERMINAL_PHASES: frozenset[str] = frozenset({"confirmed", "claim_rejected", "conflict", "reconciled_orphan", "auth_blocked_user"})

# Seluruh fase yang masih aktif atau membutuhkan rekonsiliasi (NON-TERMINAL).
# Paket dengan status dalam himpunan ini TIDAK BOLEH diproses oleh operation_id baru.
ACTIVE_OR_HELD_PHASES: frozenset[str] = ACTIVE_PHASES | RECONCILIATION_PHASES

ALL_LIFECYCLE_PHASES: frozenset[str] = ACTIVE_OR_HELD_PHASES | TERMINAL_PHASES


class PaketReceivingOperation(Base):
    """Tabel tracking idempotensi, lifecycle, dan pemulihan operasi penerimaan paket linen."""

    __tablename__ = "paket_receiving_operation"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    paket_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    hospital_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    installation_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_code: Mapped[str] = mapped_column(String(50), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_count: Mapped[int] = mapped_column(Integer, default=0)
    lifecycle_phase: Mapped[str] = mapped_column(String(30), nullable=False, default="claiming")
    sync_state: Mapped[str] = mapped_column(String(30), nullable=False, default="pending_claim")
    transaksi_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index(
            "uq_active_paket_operation",
            "paket_id",
            unique=True,
            sqlite_where=text(
                "lifecycle_phase IN ('claiming', 'claim_verified', 'local_committed', "
                "'claim_uncertain', 'claim_held_reconciliation', 'awaiting_reauth')"
            ),
        ),
    )

