from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Optional, Protocol
import uuid

logger = logging.getLogger("installation_service")

DEFAULT_INSTALLATION_FILE = Path(__file__).resolve().parent.parent.parent / "installation_identity.json"

ENV_UID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]{8,64}$")


class InstallationIdentityCorruptError(RuntimeError):
    """Exception ketika berkas installation identity corrupt, kosong, atau tidak valid."""
    pass


class InstallationIdentityMismatchError(RuntimeError):
    """Exception ketika environment variable berbeda dengan identitas berkas persisten otoritatif
    atau identitas node tidak cocok dengan operasi lama yang masih aktif/membutuhkan rekonsiliasi."""
    pass


@contextlib.contextmanager
def _file_lock(lock_path: Path):
    """Context manager untuk cross-process lock yang aman di Windows dan POSIX."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


class InstallationUIDProvider(Protocol):
    """Protokol penyedia identitas unik instalasi mesin lokal."""

    def get_installation_uid(self) -> str:
        ...


class PersistentFileInstallationUIDProvider:
    """
    Penyedia Installation UID persisten dengan kebijakan 'Seed Once, File Authoritative':
    1. Jika berkas installation_identity.json tersedia dan valid, UID berkas menjadi sumber otoritatif tunggal.
    2. Environment variable (INSTALLATION_UID) hanya berlaku sebagai seed saat berkas belum ada.
    3. Jika environment diset dan berbeda dengan berkas persisten, startup dihentikan dengan InstallationIdentityMismatchError.
    4. Berkas persisten tidak pernah diubah atau dirotasi otomatis.
    5. Jika berkas fisik korup atau kosong, startup dihentikan dengan InstallationIdentityCorruptError meskipun environment ada.
    6. Jika berkas belum ada dan environment kosong, generate UUID baru satu kali dan simpan secara atomik.
    7. Menggunakan cross-process file lock dan atomic write (tempfile + os.replace).
    8. Identitas stabil setelah restart, logout operator, dan transaksi baru.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self.file_path = file_path or DEFAULT_INSTALLATION_FILE
        self._cached_uid: Optional[str] = None

    def _read_from_file(self) -> Optional[str]:
        """Baca UID dari berkas fisik. Lempar InstallationIdentityCorruptError jika rusak/kosong."""
        if not self.file_path.exists():
            return None

        # Periksa apakah berkas kosong (0 byte)
        if self.file_path.stat().st_size == 0:
            raise InstallationIdentityCorruptError(
                f"Berkas identitas instalasi kosong (0 byte): {self.file_path}. "
                "Pembuatan UID baru secara diam-diam ditolak demi integritas instalasi."
            )

        try:
            content = self.file_path.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as exc:
            raise InstallationIdentityCorruptError(
                f"Berkas identitas instalasi corrupt/tidak dapat diuraikan: {self.file_path}. "
                f"Penyebab: {exc}. Dilarang membuat UID baru secara diam-diam."
            ) from exc

        if not isinstance(data, dict):
            raise InstallationIdentityCorruptError(
                f"Struktur data berkas identitas instalasi bukan JSON object: {self.file_path}"
            )

        uid = data.get("installation_uid")
        if not uid or not isinstance(uid, str) or not uid.strip():
            raise InstallationIdentityCorruptError(
                f"Field 'installation_uid' tidak ditemukan atau kosong dalam berkas: {self.file_path}"
            )

        return uid.strip()

    def get_installation_uid(self) -> str:
        if self._cached_uid:
            return self._cached_uid

        # 1. ATURAN FILE AUTHORITATIVE: Cek berkas fisik terlebih dahulu
        if self.file_path.exists():
            # Jika file ada, BACA SECARA KETAT. Jika korup -> raise InstallationIdentityCorruptError seketika
            file_uid = self._read_from_file()

            # Validasi environment variable terhadap file otoritatif jika diset
            raw_env = os.getenv("INSTALLATION_UID")
            if raw_env is not None:
                env_uid = raw_env.strip()
                if env_uid:
                    if not ENV_UID_PATTERN.match(env_uid):
                        raise ValueError(
                            f"Format INSTALLATION_UID pada environment tidak valid: {raw_env!r}. "
                            "Harus berupa 8-64 karakter alfanumerik, garis bawah, titik, atau strip."
                        )
                    if env_uid != file_uid:
                        raise InstallationIdentityMismatchError(
                            f"Inkonsistensi identitas instalasi: Environment variable INSTALLATION_UID='{env_uid}' "
                            f"tidak cocok dengan berkas persisten otoritatif='{file_uid}' pada {self.file_path}. "
                            "Startup dihentikan demi menjaga integritas data dan rekonsiliasi operasi."
                        )

            self._cached_uid = file_uid
            return file_uid

        # 2. FIRST INITIALIZATION: Berkas persisten belum ada
        lock_file = self.file_path.with_suffix(".lock")
        with _file_lock(lock_file):
            # Double check setelah memperoleh file lock
            if self.file_path.exists():
                file_uid = self._read_from_file()
                raw_env = os.getenv("INSTALLATION_UID")
                if raw_env is not None:
                    env_uid = raw_env.strip()
                    if env_uid:
                        if not ENV_UID_PATTERN.match(env_uid):
                            raise ValueError(
                                f"Format INSTALLATION_UID pada environment tidak valid: {raw_env!r}."
                            )
                        if env_uid != file_uid:
                            raise InstallationIdentityMismatchError(
                                f"Inkonsistensi identitas instalasi: Environment variable INSTALLATION_UID='{env_uid}' "
                                f"tidak cocok dengan berkas persisten otoritatif='{file_uid}'."
                            )
                self._cached_uid = file_uid
                return file_uid

            # Tentukan seed UID: Environment variable digunakan sebagai seed sekali jalan jika tersedia
            seed_uid: Optional[str] = None
            raw_env = os.getenv("INSTALLATION_UID")
            if raw_env is not None:
                env_uid = raw_env.strip()
                if env_uid:
                    if not ENV_UID_PATTERN.match(env_uid):
                        raise ValueError(
                            f"Format INSTALLATION_UID pada environment tidak valid: {raw_env!r}. "
                            "Harus berupa 8-64 karakter alfanumerik, garis bawah, titik, atau strip."
                        )
                    seed_uid = env_uid

            # Jika environment tidak tersedia, generate UUID baru
            if not seed_uid:
                seed_uid = f"inst_{uuid.uuid4().hex}"

            payload = {
                "installation_uid": seed_uid,
                "created_at": os.getenv("CLIENTS_TEST_TIMESTAMP", ""),
            }

            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: tulis ke berkas sementara di direktori yang sama, lalu rename secara atomik
            temp_name = f"{self.file_path.name}.tmp_{uuid.uuid4().hex}"
            temp_path = self.file_path.with_name(temp_name)
            try:
                temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                os.replace(temp_path, self.file_path)
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

            self._cached_uid = seed_uid
            return seed_uid


