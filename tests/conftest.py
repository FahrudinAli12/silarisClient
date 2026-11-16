from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import sqlite3
import sys
import tempfile
from typing import Any, Optional
import urllib.parse

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# 1. INDEPENDENT PATH DEFINITION & ISOLATION GUARDS BEFORE ANY APP IMPORT
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_blocked_paths = {
    (PROJECT_ROOT / "Client_RumahSakit_Template.db").resolve(),
    (PROJECT_ROOT / "Client_RumahSakit_Production.db").resolve(),
}

# Proses pytest utama SELALU membuat direktori sementara baru yang terkelola
# dan TIDAK PERNAH mempercayai DATABASE_URL warisan dari lingkungan.
_test_tmp_dir = tempfile.TemporaryDirectory(prefix="clients_pytest_")
_managed_temp_dir_path = Path(_test_tmp_dir.name).resolve()
_test_db_path = (_managed_temp_dir_path / "clients_isolated_test.db").resolve()

# Set allowlist direktori sementara yang diizinkan untuk seluruh koneksi SQLite berbasis file
_allowed_temp_dirs: set[Path] = {_managed_temp_dir_path}

# Tetapkan DATABASE_URL ke database sementara ini
os.environ["DATABASE_URL"] = f"sqlite:///{_test_db_path.as_posix()}"
os.environ["CLIENTS_TEST_MANAGED_DIR"] = str(_managed_temp_dir_path)

os.environ["HOSPITAL_CLOUD_ID"] = "101"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:8000,http://127.0.0.1:8000,http://testserver"
os.environ["DISABLE_BACKGROUND_SERVICES_FOR_TESTS"] = "1"


def _is_path_in_allowed_dirs(target_path: Path) -> bool:
    try:
        resolved = target_path.resolve()
    except Exception:
        return False

    for allowed in _allowed_temp_dirs:
        try:
            allowed_res = allowed.resolve()
            if resolved == allowed_res or resolved.is_relative_to(allowed_res):
                return True
        except (ValueError, AttributeError):
            pass
        # Fallback perbandingan case-insensitive untuk filesystem Windows
        try:
            res_str = os.path.normcase(os.path.abspath(str(resolved)))
            allow_str = os.path.normcase(os.path.abspath(str(allowed)))
            if res_str == allow_str or res_str.startswith(allow_str + os.sep) or res_str.startswith(allow_str + "/"):
                return True
        except Exception:
            pass
    return False


def _is_in_memory_sqlite(database: Any, uri: bool = False) -> bool:
    """Mengecek apakah target database benar-benar merupakan database in-memory murni.

    Aturan ketat:
    1. Tanpa .strip(): nama harus persis seperti yang diteruskan ke SQLite.
       Nama dengan spasi seperti ' :memory: ' BUKAN in-memory dan ditolak dari pengecualian ini.
    2. Database bernama kosong '' BUKAN database memori tanpa akses disk (SQLite membuat berkas
       sementara pada disk). Ditolak dari pengecualian memori.
    3. Jika bukan URI (uri=False), HANYA persis ':memory:' (str atau bytes) yang diizinkan.
    4. Jika URI (uri=True), hanya format resmi SQLite yang semantiknya secara tegas menjamin
       database memori murni tanpa path file yang diizinkan:
       - 'file::memory:'
       - 'file::memory:?cache=shared' atau 'file::memory:?cache=private'
       Bentuk ambigu (nama file dengan ?mode=memory, path kosong 'file:?mode=memory',
       spasi ekstra, query parameter bertentangan/duplikat, atau parameter tak dikenal)
       DITOLAK dari pengecualian memori.
    """
    if database is None:
        return False

    # 1. Plain ':memory:' tanpa spasi tambahan
    if database == ":memory:" or database == b":memory:":
        return True

    if isinstance(database, bytes):
        try:
            raw_str = database.decode("utf-8")
        except Exception:
            return False
    elif isinstance(database, str):
        raw_str = database
    else:
        return False

    # String kosong atau mengandung whitespace di awal/akhir bukan database in-memory
    if not raw_str or raw_str != raw_str.strip():
        return False

    # 2. URI SQLite in-memory (hanya dievaluasi jika uri=True)
    if uri and raw_str.startswith("file:"):
        try:
            parsed = urllib.parse.urlsplit(raw_str)
        except Exception:
            return False

        if parsed.scheme != "file":
            return False

        if parsed.netloc:
            return False

        if parsed.fragment:
            return False

        # Path WAJIB persis ':memory:'. Bentuk ambigu dengan nama file atau path kosong ditolak.
        if parsed.path != ":memory:":
            return False

        if not parsed.query:
            return True

        try:
            pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not pairs:
                return False

            keys = [k for k, _ in pairs]
            # Tolak jika ada parameter duplikat
            if len(keys) != len(set(keys)):
                return False

            param_dict = dict(pairs)
            for key, val in param_dict.items():
                if key == "cache":
                    if val not in ("shared", "private"):
                        return False
                elif key == "mode":
                    if val != "memory":
                        return False
                else:
                    # Parameter selain cache (shared/private) dan mode=memory ditolak
                    return False

            return True
        except Exception:
            return False

    return False


