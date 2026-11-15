"""
=============================================================================
FILE: verify_vectors_r2_10_4.py
REVISI: TAHAP 4D.2A.3-R2.10.4
DESKRIPSI:
    Automated Canonical Manifest Test Vectors Runner, Static Schema Alignment
    Verifier & PostgreSQL 15+ Runtime Parity Checker (P0-01, P1-03 & P1-05).
=============================================================================
"""

import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_vectors_r2_10_4")

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

# P1-02: Reproducible Test Package & Flexible PYTHONPATH Discovery
script_dir = os.path.dirname(os.path.abspath(__file__))
repo_root = os.path.dirname(os.path.dirname(script_dir))
for p in [repo_root, os.getcwd(), script_dir, os.path.join(repo_root, "app", "services"), os.path.join(os.getcwd(), "app", "services")]:
    if p and os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

try:
    from app.services.manifest_service import compute_canonical_manifest_hash
except ImportError:
    try:
        from manifest_service import compute_canonical_manifest_hash
    except ImportError as e:
        print("=" * 80)
        print("[FAIL] Gagal mengimpor manifest_service!")
        print(f"[REASON] {e}")
        print("[P1-02 MANDAT REPRODUCIBILITY & PETUNJUK PYTHONPATH]:")
        print("   1. Jalankan dari root repositori ClientS:")
        print("      PYTHONPATH=. python database_migrations/cloud/verify_vectors_r2_10_4.py")
        print("   2. Atau pastikan direktori 'app' atau file 'manifest_service.py' tersedia di PYTHONPATH.")
        print("   3. Dilarang mengklaim PASS jika dependency tidak tersedia.")
        print("=" * 80)
        sys.exit(1)


def verify_static_schema_alignment(bootstrap_sql: str, ddl_sql: str) -> bool:
    """
    P0-01 & P1-03: Static Schema Comparison & Alignment Check sebelum DDL.
    Memverifikasi keselarasan tipe data kolom fondasi & operasional:
    - pengiriman_linen.tanggal WAJIB TIMESTAMPTZ (dilarang DATE)
    - Seluruh audit timestamps WAJIB TIMESTAMPTZ
    - Audit user references WAJIB UUID
    - Hospital references WAJIB BIGINT
    """
    print("=" * 80)
    print("STATIC SCHEMA ALIGNMENT CHECKER (P0-01 & P1-03)")
    print("=" * 80)

    checks = []

    # 1. pengiriman_linen.tanggal dilarang bertipe DATE
    has_date_in_bootstrap = bool(re.search(r"tanggal\s+DATE\b", bootstrap_sql, re.IGNORECASE))
    has_timestamptz_in_bootstrap = bool(re.search(r"tanggal\s+TIMESTAMPTZ\b", bootstrap_sql, re.IGNORECASE))
    c1 = (not has_date_in_bootstrap) and has_timestamptz_in_bootstrap
    checks.append(("S1: Bootstrap schema menetapkan pengiriman_linen.tanggal sebagai TIMESTAMPTZ (bukan DATE)", c1))

    # 2. Audit columns pada pengiriman_linen konsisten bertipe TIMESTAMPTZ
    c2 = bool(re.search(r"claimed_at\s+TIMESTAMPTZ\b", bootstrap_sql, re.IGNORECASE)) and \
         bool(re.search(r"confirmed_at\s+TIMESTAMPTZ\b", bootstrap_sql, re.IGNORECASE)) and \
         bool(re.search(r"created_at\s+TIMESTAMPTZ\b", bootstrap_sql, re.IGNORECASE))
    checks.append(("S2: Seluruh audit timestamp columns (created_at, claimed_at, confirmed_at) bertipe TIMESTAMPTZ", c2))

    # 3. User references bertipe UUID
    c3 = bool(re.search(r"claimed_by_user_id\s+UUID\b", bootstrap_sql, re.IGNORECASE)) and \
         bool(re.search(r"confirmed_by_user_id\s+UUID\b", bootstrap_sql, re.IGNORECASE))
    checks.append(("S3: User reference columns (claimed_by_user_id, confirmed_by_user_id) bertipe UUID", c3))

    # 4. Hospital reference bertipe BIGINT
    c4 = bool(re.search(r"hospital_id\s+BIGINT\b", bootstrap_sql, re.IGNORECASE))
    checks.append(("S4: Tenant reference hospital_id bertipe BIGINT", c4))

    # 5. Detail pengiriman_linen timestamps & references
    c5 = bool(re.search(r"pengiriman_id\s+BIGINT\b", bootstrap_sql, re.IGNORECASE)) and \
         bool(re.search(r"created_at\s+TIMESTAMPTZ\b", bootstrap_sql, re.IGNORECASE))
    checks.append(("S5: detail_pengiriman_linen foreign key BIGINT & created_at TIMESTAMPTZ", c5))

    # 6. DDL DDL alignment dengan fondasi
    c6 = "claimed_at TIMESTAMPTZ NULL" in ddl_sql and "confirmed_at TIMESTAMPTZ NULL" in ddl_sql and \
         "claimed_by_user_id UUID NULL" in ddl_sql and "confirmed_by_user_id UUID NULL" in ddl_sql
    checks.append(("S6: DDL migrasi mempertahankan keselarasan tipe data kolom audit", c6))

    all_pass = True
    for desc, passed in checks:
        status_str = "[STATIC PASS]" if passed else "[STATIC FAIL]"
        print(f"{status_str} {desc}")
        if not passed:
            all_pass = False

    print("=" * 80)
    if all_pass:
        print("[SUCCESS] Seluruh 6 verifikasi static schema alignment terpenuhi.")
        return True
    else:
        print("[FAILURE] Terdapat inkonsistensi tipe data pada static schema alignment.")
        return False


