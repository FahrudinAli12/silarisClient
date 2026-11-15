from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import re
from typing import Any, Callable, Optional, Protocol, Sequence
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.detail_transaksi_linen import DetailTransaksiLinen
from app.models.linen import Linen
from app.models.paket_receiving_operation import (
    ACTIVE_OR_HELD_PHASES,
    PaketReceivingOperation,
)
from app.models.riwayat_linen import RiwayatLinen
from app.models.transaksi_linen import TransaksiLinen
from app.services.circuit_breaker import OperationCircuitBreaker
from app.services.claim_validator_service import evaluate_claim_response
from app.services.hospital_binding_service import verify_hospital_binding
from app.services.installation_service import (
    InstallationIdentityMismatchError,
    InstallationUIDProvider,
    PersistentFileInstallationUIDProvider,
)
from app.services.manifest_service import compute_canonical_manifest_hash, validate_scan_completeness
from app.services.rfid_service import RFIDScanSession, RFIDServiceManager
from app.services.session_store import SessionData, SessionStore

logger = logging.getLogger("package_receiving_service")


# =============================================================================
# PROTOKOL & EXCEPTION DEFINITIONS
# =============================================================================

class CloudClaimConfirmAdapter(Protocol):
    """Protokol komunikasi RPC Claim, Confirm, dan Status ke cloud backend."""

    async def claim_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def confirm_package(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    async def get_package_claim_status(
        self, token: str, paket_id: int, hospital_id: Optional[int] = None
    ) -> dict[str, Any]:
        ...


class PackageReceivingError(Exception):
    """Base exception untuk koordinator penerimaan paket."""
    pass


class AuthenticationError(PackageReceivingError):
    pass


class HospitalMismatchError(PackageReceivingError):
    pass


class ManifestCompletenessError(PackageReceivingError):
    pass


class ActivePackageOperationConflictError(PackageReceivingError):
    pass


class CommitAuthorityError(PackageReceivingError):
    pass


class ConfirmPermitError(PackageReceivingError):
    pass


class StateTransitionError(PackageReceivingError):
    pass


class CircuitBreakerActiveError(PackageReceivingError):
    pass


class RoomValidationError(PackageReceivingError):
    """Exception saat validasi master data ruangan atau ruangan tujuan gagal."""
    pass


# =============================================================================
# PACKAGE RECEIVING COORDINATOR SERVICE
# =============================================================================

class PackageReceivingService:
    """
    Koordinator Alur Penerimaan Paket Linen Rumah Sakit (Phase 4B.2 / R2.9).
    Mengintegrasikan 19 langkah siklus hidup dengan atomisitas SQLite,
    komunikasi Supabase RPC non-blocking, serta mekanisme pemulihan crash recovery (F6).
    """

    def __init__(
        self,
        session_store: SessionStore,
        rfid_service_manager: RFIDServiceManager,
        cloud_adapter: CloudClaimConfirmAdapter,
        circuit_breaker: Optional[OperationCircuitBreaker] = None,
        installation_uid_provider: Optional[InstallationUIDProvider] = None,
        db_session_factory: Callable[[], Session] = SessionLocal,
        time_provider: Callable[[], float] = lambda: datetime.now().timestamp(),
    ) -> None:
        self.session_store = session_store
        self.rfid_service_manager = rfid_service_manager
        self.cloud_adapter = cloud_adapter
        self.circuit_breaker = circuit_breaker or OperationCircuitBreaker()
        self.installation_uid_provider = installation_uid_provider or PersistentFileInstallationUIDProvider()
        self.db_session_factory = db_session_factory
        self.time_provider = time_provider

    async def reconcile_held_operation(
        self,
        operation_id: str,
        session_id: str,
        token: str,
        hospital_id: int,
    ) -> dict[str, Any]:
        """
        Rekonsiliasi crash recovery untuk operasi yang tertahan dalam status
        claim_uncertain atau claim_held_reconciliation (F6).
        """
        inst_uid = self.installation_uid_provider.get_installation_uid()

        with self.db_session_factory() as db:
            op_rec = db.query(PaketReceivingOperation).filter_by(operation_id=operation_id).first()
            if not op_rec:
                return {"status": "not_found", "operation_id": operation_id, "detail": "Operasi tidak ditemukan."}

            if int(op_rec.hospital_id) != int(hospital_id):
                raise HospitalMismatchError(
                    f"Operasi {operation_id} terikat pada RS {op_rec.hospital_id}, bukan RS {hospital_id}."
                )

            if op_rec.lifecycle_phase in ("confirmed", "claim_rejected", "conflict", "reconciled_orphan", "auth_blocked_user"):
                return {
                    "status": "terminal",
                    "lifecycle_phase": op_rec.lifecycle_phase,
                    "operation_id": operation_id,
                    "paket_id": op_rec.paket_id,
                    "transaksi_id": op_rec.transaksi_id,
                }

            target_pkg_id = op_rec.paket_id
            verif_code = op_rec.verification_code
            expected_hash = op_rec.manifest_hash

        # Panggil status RPC cloud
        try:
            status_res = await self.cloud_adapter.get_package_claim_status(token, target_pkg_id, hospital_id)
        except Exception as exc:
            logger.warning("Gagal memanggil status RPC saat rekonsiliasi: %s", exc)
            return {
                "status": "held_reconciliation",
                "lifecycle_phase": "claim_held_reconciliation",
                "operation_id": operation_id,
                "paket_id": target_pkg_id,
                "error": str(exc),
            }

        pkg_status = status_res.get("package_status") or status_res.get("status_paket")

        if pkg_status == "pending":
            # Cloud masih pending, klaim belum masuk -> retry Claim dengan operation_id yang sama
            retry_payload = {
                "paket_id": int(target_pkg_id),
                "hospital_id": int(hospital_id),
                "operation_id": operation_id,
                "installation_uid": inst_uid,
                "verification_code": str(verif_code),
                "expected_manifest_hash": expected_hash,
            }
            try:
                claim_res = await self.cloud_adapter.claim_package(token, retry_payload)
                outcome = evaluate_claim_response(
                    claim_res,
                    expected_paket_id=int(target_pkg_id),
                    expected_op_id=operation_id,
                    expected_inst_uid=inst_uid,
                    expected_hash=expected_hash,
                )
                if outcome == "verified":
                    with self.db_session_factory() as db:
                        row = db.query(PaketReceivingOperation).filter_by(operation_id=operation_id).first()
                        if row:
                            row.lifecycle_phase = "claim_verified"
                            row.sync_state = "pending_claim"
                            row.failure_reason = None
                            db.commit()
                    return {"status": "reconciled", "lifecycle_phase": "claim_verified", "operation_id": operation_id}
                else:
                    with self.db_session_factory() as db:
                        row = db.query(PaketReceivingOperation).filter_by(operation_id=operation_id).first()
                        if row:
                            row.lifecycle_phase = "claim_rejected" if outcome == "conflict" else "claim_uncertain"
                            row.sync_state = "claim_held_reconciliation"
                            db.commit()
                    return {"status": outcome, "lifecycle_phase": row.lifecycle_phase, "operation_id": operation_id}
            except Exception as exc:
                return {"status": "retry_failed", "lifecycle_phase": "claim_uncertain", "error": str(exc)}

        elif pkg_status == "claimed":
            cloud_pkg_id = status_res.get("package_id") or status_res.get("paket_id")
            cloud_hosp_id = status_res.get("hospital_id")
            cloud_op_id = status_res.get("claimed_by_operation_id")
            cloud_inst_uid = status_res.get("claimed_by_installation_uid")
            cloud_hash = status_res.get("manifest_hash")

            matches_claimed_identity = (
                cloud_pkg_id is not None and int(cloud_pkg_id) == int(target_pkg_id)
                and cloud_hosp_id is not None and int(cloud_hosp_id) == int(hospital_id)
                and bool(cloud_op_id) and str(cloud_op_id).strip() == operation_id
                and bool(cloud_inst_uid) and str(cloud_inst_uid).strip() == inst_uid
                and bool(cloud_hash) and str(cloud_hash).strip().lower() == str(expected_hash).strip().lower()
            )

            if matches_claimed_identity:
                # Terbukti diklaim oleh operasi ini
                with self.db_session_factory() as db:
                    row = db.query(PaketReceivingOperation).filter_by(operation_id=operation_id).first()
                    if row:
                        row.lifecycle_phase = "claim_verified"
                        row.sync_state = "pending_claim"
                        row.failure_reason = None
                        db.commit()
                return {"status": "reconciled", "lifecycle_phase": "claim_verified", "operation_id": operation_id}
            else:
                # Diklaim oleh operasi lain atau parameter identitas tidak cocok (R2.10.1 P0)
                failure_msg = "CONFLICT: Identitas klaim cloud (package_id, hospital_id, claimed_by_operation_id, claimed_by_installation_uid, manifest_hash) tidak cocok dengan operasi lokal."
                with self.db_session_factory() as db:
                    row = db.query(PaketReceivingOperation).filter_by(operation_id=operation_id).first()
                    if row:
                        row.lifecycle_phase = "claim_rejected"
                        row.sync_state = "claim_held_reconciliation"
                        row.failure_reason = failure_msg
                        db.commit()
                return {"status": "conflict", "lifecycle_phase": "claim_rejected", "operation_id": operation_id, "detail": failure_msg}

        elif pkg_status in ("confirmed", "diterima", "received"):
            cloud_pkg_id = status_res.get("package_id") or status_res.get("paket_id")
            cloud_hosp_id = status_res.get("hospital_id")
            cloud_conf_op_id = status_res.get("confirmed_by_operation_id")
            cloud_conf_inst_uid = status_res.get("confirmed_by_installation_uid")
            cloud_hash = status_res.get("manifest_hash")

            matches_identity = (
                cloud_pkg_id is not None and int(cloud_pkg_id) == int(target_pkg_id)
                and cloud_hosp_id is not None and int(cloud_hosp_id) == int(hospital_id)
                and bool(cloud_conf_op_id) and str(cloud_conf_op_id).strip() == operation_id
                and bool(cloud_conf_inst_uid) and str(cloud_conf_inst_uid).strip() == inst_uid
                and bool(cloud_hash) and str(cloud_hash).strip().lower() == str(expected_hash).strip().lower()
            )

            with self.db_session_factory() as db:
                row = db.query(PaketReceivingOperation).filter_by(operation_id=operation_id).first()
                if not row:
                    return {"status": "not_found", "operation_id": operation_id}

                if not matches_identity:
                    # Identitas tidak cocok: pertahankan status held/conflict demi mencegah adopsi paket ilegal (R2.10 P0)
                    row.lifecycle_phase = "conflict"
                    row.sync_state = "claim_held_reconciliation"
                    failure_msg = "CONFLICT: Identitas konfirmasi cloud (package_id, hospital_id, confirmed_by_operation_id, confirmed_by_installation_uid, manifest_hash) tidak cocok dengan operasi lokal."
                    row.failure_reason = failure_msg
                    db.commit()
                    return {"status": "conflict", "lifecycle_phase": "conflict", "operation_id": operation_id, "detail": failure_msg}

                if row.transaksi_id:
                    # R2.10.1 P0: Verifikasi bahwa transaksi lokal benar-benar ada dan terikat pada paket ini!
                    local_trx = db.query(TransaksiLinen).filter_by(id=row.transaksi_id).first()
                    if not local_trx:
                        row.lifecycle_phase = "held_reconciliation"
                        row.sync_state = "claim_held_reconciliation"
                        failure_msg = f"INTEGRITY_ERROR: Transaksi lokal ID {row.transaksi_id} tidak ditemukan di database."
                        row.failure_reason = failure_msg
                        db.commit()
                        return {"status": "held_reconciliation", "lifecycle_phase": "held_reconciliation", "operation_id": operation_id, "detail": failure_msg}

                    # R2.10.2 P1: Parsing exact nilai numerik ID paket dari catatan transaksi (mencegah false-positive substring seperti paket 12 vs 123)
                    catatan_trx = local_trx.catatan or ""
                    match_pkg = re.search(r"\bPaket\s*:\s*(\d+)\b", catatan_trx, re.IGNORECASE)
                    found_pkg_id = int(match_pkg.group(1)) if match_pkg else None

                    if found_pkg_id is None or found_pkg_id != int(target_pkg_id):
                        row.lifecycle_phase = "conflict"
                        row.sync_state = "claim_held_reconciliation"
                        failure_msg = f"CONFLICT: Transaksi lokal ID {row.transaksi_id} tidak terikat valid pada paket {target_pkg_id} (ditemukan paket ID {found_pkg_id} dari catatan: '{catatan_trx}')."
                        row.failure_reason = failure_msg
                        db.commit()
                        return {"status": "conflict", "lifecycle_phase": "conflict", "operation_id": operation_id, "detail": failure_msg}

                    # Sudah ada transaksi lokal yang terbukti sah dan identitas cloud valid, tandai confirmed
                    row.lifecycle_phase = "confirmed"
                    row.sync_state = "synced"
                    row.failure_reason = None
                    db.commit()
                    return {
                        "status": "reconciled",
                        "lifecycle_phase": "confirmed",
                        "operation_id": operation_id,
                        "transaksi_id": row.transaksi_id,
                    }
                else:
                    # Cloud confirmed tapi lokal belum ada transaksi: tandai orphan tanpa duplikasi transaksi
                    row.lifecycle_phase = "reconciled_orphan"
                    row.sync_state = "claim_held_reconciliation"
                    row.failure_reason = "RECONCILED_ORPHAN: Cloud berstatus confirmed tanpa transaksi lokal tercatat."
                    db.commit()
                    return {"status": "orphan", "lifecycle_phase": "reconciled_orphan", "operation_id": operation_id}

        return {"status": "unresolved", "lifecycle_phase": "claim_held_reconciliation", "operation_id": operation_id}

    async def process_package_receiving(
        self,
        session_id: str,
        paket_id: int | str,
        verification_code: str,
        manifest_items: Sequence[dict[str, Any]],
        scan_session: RFIDScanSession,
        operation_id: Optional[str] = None,
        ruangan_tujuan: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Mengeksekusi 19 langkah siklus hidup penerimaan paket linen secara atomik dan aman.
        """
        # 1. Validasi sesi aktif
        session: Optional[SessionData] = await self.session_store.get_session(session_id)
        if not session or session.is_revoked:
            raise AuthenticationError("Sesi operator tidak valid atau telah kedaluwarsa.")

        hospital_id = int(session.hospital_id)

        # 2. Server-side hospital binding verification
        with self.db_session_factory() as db:
            try:
                verify_hospital_binding(db, hospital_id)
            except Exception as exc:
                raise HospitalMismatchError(f"Hospital binding tidak valid: {exc}")

            from app.models.ruangan import Ruangan
            if ruangan_tujuan:
                ruangan_tujuan = ruangan_tujuan.strip()
                room_obj = db.query(Ruangan).filter(Ruangan.nama_ruangan == ruangan_tujuan).first()
                if not room_obj:
                    raise RoomValidationError(
                        f"Ruangan tujuan '{ruangan_tujuan}' tidak terdaftar pada master data ruangan rumah sakit."
                    )
                if hasattr(room_obj, "is_active") and not room_obj.is_active:
                    raise RoomValidationError(
                        f"Ruangan tujuan '{ruangan_tujuan}' berstatus nonaktif dan tidak dapat digunakan untuk penerimaan paket."
                    )
            else:
                first_room = db.query(Ruangan).filter(getattr(Ruangan, "is_active", True) == True).order_by(Ruangan.id.asc()).first()
                if not first_room:
                    raise RoomValidationError(
                        "Master data ruangan kosong atau tidak ada ruangan aktif. Harap daftarkan minimal 1 ruangan aktif sebelum menerima paket."
                    )
                ruangan_tujuan = first_room.nama_ruangan

        # 3. Validasi paket dan manifest cloud
        manifest_hash = compute_canonical_manifest_hash(manifest_items)

        # 4. Validasi bukti scan RFID fisik
        if scan_session.is_invalidated:
            raise RuntimeError(f"Scan session is invalidated: {scan_session.invalidation_reason or 'hardware_disconnect'}")

        # 5. Bekukan snapshot EPC
        snapshot_epcs = scan_session.freeze_snapshot()
        scan_session.validate_for_commit()
        completeness = validate_scan_completeness(manifest_items, snapshot_epcs)
        if not completeness["is_complete_100"]:
            raise ManifestCompletenessError(
                f"Hasil scan fisik tidak 100% cocok dengan manifest. Missing: {completeness['missing_epcs']}, Unexpected: {completeness['unexpected_epcs']}"
            )

        # 6. Tetapkan operation_id
        effective_op_id = (operation_id or f"op_{uuid.uuid4().hex}").strip()
        scan_session.bind_to_operation(effective_op_id)
        scan_session.bind_to_package(paket_id)
        self.rfid_service_manager.register_scan_session(scan_session)

        inst_uid = self.installation_uid_provider.get_installation_uid()

        # 7 & 8. Simpan intent dengan status claiming & COMMIT intent lokal sebelum RPC
        with self.db_session_factory() as db:
            db.execute(text("PRAGMA busy_timeout = 5000;"))
            db.execute(text("BEGIN IMMEDIATE;"))

            # Cegah transaksi paralel ganda atau pemrosesan ulang paket yang sudah selesai
            active_conflict = (
                db.query(PaketReceivingOperation)
                .filter(
                    PaketReceivingOperation.paket_id == int(paket_id),
                    PaketReceivingOperation.operation_id != effective_op_id,
                    PaketReceivingOperation.lifecycle_phase.in_(ACTIVE_OR_HELD_PHASES | {"confirmed"}),
                )
                .first()
            )
            if active_conflict:
                db.rollback()
                raise ActivePackageOperationConflictError(
                    f"Paket {paket_id} sedang diproses oleh operasi aktif lain: "
                    f"{active_conflict.operation_id} (phase: {active_conflict.lifecycle_phase})"
                )

            op_rec = (
                db.query(PaketReceivingOperation)
                .filter(PaketReceivingOperation.operation_id == effective_op_id)
                .first()
            )

            # F10: Operasi terminal tidak boleh diproses ulang dengan operation_id lama
            if op_rec and op_rec.lifecycle_phase in ("claim_rejected", "conflict", "reconciled_orphan", "auth_blocked_user"):
                db.rollback()
                raise StateTransitionError(
                    f"Operasi {effective_op_id} berada dalam status terminal '{op_rec.lifecycle_phase}', "
                    "tidak dapat diproses ulang dengan operation_id lama."
                )

            # Proteksi integritas identitas node
            if op_rec and op_rec.installation_uid != inst_uid:
                db.rollback()
                raise InstallationIdentityMismatchError(
                    f"Operasi {effective_op_id} terikat pada installation_uid '{op_rec.installation_uid}', "
                    f"tidak cocok dengan identitas node saat ini '{inst_uid}'. Proses dihentikan demi integritas data."
                )

            # Proteksi paket
            if op_rec and int(op_rec.paket_id) != int(paket_id):
                db.rollback()
                raise ActivePackageOperationConflictError(
                    f"Operasi {effective_op_id} terikat pada paket {op_rec.paket_id}, "
                    f"tidak dapat digunakan untuk paket {paket_id}."
                )

            # Proteksi rumah sakit
            if op_rec and int(op_rec.hospital_id) != int(hospital_id):
                db.rollback()
                raise HospitalMismatchError(
                    f"Operasi {effective_op_id} terikat pada RS {op_rec.hospital_id}, "
                    f"tidak dapat digunakan lintas rumah sakit ({hospital_id})."
                )

            # Idempotensi: jika sudah pernah confirmed, langsung return
            if op_rec and op_rec.lifecycle_phase == "confirmed":
                db.commit()
                return {
                    "status": "ok",
                    "lifecycle_phase": "confirmed",
                    "sync_state": "synced",
                    "operation_id": effective_op_id,
                    "paket_id": paket_id,
                    "transaksi_id": op_rec.transaksi_id,
                    "idempotent": True,
                }

            # F6: Penanganan operasi tertahan rekonsiliasi pasca crash startup recovery
            if op_rec and op_rec.lifecycle_phase == "claim_held_reconciliation":
                db.commit()
                return {
                    "status": "held_reconciliation",
                    "lifecycle_phase": "claim_held_reconciliation",
                    "sync_state": "claim_held_reconciliation",
                    "operation_id": effective_op_id,
                    "paket_id": paket_id,
                    "message": "Operasi tertahan dalam status claim_held_reconciliation pasca pemulihan crash. Perlu rekonsiliasi eksplisit.",
                }
            else:
                if not op_rec:
                    op_rec = PaketReceivingOperation(
                        operation_id=effective_op_id,
                        paket_id=int(paket_id),
                        hospital_id=hospital_id,
                        installation_uid=inst_uid,
                        verification_code=str(verification_code),
                        manifest_hash=manifest_hash,
                        scan_count=len(snapshot_epcs),
                        lifecycle_phase="claiming",
                        sync_state="pending_claim",
                    )
                    db.add(op_rec)

                db.commit()
                current_lifecycle_phase = op_rec.lifecycle_phase

        # 9. Kirim Claim melalui cloud RPC (tanpa menahan lock memori atau transaksi DB)
        # F2 Fix: Sertakan hospital_id di payload claim
        claim_payload = {
            "paket_id": int(paket_id),
            "hospital_id": int(hospital_id),
            "operation_id": effective_op_id,
            "installation_uid": inst_uid,
            "verification_code": str(verification_code),
            "expected_manifest_hash": manifest_hash,
        }

        # Jika sudah berstatus claim_verified, local_committed, atau awaiting_reauth, lewati Claim RPC
        skip_claim = current_lifecycle_phase in ("claim_verified", "local_committed", "awaiting_reauth")

        if not skip_claim:
            try:
                claim_res = await self.cloud_adapter.claim_package(session.access_token, claim_payload)
            except (TimeoutError, asyncio.TimeoutError, Exception) as exc:
                # 10. Timeout menghasilkan claim_uncertain
                logger.warning("RPC Claim mengalami gangguan / timeout: %s", exc)
                with self.db_session_factory() as db:
                    row = db.query(PaketReceivingOperation).filter_by(operation_id=effective_op_id).first()
                    if row:
                        row.lifecycle_phase = "claim_uncertain"
                        row.sync_state = "claim_held_reconciliation"
                        row.failure_reason = f"CLAIM_TIMEOUT: {exc}"
                        db.commit()
                return {
                    "status": "uncertain",
                    "lifecycle_phase": "claim_uncertain",
                    "sync_state": "claim_held_reconciliation",
                    "operation_id": effective_op_id,
                    "paket_id": paket_id,
                    "error": str(exc),
                }

            # 11. Respons Claim yang sah menghasilkan claim_verified
            try:
                outcome = evaluate_claim_response(
                    claim_res,
                    expected_paket_id=int(paket_id),
                    expected_op_id=effective_op_id,
                    expected_inst_uid=inst_uid,
                    expected_hash=manifest_hash,
                )
            except Exception as exc:
                logger.warning("Respons Claim malformed / invalid: %s", exc)
                with self.db_session_factory() as db:
                    row = db.query(PaketReceivingOperation).filter_by(operation_id=effective_op_id).first()
                    if row:
                        row.lifecycle_phase = "claim_uncertain"
                        row.sync_state = "claim_held_reconciliation"
                        row.failure_reason = f"CLAIM_MALFORMED: {exc}"
                        db.commit()
                return {
                    "status": "uncertain",
                    "lifecycle_phase": "claim_uncertain",
                    "sync_state": "claim_held_reconciliation",
                    "operation_id": effective_op_id,
                    "detail": str(exc),
                }

            if outcome != "verified":
                with self.db_session_factory() as db:
                    row = db.query(PaketReceivingOperation).filter_by(operation_id=effective_op_id).first()
                    if row:
                        row.lifecycle_phase = "claim_rejected" if outcome == "conflict" else "claim_uncertain"
                        row.sync_state = "claim_held_reconciliation"
                        row.failure_reason = f"CLAIM_{outcome.upper()}"
                        db.commit()
                return {
                    "status": "rejected" if outcome == "conflict" else "mismatch",
                    "lifecycle_phase": "claim_rejected" if outcome == "conflict" else "claim_uncertain",
                    "outcome": outcome,
                    "operation_id": effective_op_id,
                }

            with self.db_session_factory() as db:
                row = db.query(PaketReceivingOperation).filter_by(operation_id=effective_op_id).first()
                if row:
                    row.lifecycle_phase = "claim_verified"
                    row.sync_state = "pending_claim"
                    row.failure_reason = None
                    db.commit()

        # 12. Ambil commit authority
        auth_session = await self.session_store.acquire_commit_authority(session_id, effective_op_id)
        if not auth_session:
            raise CommitAuthorityError("Gagal memperoleh hak commit eksklusif: sesi telah dicabut atau operasi lain sedang commit.")

        # 13. Jalankan seluruh penulisan bisnis dan local_committed dalam satu transaksi SQLite
        if self.circuit_breaker.is_locked(effective_op_id) or self.circuit_breaker.is_package_locked(int(paket_id)):
            await self.session_store.release_commit_authority(auth_session, effective_op_id)
            raise CircuitBreakerActiveError("Circuit breaker disk full aktif. Transaksi commit diblokir.")

        trx_id: Optional[int] = None
        try:
            with self.db_session_factory() as db:
                db.execute(text("PRAGMA busy_timeout = 5000;"))
                db.execute(text("BEGIN IMMEDIATE;"))

                upd_res = db.execute(
                    text(
                        "UPDATE paket_receiving_operation "
                        "SET lifecycle_phase = 'local_committed', sync_state = 'pending_confirm' "
                        "WHERE operation_id = :op_id AND ("
                        "    lifecycle_phase = 'claim_verified' "
                        "    OR lifecycle_phase = 'local_committed' "
                        "    OR lifecycle_phase = 'awaiting_reauth'"
                        ")"
                    ),
                    {"op_id": effective_op_id},
                )

                if upd_res.rowcount != 1:
                    db.rollback()
                    raise StateTransitionError(
                        f"Transisi local_committed gagal: rowcount={upd_res.rowcount}. "
                        "Operasi bukan dalam state yang sah (claim_verified / local_committed / awaiting_reauth)."
                    )

                # Cek apakah transaksi bisnis sudah pernah dibuat sebelumnya (idempotensi)
                existing_op = db.query(PaketReceivingOperation).filter_by(operation_id=effective_op_id).first()
                if existing_op and existing_op.transaksi_id:
                    trx_id = existing_op.transaksi_id
                else:
                    manifest_dict = {item.get("epc"): item for item in manifest_items if item.get("epc")}
                    linen_objs = []
                    for epc in snapshot_epcs:
                        m_item = manifest_dict.get(epc, {})
                        kat = m_item.get("kategori") or "Linen"
                        nama_lin = m_item.get("nama_linen") or "Linen Masuk"

                        linen_obj = db.query(Linen).filter_by(epc=epc).first()
                        if linen_obj:
                            linen_obj.status = "tersedia"
                            linen_obj.lokasi = ruangan_tujuan
                            linen_obj.total_cuci = (linen_obj.total_cuci or 0) + 1
                        else:
                            linen_obj = Linen(
                                epc=epc,
                                kategori=kat,
                                nama_linen=nama_lin,
                                lokasi=ruangan_tujuan,
                                status="tersedia",
                                total_cuci=0,
                                created_at=datetime.utcnow(),
                            )
                            db.add(linen_obj)
                        linen_objs.append(linen_obj)

                    db.flush()

                    kode_trx = f"TRX-RCV-{effective_op_id.replace('-', '')[:30]}"
                    transaksi = TransaksiLinen(
                        jenis_transaksi="penerimaan",
                        kode_transaksi=kode_trx,
                        petugas=session.email,
                        ruangan=ruangan_tujuan,
                        total_linen=len(snapshot_epcs),
                        status="selesai",
                        catatan=f"Penerimaan dari cloud | Paket: {paket_id}",
                        reader_source=scan_session.reader_name or "Reader-COM",
                        port_source=scan_session.port or "COM",
                        created_at=datetime.utcnow(),
                    )
                    db.add(transaksi)
                    db.flush()
                    trx_id = int(transaksi.id)

                    for l in linen_objs:
                        # 1. Detail Transaksi
                        db.add(
                            DetailTransaksiLinen(
                                transaksi_id=trx_id,
                                linen_id=l.id,
                                epc=l.epc,
                                status_sebelum="baru",
                                status_setelah="tersedia",
                                waktu_scan=datetime.utcnow(),
                                keterangan=f"Diterima dari cloud | Kode: {verification_code}",
                            )
                        )

                        # 2. Riwayat Linen
                        db.add(
                            RiwayatLinen(
                                linen_id=l.id,
                                transaksi_id=trx_id,
                                event_type="penerimaan",
                                lokasi_sebelumnya="Cloud Supplier",
                                lokasi_sesudah=ruangan_tujuan,
                                created_at=datetime.utcnow(),
                            )
                        )

                    if existing_op:
                        existing_op.transaksi_id = trx_id

                db.commit()
        finally:
            await self.session_store.release_commit_authority(auth_session, effective_op_id)

        # 14. Ambil Confirm dispatch permit (Lokal SessionStore)
        ok_perm, token_perm, permit_id, perm_err = await self.session_store.acquire_confirm_dispatch_permit(
            session_id, effective_op_id, int(paket_id), hospital_id
        )
        if not ok_perm or not permit_id:
            raise ConfirmPermitError(f"Gagal memperoleh Confirm dispatch permit: {perm_err}")

        # 15. Konsumsi permit secara atomik sebelum dispatch
        consumed_ok, consumed_token, consume_err = await self.session_store.consume_confirm_dispatch_permit(
            session_id, permit_id
        )
        if not consumed_ok or not consumed_token:
            raise ConfirmPermitError(f"Dispatch permit ditolak saat konsumsi: {consume_err}")

        # 16. Kirim Confirm melalui cloud RPC (F3 Fix: 6 parameter eksak, tanpa p_permit_id)
        confirm_payload = {
            "paket_id": int(paket_id),
            "hospital_id": int(hospital_id),
            "operation_id": effective_op_id,
            "installation_uid": inst_uid,
            "verification_code": str(verification_code),
            "manifest_hash": manifest_hash,
        }

        try:
            confirm_res = await self.cloud_adapter.confirm_package(consumed_token, confirm_payload)
        except Exception as exc:
            logger.warning("Confirm RPC mengalami error jaringan: %s", exc)
            return {
                "status": "confirm_pending",
                "lifecycle_phase": "local_committed",
                "sync_state": "pending_confirm",
                "operation_id": effective_op_id,
                "paket_id": paket_id,
                "transaksi_id": trx_id,
                "detail": f"Confirm RPC error jaringan: {exc}. Status lokal dipertahankan local_committed.",
            }

        # 17. Validasi identitas dan respons Confirm (P1 Strict Confirm Response - R2.10)
        # Confirm response wajib mempunyai 6 field: paket_id, hospital_id, operation_id, installation_uid, manifest_hash, status
        # Seluruh field harus ada dan bernilai valid (tidak boleh NULL, hilang, atau mismatch)
        res_paket_id = confirm_res.get("paket_id") if isinstance(confirm_res, dict) else None
        if res_paket_id is None and isinstance(confirm_res, dict):
            res_paket_id = confirm_res.get("package_id")
        res_hospital_id = confirm_res.get("hospital_id") if isinstance(confirm_res, dict) else None
        res_operation_id = confirm_res.get("operation_id") if isinstance(confirm_res, dict) else None
        res_installation_uid = confirm_res.get("installation_uid") if isinstance(confirm_res, dict) else None
        res_manifest_hash = confirm_res.get("manifest_hash") if isinstance(confirm_res, dict) else None
        res_status = (confirm_res.get("status") or confirm_res.get("status_paket")) if isinstance(confirm_res, dict) else None

        is_confirm_valid = (
            isinstance(confirm_res, dict)
            and res_paket_id is not None and int(res_paket_id) == int(paket_id)
            and res_hospital_id is not None and int(res_hospital_id) == int(hospital_id)
            and bool(res_operation_id) and str(res_operation_id).strip() == effective_op_id
            and bool(res_installation_uid) and str(res_installation_uid).strip() == inst_uid
            and bool(res_manifest_hash) and str(res_manifest_hash).strip().lower() == manifest_hash.lower()
            and bool(res_status) and (str(res_status).lower() in ("confirmed", "diterima", "ok") or confirm_res.get("status_paket") == "confirmed")
        )

        if not is_confirm_valid:
            logger.warning("Respons Confirm cloud tidak lengkap atau tidak valid: %s", confirm_res)
            return {
                "status": "confirm_unverified",
                "lifecycle_phase": "local_committed",
                "sync_state": "pending_confirm",
                "operation_id": effective_op_id,
                "paket_id": paket_id,
                "transaksi_id": trx_id,
                "detail": "Respons Confirm cloud tidak lengkap (wajib: paket_id, hospital_id!=NULL, operation_id, installation_uid, manifest_hash!=NULL, status). State lokal tetap local_committed.",
            }

        # 18. Simpan status confirmed
        with self.db_session_factory() as db:
            row = db.query(PaketReceivingOperation).filter_by(operation_id=effective_op_id).first()
            if row:
                row.lifecycle_phase = "confirmed"
                row.sync_state = "synced"
                row.failure_reason = None
                db.commit()

        # 19. Lepaskan seluruh hak dan resource sesuai lifecycle
        await self.session_store.release_confirm_dispatch_permit(auth_session, permit_id)
        self.rfid_service_manager.unregister_scan_session(scan_session)

        return {
            "status": "ok",
            "lifecycle_phase": "confirmed",
            "sync_state": "synced",
            "operation_id": effective_op_id,
            "paket_id": paket_id,
            "transaksi_id": trx_id,
        }