def _normalize_sqlite_path(database: Any, uri: bool = False) -> Optional[Path]:
    """Normalisasi berbagai varian path SQLite dengan semantik SQLite resmi:
    - Membedakan path biasa vs URI berdasarkan parameter uri=True.
    - Tanpa .strip(): mempertahankan path persis seperti yang diberikan ke SQLite.
    - Pada path biasa (uri=False), karakter '?' TIDAK di-split dan percent-encoding TIDAK di-unquote.
    - Pada URI (uri=True), hanya diurai jika diawali 'file:', query string (?mode=ro) dipisahkan,
      percent-encoding di-unquote, serta path absolut POSIX maupun drive Windows ditangani dengan tepat.
    """
    if database is None:
        return None

    if isinstance(database, bytes):
        try:
            raw_str = database.decode("utf-8")
        except Exception:
            return None
    elif isinstance(database, (str, os.PathLike)):
        raw_str = str(database)
    else:
        return None

    # Path kosong atau persis ':memory:' tidak dinormalisasi ke filesystem path
    if not raw_str or raw_str == ":memory:":
        return None

    if uri:
        # Semantik URI SQLite: hanya diurai sebagai URI jika diawali 'file:'
        if raw_str.startswith("file:"):
            parsed = urllib.parse.urlsplit(raw_str)
            if parsed.path in (":memory:", ""):
                return None

            unquoted_path = urllib.parse.unquote(parsed.path)

            # Penanganan path Windows vs POSIX:
            # Pada Windows, file:///D:/path menghasilkan parsed.path = '/D:/path'
            drive_match = re.match(r"^/([a-zA-Z]:.*)$", unquoted_path)
            if drive_match:
                clean_path = drive_match.group(1)
            elif parsed.netloc and parsed.netloc.lower() != "localhost":
                # UNC path: file://server/share/db.sqlite -> //server/share/db.sqlite
                clean_path = f"//{parsed.netloc}{unquoted_path}"
            else:
                # POSIX path atau path relatif URI
                clean_path = unquoted_path

            if not clean_path or clean_path in (":memory:", ""):
                return None
            try:
                return Path(clean_path).resolve()
            except Exception:
                return None
        else:
            # uri=True tetapi tidak diawali 'file:' -> SQLite memperlakukannya sebagai path biasa
            try:
                return Path(raw_str).resolve()
            except Exception:
                return None
    else:
        # uri=False: SQLite memperlakukan seluruh string sebagai filesystem path literal
        # Karakter '?' adalah karakter nama file biasa, TIDAK di-split!
        # Percent-encoding TIDAK di-unquote!
        try:
            return Path(raw_str).resolve()
        except Exception:
            return None


# Pasang guard pada sqlite3.connect DAN sqlite3.dbapi2.connect SEBELUM import modul app
_orig_sqlite_connect = sqlite3.connect
_orig_dbapi2_connect = sqlite3.dbapi2.connect


class _GuardRegistry:
    orig_sqlite_connect = _orig_sqlite_connect
    orig_dbapi2_connect = _orig_dbapi2_connect
    orig_socket_connect = socket.socket.connect
    orig_socket_connect_ex = socket.socket.connect_ex
    orig_socket_sendto = socket.socket.sendto
    orig_getaddrinfo = socket.getaddrinfo
    orig_gethostbyname = socket.gethostbyname
    orig_gethostbyname_ex = socket.gethostbyname_ex


def _guarded_sqlite_connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
    if len(args) >= 7:
        uri = bool(args[6])
    else:
        uri = bool(kwargs.get("uri", False))

    # Izinkan :memory: atau anonymous disk database ""
    if _is_in_memory_sqlite(database, uri=uri):
        return _GuardRegistry.orig_sqlite_connect(database, *args, **kwargs)

    target_path = _normalize_sqlite_path(database, uri=uri)
    if target_path is None:
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Target path SQLite tidak dapat dipastikan aman atau tidak valid: {database!r}"
        )

    # 1. Tolak tegas database aplikasi terlarang
    if target_path in _blocked_paths:
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Pengujian mencoba mengakses database aplikasi terlarang: {target_path}"
        )

    # 2. Aturan allowlist: seluruh koneksi berbasis file WAJIB berada di dalam direktori sementara yang dikelola pengujian
    if not _is_path_in_allowed_dirs(target_path):
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Akses database SQLite di luar direktori sementara pengujian ditolak: {target_path}"
        )

    return _GuardRegistry.orig_sqlite_connect(database, *args, **kwargs)