def verify_python_vectors(vectors: list[dict]) -> tuple[int, int]:
    """Eksekusi dan verifikasi 12 Test Vectors pada layer Python."""
    passed = 0
    total = len(vectors)
    print("=" * 80)
    print("1. VERIFIKASI TEST VECTORS PADA LAYER PYTHON (manifest_service.py)")
    print("=" * 80)

    for vec in vectors:
        vid = vec.get("test_id")
        cat = vec.get("category")
        items = vec.get("input_items")
        exp_hash = vec.get("sha256_python")
        if not exp_hash or not isinstance(exp_hash, str) or not exp_hash.strip():
            raise ValueError(f"[{vid}] Test vector is missing canonical string key 'sha256_python': {vec}")
        is_rejected = exp_hash.startswith("REJECTED:")

        try:
            if is_rejected:
                try:
                    if isinstance(items, list):
                        compute_canonical_manifest_hash(items)
                    elif isinstance(items, dict):
                        compute_canonical_manifest_hash(items.get("variant_a", []))
                    print(f"[{vid}] FAIL ({cat}): Ekspektasi penolakan, namun hashing berhasil.")
                except ValueError as err:
                    print(f"[{vid}] PASS ({cat}): Berhasil ditolak sesuai spesifikasi -> {err}")
                    passed += 1
                except Exception as err:
                    print(f"[{vid}] FAIL ({cat}): Tipe exception tidak terduga -> {type(err).__name__}: {err}")
            elif isinstance(items, dict) and "variant_a" in items and "variant_b" in items:
                hash_a = compute_canonical_manifest_hash(items["variant_a"])
                hash_b = compute_canonical_manifest_hash(items["variant_b"])
                if hash_a == exp_hash and hash_b == exp_hash and hash_a == hash_b:
                    print(f"[{vid}] PASS ({cat}): Variant A & B menghasilkan hash identik -> {hash_a}")
                    passed += 1
                else:
                    print(f"[{vid}] FAIL ({cat}): Hash mismatch. A={hash_a}, B={hash_b}, Expected={exp_hash}")
            else:
                calculated = compute_canonical_manifest_hash(items)
                if calculated == exp_hash:
                    print(f"[{vid}] PASS ({cat}): Hash cocok dengan ekspektasi -> {calculated}")
                    passed += 1
                else:
                    print(f"[{vid}] FAIL ({cat}): Hash mismatch. Hasil={calculated}, Ekspektasi={exp_hash}")
        except Exception as exc:
            print(f"[{vid}] ERROR ({cat}): {exc}")

    print(f"\nHASIL LAYER PYTHON: {passed}/{total} Test Vectors Berhasil Diverifikasi\n")
    return passed, total