def validate_installation_uid_against_db(db_session: Any, current_uid: str) -> None:
    """
    Memeriksa apakah terdapat operasi penerimaan paket lama di database yang masih aktif
    atau membutuhkan rekonsiliasi (ACTIVE_OR_HELD_PHASES).
    
    Status yang dilindungi:
    - claiming, claim_verified, local_committed (ACTIVE_PHASES)
    - claim_uncertain, claim_held_reconciliation, awaiting_reauth (RECONCILIATION_PHASES)
    
    Jika ditemukan operasi dengan installation_uid yang berbeda dengan current_uid,
    maka startup/eksekusi dihentikan dengan InstallationIdentityMismatchError
    agar operasi lama tidak menjadi yatim atau rusak integritasnya.
    """
    from app.models.paket_receiving_operation import PaketReceivingOperation, ACTIVE_OR_HELD_PHASES

    mismatched_op = (
        db_session.query(PaketReceivingOperation)
        .filter(
            PaketReceivingOperation.lifecycle_phase.in_(ACTIVE_OR_HELD_PHASES),
            PaketReceivingOperation.installation_uid != current_uid,
        )
        .first()
    )
    if mismatched_op:
        raise InstallationIdentityMismatchError(
            f"Inkonsistensi Identitas Node: Database lokal memiliki operasi aktif/tertahan "
            f"(ID: '{mismatched_op.operation_id}', Paket ID: {mismatched_op.paket_id}, "
            f"Phase: '{mismatched_op.lifecycle_phase}') yang terikat pada installation_uid '{mismatched_op.installation_uid}', "
            f"tetapi identitas node saat ini adalah '{current_uid}'. "
            "Startup dihentikan demi melindungi operasi lama dari kerusakan identitas."
        )