sqlite3.connect = _guarded_sqlite_connect
sqlite3.dbapi2.connect = _guarded_sqlite_connect

# ─────────────────────────────────────────────────────────────────────────────
# 2. NETWORK & SERIAL HARDWARE GUARDS
# ─────────────────────────────────────────────────────────────────────────────
# Blokir koneksi jaringan eksternal (hanya izinkan loopback internal untuk asyncio IPC / TestClient)
def _is_loopback(host: Any) -> bool:
    if host is None:
        return True
    h = str(host).strip().lower()
    if h in ("127.0.0.1", "localhost", "::1", "0.0.0.0", ""):
        return True
    if h.startswith("127."):
        return True
    return False


def _guarded_socket_connect(self: socket.socket, address: Any) -> Any:
    if isinstance(address, tuple) and len(address) >= 1:
        if not _is_loopback(address[0]):
            raise RuntimeError(
                f"TEST ISOLATION VIOLATION: Koneksi TCP keluar ke host eksternal diblokir: {address}"
            )
    elif isinstance(address, str) and not _is_loopback(address):
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Koneksi keluar ke host eksternal diblokir: {address}"
        )
    return _GuardRegistry.orig_socket_connect(self, address)


def _guarded_socket_connect_ex(self: socket.socket, address: Any) -> int:
    if isinstance(address, tuple) and len(address) >= 1:
        if not _is_loopback(address[0]):
            raise RuntimeError(
                f"TEST ISOLATION VIOLATION: Koneksi non-blocking TCP keluar ke host eksternal diblokir: {address}"
            )
    elif isinstance(address, str) and not _is_loopback(address):
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Koneksi non-blocking TCP keluar ke host eksternal diblokir: {address}"
        )
    return _GuardRegistry.orig_socket_connect_ex(self, address)


def _guarded_socket_sendto(self: socket.socket, data: Any, *args: Any) -> int:
    target_addr = None
    if len(args) == 1:
        target_addr = args[0]
    elif len(args) >= 2:
        target_addr = args[1]
    if target_addr and isinstance(target_addr, tuple) and len(target_addr) >= 1:
        if not _is_loopback(target_addr[0]):
            raise RuntimeError(
                f"TEST ISOLATION VIOLATION: Pengiriman UDP/datagram ke host eksternal diblokir: {target_addr}"
            )
    return _GuardRegistry.orig_socket_sendto(self, data, *args)


def _guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if host is not None and not _is_loopback(host):
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Resolusi DNS host eksternal diblokir: {host}"
        )
    return _GuardRegistry.orig_getaddrinfo(host, port, *args, **kwargs)


def _guarded_gethostbyname(hostname: str) -> str:
    if not _is_loopback(hostname):
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Resolusi DNS gethostbyname eksternal diblokir: {hostname}"
        )
    return _GuardRegistry.orig_gethostbyname(hostname)


def _guarded_gethostbyname_ex(hostname: str) -> Any:
    if not _is_loopback(hostname):
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Resolusi DNS gethostbyname_ex eksternal diblokir: {hostname}"
        )
    return _GuardRegistry.orig_gethostbyname_ex(hostname)


socket.socket.connect = _guarded_socket_connect
socket.socket.connect_ex = _guarded_socket_connect_ex
socket.socket.sendto = _guarded_socket_sendto
socket.getaddrinfo = _guarded_getaddrinfo
socket.gethostbyname = _guarded_gethostbyname
socket.gethostbyname_ex = _guarded_gethostbyname_ex

# Blokir port serial fisik agar hardware scanner RFID tidak tersentuh saat pengujian
try:
    import serial
    _orig_serial_init = serial.Serial.__init__

    def _guarded_serial_init(self: Any, *args: Any, **kwargs: Any) -> None:
        self.is_open = False
        raise RuntimeError(
            f"TEST ISOLATION VIOLATION: Akses ke perangkat serial fisik diblokir selama pengujian: {args}"
        )

    serial.Serial.__init__ = _guarded_serial_init
except (ImportError, AttributeError):
    pass

# Bypass Windows sandbox loopback TCP connect blocking for asyncio self-pipe
if sys.platform == "win32":
    try:
        from asyncio.proactor_events import BaseProactorEventLoop
        from asyncio.windows_events import IocpProactor
        _udp_bypass = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        BaseProactorEventLoop._make_self_pipe = lambda self: setattr(self, "_ssock", _udp_bypass) or setattr(self, "_csock", _udp_bypass)
        BaseProactorEventLoop._close_self_pipe = lambda self: None
        BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None

        _orig_iocp_poll = IocpProactor._poll
        IocpProactor._poll = lambda self, timeout: _orig_iocp_poll(
            self, 0.01 if (timeout is None or timeout > 0.01) else timeout
        )
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# 3. IMPORT MODUL APLIKASI SETELAH SELURUH GUARD AKTIF
# ─────────────────────────────────────────────────────────────────────────────
from app.database import (
    DEFAULT_OLD_DB_PATH,
    DEFAULT_NEW_DB_PATH,
    get_effective_sqlite_path,
    init_db,
    SessionLocal,
    engine,
)