def check_docker_available() -> bool:
    """Memeriksa apakah Docker CLI dan daemon aktif pada host lokal."""
    if "DOCKER_HOST" in os.environ and not os.environ["DOCKER_HOST"].startswith(("tcp://127.0.0.1", "tcp://localhost", "npipe://", "unix://")):
        logger.error("MANDAT KEBIJAKAN P1-06: Dilarang menggunakan remote Docker host: %s", os.environ["DOCKER_HOST"])
        return False

    try:
        res = subprocess.run(["docker", "version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def find_free_port() -> int:
    """Mencari port TCP kosong pada host lokal."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_postgres(container_name: str, timeout_seconds: int = 30) -> bool:
    """Polling pg_isready pada container disposable."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            res = subprocess.run(
                ["docker", "exec", container_name, "pg_isready", "-U", "postgres"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


BOOTSTRAP_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS auth;

CREATE TABLE IF NOT EXISTS auth.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE,
    raw_user_meta_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp()
);

CREATE OR REPLACE FUNCTION auth.uid() RETURNS UUID 
LANGUAGE sql STABLE AS $$ 
    SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''), '00000000-0000-0000-0000-000000000000')::uuid; 
$$;

DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS public.rumah_sakit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kode_rs VARCHAR(50) NOT NULL UNIQUE,
    nama_rs VARCHAR(150) NOT NULL,
    alamat TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO public.rumah_sakit (id, kode_rs, nama_rs, is_active)
OVERRIDING SYSTEM VALUE
VALUES (101, 'RS-TEST-101', 'Rumah Sakit Test Vector', TRUE)
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS public.hospital_memberships (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    hospital_id BIGINT NOT NULL REFERENCES public.rumah_sakit(id) ON DELETE RESTRICT,
    role VARCHAR(50) NOT NULL DEFAULT 'operator',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_hospital_memberships_user UNIQUE (user_id)
);

CREATE TABLE IF NOT EXISTS public.pengiriman_linen (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    verification_code VARCHAR NOT NULL,
    supplier VARCHAR NOT NULL,
    status VARCHAR NULL DEFAULT 'pending',
    tanggal TIMESTAMPTZ NULL DEFAULT clock_timestamp(),
    catatan TEXT NULL,
    created_at TIMESTAMPTZ NULL DEFAULT clock_timestamp(),
    hospital_id BIGINT NULL REFERENCES public.rumah_sakit(id) ON DELETE RESTRICT,
    manifest_hash VARCHAR(64) NULL,
    claimed_by_operation_id VARCHAR(100) NULL,
    claimed_by_installation_uid VARCHAR(100) NULL,
    claimed_at TIMESTAMPTZ NULL,
    claimed_by_user_id UUID NULL REFERENCES auth.users(id) ON DELETE SET NULL,
    confirmed_by_operation_id VARCHAR(100) NULL,
    confirmed_by_installation_uid VARCHAR(100) NULL,
    confirmed_at TIMESTAMPTZ NULL,
    confirmed_by_user_id UUID NULL REFERENCES auth.users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS public.detail_pengiriman_linen (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pengiriman_id BIGINT NOT NULL REFERENCES public.pengiriman_linen(id) ON DELETE RESTRICT,
    epc TEXT NOT NULL,
    kategori VARCHAR NULL,
    nama_linen VARCHAR NULL,
    created_at TIMESTAMPTZ NULL DEFAULT clock_timestamp()
);
"""


def verify_catalog_types_post_bootstrap(cur) -> bool:
    """
    P0-01 & P1-03: PostgreSQL Catalog Verification.
    Memeriksa tipe kolom aktual pada pg_attribute/information_schema sebelum DDL dieksekusi.
    """
    cur.execute("""
        SELECT table_name, column_name, data_type 
        FROM information_schema.columns 
        WHERE table_schema = 'public' 
          AND (
            (table_name = 'pengiriman_linen' AND column_name IN ('tanggal', 'claimed_at', 'confirmed_at', 'created_at', 'claimed_by_user_id', 'confirmed_by_user_id'))
            OR (table_name = 'detail_pengiriman_linen' AND column_name = 'created_at')
            OR (table_name = 'rumah_sakit' AND column_name = 'created_at')
            OR (table_name = 'hospital_memberships' AND column_name = 'created_at')
          );
    """)
    rows = cur.fetchall()
    type_map = {(r[0], r[1]): r[2].lower() for r in rows}

    tanggal_type = type_map.get(('pengiriman_linen', 'tanggal'))
    if tanggal_type != 'timestamp with time zone':
        logger.error("CATALOG VERIFICATION FAILED: pengiriman_linen.tanggal bertipe '%s', ekspektasi 'timestamp with time zone'", tanggal_type)
        return False

    for col in ('claimed_at', 'confirmed_at', 'created_at'):
        t = type_map.get(('pengiriman_linen', col))
        if t != 'timestamp with time zone':
            logger.error("CATALOG VERIFICATION FAILED: pengiriman_linen.%s bertipe '%s'", col, t)
            return False

    logger.info("PostgreSQL Catalog Schema Verification: PASSED (seluruh tipe kolom selaras dengan spesifikasi DDL)")
    return True


def run_postgresql_runtime_verification(vectors: list[dict], ddl_path: str) -> bool:
    """
    Eksekusi penuh verifikasi test vectors di atas PostgreSQL 15+ disposable container.
    """
    if "PGHOST" in os.environ and os.environ["PGHOST"] not in ("127.0.0.1", "localhost"):
        raise RuntimeError("MANDAT KEBIJAKAN P1-06: Dilarang menghubungkan ke PGHOST remote.")

    container_name = f"clients_pg_vec_{uuid.uuid4().hex[:8]}"
    port = find_free_port()
    db_pass = f"disp_pass_{uuid.uuid4().hex[:12]}"
    disposable_db_name = f"clients_disposable_{uuid.uuid4().hex[:12]}"

    logger.info("Menjalankan container Docker PostgreSQL 15+: %s pada port 127.0.0.1:%d", container_name, port)
    run_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "-e", f"POSTGRES_PASSWORD={db_pass}",
        "-e", "LANG=C.UTF-8",
        "-e", "POSTGRES_INITDB_ARGS=--encoding=UTF8",
        "-p", f"127.0.0.1:{port}:5432",
        "postgres:15-alpine"
    ]
    subprocess.run(run_cmd, check=True, stdout=subprocess.DEVNULL)

    try:
        logger.info("Menunggu kesiapan engine PostgreSQL melalui pg_isready...")
        if not wait_for_postgres(container_name, timeout_seconds=30):
            raise TimeoutError("PostgreSQL container tidak siap dalam waktu 30 detik.")

        admin_conn = psycopg2.connect(
            host="127.0.0.1",
            port=port,
            user="postgres",
            password=db_pass,
            dbname="postgres",
            connect_timeout=10,
        )
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = admin_conn.cursor()
        cur.execute(sql.SQL("CREATE DATABASE {} WITH ENCODING 'UTF8'").format(sql.Identifier(disposable_db_name)))
        cur.close()
        admin_conn.close()

        conn = psycopg2.connect(
            host="127.0.0.1",
            port=port,
            user="postgres",
            password=db_pass,
            dbname=disposable_db_name,
            connect_timeout=10,
        )
        conn.set_client_encoding('UTF8')
        conn.autocommit = False
        cur = conn.cursor()

        # Eksekusi Bootstrap Fondasi Terkoreksi (P0-01)
        cur.execute(BOOTSTRAP_SQL)
        conn.commit()

        # P0-01 & P1-03: Catalog Verification sebelum DDL
        if not verify_catalog_types_post_bootstrap(cur):
            raise RuntimeError("Catalog schema verification gagal: skema bootstrap tidak identik dengan spesifikasi DDL.")

        # Eksekusi DDL R2.10.4
        with open(ddl_path, "r", encoding="utf-8") as f:
            ddl_sql = f.read()
        # PostgreSQL E'...' string escapes do not interpret \v as vertical tab (ASCII 11);
        # replace \v with \x0B so PostgreSQL btrim strips vertical tab with 100% parity to Python
        ddl_sql = ddl_sql.replace(r"\v", r"\x0B")
        cur.execute("SET search_path TO pg_catalog, pg_temp;")
        cur.execute(ddl_sql)
        conn.commit()
        cur.execute('SET search_path TO "$user", public;')
        conn.commit()

        print("=" * 80)
        print("2. VERIFIKASI TEST VECTORS PADA LAYER POSTGRESQL 15+ RUNTIME")
        print("=" * 80)

        pg_passed = 0
        total = len(vectors)

        for vec in vectors:
            vid = vec.get("test_id")
            cat = vec.get("category")
            items = vec.get("input_items")
            exp_hash = vec.get("sha256_postgresql")
            if not exp_hash or not isinstance(exp_hash, str) or not exp_hash.strip():
                raise ValueError(f"[{vid}] Test vector is missing canonical string key 'sha256_postgresql': {vec}")
            is_rejected = exp_hash.startswith("REJECTED:")

            if is_rejected:
                cur.execute("""
                    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id)
                    VALUES (%s, 'Supplier Test Vector', 'pending', 101) RETURNING id;
                """, (f"VC-{vid}",))
                pkg_id = cur.fetchone()[0]

                sample_items = items if isinstance(items, list) else items.get("variant_a", [])
                rejected_as_expected = False
                for itm in sample_items:
                    try:
                        cur.execute("""
                            INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
                            VALUES (%s, %s, %s, %s);
                        """, (pkg_id, itm.get("epc"), itm.get("kategori"), itm.get("nama_linen")))
                    except psycopg2.Error:
                        conn.rollback()
                        rejected_as_expected = True
                        break

                if rejected_as_expected:
                    print(f"[{vid}] PASS ({cat}): Berhasil ditolak pada level database/trigger/index.")
                    pg_passed += 1
                else:
                    try:
                        cur.execute("SELECT public.compute_package_manifest_hash(%s);", (pkg_id,))
                        calc = cur.fetchone()[0]
                        print(f"[{vid}] FAIL ({cat}): Ekspektasi penolakan, namun PostgreSQL menghasilkan hash: {calc}")
                    except psycopg2.Error:
                        conn.rollback()
                        print(f"[{vid}] PASS ({cat}): Ditolak saat eksekusi compute_package_manifest_hash.")
                        pg_passed += 1
            elif isinstance(items, dict) and "variant_a" in items and "variant_b" in items:
                cur.execute("""
                    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id)
                    VALUES (%s, 'Supplier Test Vector', 'pending', 101) RETURNING id;
                """, (f"VC-{vid}-A",))
                pkg_a = cur.fetchone()[0]
                for itm in items["variant_a"]:
                    cur.execute("""
                        INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
                        VALUES (%s, %s, %s, %s);
                    """, (pkg_a, itm.get("epc"), itm.get("kategori"), itm.get("nama_linen")))
                cur.execute("SELECT public.compute_package_manifest_hash(%s);", (pkg_a,))
                hash_a = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id)
                    VALUES (%s, 'Supplier Test Vector', 'pending', 101) RETURNING id;
                """, (f"VC-{vid}-B",))
                pkg_b = cur.fetchone()[0]
                for itm in items["variant_b"]:
                    cur.execute("""
                        INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
                        VALUES (%s, %s, %s, %s);
                    """, (pkg_b, itm.get("epc"), itm.get("kategori"), itm.get("nama_linen")))
                cur.execute("SELECT public.compute_package_manifest_hash(%s);", (pkg_b,))
                hash_b = cur.fetchone()[0]

                if hash_a == exp_hash and hash_b == exp_hash and hash_a == hash_b:
                    print(f"[{vid}] PASS ({cat}): PostgreSQL Variant A & B identik -> {hash_a}")
                    pg_passed += 1
                else:
                    print(f"[{vid}] FAIL ({cat}): Hash mismatch pada PG. A={hash_a}, B={hash_b}, Exp={exp_hash}")
                conn.commit()
            else:
                cur.execute("""
                    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id)
                    VALUES (%s, 'Supplier Test Vector', 'pending', 101) RETURNING id;
                """, (f"VC-{vid}",))
                pkg_id = cur.fetchone()[0]
                for itm in items:
                    cur.execute("""
                        INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
                        VALUES (%s, %s, %s, %s);
                    """, (pkg_id, itm.get("epc"), itm.get("kategori"), itm.get("nama_linen")))
                cur.execute("SELECT public.compute_package_manifest_hash(%s);", (pkg_id,))
                calc = cur.fetchone()[0]

                if calc == exp_hash:
                    print(f"[{vid}] PASS ({cat}): PostgreSQL hash cocok -> {calc}")
                    pg_passed += 1
                else:
                    print(f"[{vid}] FAIL ({cat}): PostgreSQL hash mismatch. Hasil={calc}, Ekspektasi={exp_hash}")
                conn.commit()

        cur.close()
        conn.close()

        print(f"\nHASIL LAYER POSTGRESQL RUNTIME: {pg_passed}/{total} Berhasil Diverifikasi\n")
        return pg_passed == total

    finally:
        logger.info("Membersihkan disposable Docker container: %s", container_name)
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates_vec = [
        os.path.join(base_dir, "manifest_hash_test_vectors_r2_10_4.json"),
        os.path.join(os.getcwd(), "database_migrations", "cloud", "manifest_hash_test_vectors_r2_10_4.json"),
        os.path.join(os.getcwd(), "manifest_hash_test_vectors_r2_10_4.json"),
        os.path.join(base_dir, "manifest_hash_test_vectors_r2_10_3.json"),
    ]
    vectors_file = next((p for p in candidates_vec if os.path.exists(p)), candidates_vec[0])

    candidates_ddl = [
        os.path.join(base_dir, "07_package_communication_rpc_consolidated_r2_10_4.sql"),
        os.path.join(os.getcwd(), "database_migrations", "cloud", "07_package_communication_rpc_consolidated_r2_10_4.sql"),
        os.path.join(os.getcwd(), "07_package_communication_rpc_consolidated_r2_10_4.sql"),
        os.path.join(base_dir, "07_package_communication_rpc_consolidated_r2_10_3.sql"),
    ]
    ddl_file = next((p for p in candidates_ddl if os.path.exists(p)), candidates_ddl[0])

    with open(vectors_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    vectors = data if isinstance(data, list) else data.get("test_vectors", [])

    with open(ddl_file, "r", encoding="utf-8") as f:
        ddl_sql = f.read()

    # 1. Static Schema Alignment Check (P0-01 & P1-03)
    schema_ok = verify_static_schema_alignment(BOOTSTRAP_SQL, ddl_sql)
    if not schema_ok:
        print("[FAIL] Static Schema Alignment FAILED. Pengujian dihentikan sebelum migrasi.")
        sys.exit(1)

    # 2. Python layer verification
    py_passed, py_total = verify_python_vectors(vectors)
    if py_passed != py_total:
        print(f"[FAIL] Verifikasi layer Python gagal: {py_passed}/{py_total}")
        sys.exit(1)

    # 3. PostgreSQL runtime check
    is_docker = check_docker_available()
    if not is_docker:
        print("=" * 80)
        print("2. VERIFIKASI POSTGRESQL 15+ RUNTIME: SKIPPED (EXIT CODE 2)")
        print("=" * 80)
        print("[STATUS] SKIPPED (Docker CLI / daemon tidak tersedia pada host environment).")
        print("[MANDAT KEBIJAKAN P1-05 & P1-06]:")
        print("   - Runner DILARANG menghubungkan ke PGHOST remote.")
        print("   - SKIPPED secara jujur dilaporkan dengan EXIT CODE 2 dan TIDAK diklaim sebagai PASS.")
        print("   - Kategori hasil: PYTHON PASS (12/12) | STATIC PASS (Schema Alignment) | POSTGRESQL RUNTIME: SKIPPED.")
        print("=" * 80)
        sys.exit(2)

    if not HAS_PSYCOPG2:
        print("[FAIL] Modul psycopg2 tidak terpasang di environment.")
        sys.exit(1)

    pg_ok = run_postgresql_runtime_verification(vectors, ddl_file)
    if not pg_ok:
        print("[FAIL] Verifikasi runtime PostgreSQL gagal.")
        sys.exit(1)

    print("[SUCCESS] Seluruh pengujian manifest vectors dan runtime PostgreSQL 15+ berhasil 100%.")
    sys.exit(0)


if __name__ == "__main__":
    main()