# Buktikan bahwa engine dan SessionLocal aplikasi menunjuk database sementara
effective_engine_path = get_effective_sqlite_path(engine)
assert effective_engine_path is not None, "Engine harus menunjuk database file SQLite sementara!"
assert effective_engine_path.resolve() == _test_db_path, (
    f"Engine harus menunjuk file sementara {_test_db_path}, bukan {effective_engine_path}"
)
assert effective_engine_path.resolve() not in _blocked_paths, (
    f"Engine aplikasi tidak boleh menunjuk database aplikasi saat pengujian! ({effective_engine_path})"
)

from app.models.rumah_sakit import RumahSakit
from app.routers.auth_router import AuthRouterConfig, set_default_config
from app.services.auth_dependencies import require_active_session
from app.main import app
from app.services.hospital_binding_service import provision_hospital_binding

# Konfigurasi eksplisit sesi dan origin tes
set_default_config(
    AuthRouterConfig(
        session_duration_seconds=28800,
        allowed_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://testserver"],
    )
)

# Inisialisasi skema dan binding rumah sakit default uji coba
init_db()
_db = SessionLocal()
try:
    provision_hospital_binding(
        _db,
        hospital_id=101,
        nama_rs="RS Uji Coba",
        kode_rs="RS_TEST",
        alamat="Jl. Uji No. 1",
    )
finally:
    _db.close()


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_suite():
    """Tutup koneksi, dispose engine, dan bersihkan direktori sementara setelah suite selesai."""
    yield
    try:
        from app.database import engine
        engine.dispose()
    except Exception:
        pass
    try:
        if _test_tmp_dir is not None:
            _test_tmp_dir.cleanup()
    except Exception:
        pass


@pytest.fixture
def isolation_guard_info():
    """Fixture yang mengekspos informasi guard pengujian tanpa perlu mengimpor ulang conftest."""
    def _block_path(p: Path) -> None:
        _blocked_paths.add(Path(p).resolve())

    def _unblock_path(p: Path) -> None:
        _blocked_paths.discard(Path(p).resolve())

    def _register_allowed_dir(p: Path) -> None:
        _allowed_temp_dirs.add(Path(p).resolve())

    def _unregister_allowed_dir(p: Path) -> None:
        _allowed_temp_dirs.discard(Path(p).resolve())

    return {
        "blocked_paths": _blocked_paths,
        "block_path": _block_path,
        "unblock_path": _unblock_path,
        "allowed_temp_dirs": _allowed_temp_dirs,
        "register_allowed_dir": _register_allowed_dir,
        "unregister_allowed_dir": _unregister_allowed_dir,
        "is_path_allowed": _is_path_in_allowed_dirs,
        "managed_tmp_dir": _managed_temp_dir_path,
        "test_db_path": _test_db_path,
        "normalize_path": _normalize_sqlite_path,
        "orig_sqlite_connect": _GuardRegistry.orig_sqlite_connect,
        "orig_dbapi2_connect": _GuardRegistry.orig_dbapi2_connect,
        "orig_socket_connect": _GuardRegistry.orig_socket_connect,
        "orig_socket_connect_ex": _GuardRegistry.orig_socket_connect_ex,
        "orig_socket_sendto": _GuardRegistry.orig_socket_sendto,
        "orig_getaddrinfo": _GuardRegistry.orig_getaddrinfo,
        "orig_gethostbyname": _GuardRegistry.orig_gethostbyname,
        "orig_gethostbyname_ex": _GuardRegistry.orig_gethostbyname_ex,
        "registry": _GuardRegistry,
    }


@pytest.fixture(autouse=True)
def configure_auth_and_session(request):
    """
    Ensure legacy functional tests run in an authenticated context,
    while auth test suites (test_auth_*) run against the real security dependencies.
    """
    test_file_name = request.node.fspath.basename
    if "test_auth" in test_file_name:
        # Auth tests verify real unauthenticated 401s, redirects, cookies, and tokens
        yield
        return

    # For legacy business logic tests, provide authenticated session context
    app.dependency_overrides[require_active_session] = lambda: {
        "user_id": "test-user-id",
        "email": "operator@rs.id",
        "hospital_id": 101,
        "role": "operator",
    }
    yield
    app.dependency_overrides.pop(require_active_session, None)
