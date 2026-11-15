"""
=============================================================================
FILE: run_concurrency_tests_r2_10_4.py
REVISI: TAHAP 4D.2A.3-R2.10.4
DESKRIPSI:
    Automated Concurrency & Multi-Tenant Test Runner untuk Migrasi R2.10.4
    Menerapkan:
    1. Transaction Integrity & Explicit Commit pada Skenario 1 (P0-01 Fix).
    2. Role Fidelity & Operasional Role Sebenarnya (service_role, authenticated, anon) (P0-02 Fix).
    3. TV-09 Constraint Name Verification & Temporary Trigger Bypass Test (P0-03 Fix).
    4. Thread Timeout Assertion & Deadlock Isolation (P1-02 & P1-03 Fix).
    5. Detail Immutability Across Statuses (P1-05 Fix).
    6. Container Isolation 127.0.0.1 (P1-06 Fix).
=============================================================================
"""

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ConcurrencyRunnerR2.10.4")

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


def check_docker_available() -> bool:
    """Memeriksa ketersediaan Docker daemon lokal sesuai P1-06."""
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


def wait_for_blocking(monitor_conn, blocked_pid: int, blocker_pid: int, timeout_seconds: float = 3.0) -> bool:
    """Aktif membuktikan blocked_pid sedang menunggu lock yang dipegang oleh blocker_pid."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        cur = monitor_conn.cursor()
        cur.execute("SELECT %s = ANY(pg_blocking_pids(%s));", (blocker_pid, blocked_pid))
        row = cur.fetchone()
        cur.close()
        if row and row[0]:
            return True
        time.sleep(0.05)
    return False


def safe_join_thread(t: threading.Thread, conn_to_abort=None, timeout: float = 10.0) -> bool:
    """P1-02: Join thread dengan batas waktu dan pemeriksaan is_alive()."""
    t.join(timeout=timeout)
    if t.is_alive():
        logger.error("P1-02 VIOLATION: Thread '%s' masih berjalan setelah timeout %.1f detik!", t.name, timeout)
        if conn_to_abort:
            try:
                conn_to_abort.close()
            except Exception:
                pass
        return False
    return True


def assert_idle_connection(conn, conn_name: str = "connection"):
    """P0-01: Memastikan koneksi tidak berada dalam status INTRANS."""
    status = conn.get_transaction_status()
    if status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
        conn.rollback()
        raise AssertionError(f"P0-01 VIOLATION: Koneksi '{conn_name}' masih dalam transaksi terbuka (status: {status})!")


def run_full_concurrency_suite(port: int, db_pass: str, disposable_db_name: str, ddl_path: str, harness_path: str) -> dict:
    """
    Eksekusi 11 langkah bootstrap dan pengujian konkurensi/role fidelity.
    """
    logger.info("Menyambungkan ke database disposable: %s", disposable_db_name)
    admin_conn = psycopg2.connect(
        host="127.0.0.1",
        port=port,
        user="postgres",
        password=db_pass,
        dbname=disposable_db_name,
        connect_timeout=10,
    )
    admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur_admin = admin_conn.cursor()

    # Langkah 2: Bootstrap pgcrypto
    logger.info("Langkah 2: Inisialisasi ekstensi pgcrypto...")
    cur_admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

    # Langkah 3: Bootstrap auth schema & auth.users
    logger.info("Langkah 3: Inisialisasi skema auth dan tabel auth.users...")
    cur_admin.execute("CREATE SCHEMA IF NOT EXISTS auth;")
    cur_admin.execute("""
        CREATE TABLE IF NOT EXISTS auth.users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email VARCHAR(255) UNIQUE NOT NULL,
            raw_user_meta_data JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT clock_timestamp()
        );
    """)

    # Langkah 4: Bootstrap auth.uid()
    logger.info("Langkah 4: Inisialisasi fungsi auth.uid()...")
    cur_admin.execute("""
        CREATE OR REPLACE FUNCTION auth.uid() RETURNS UUID 
        LANGUAGE sql STABLE AS $$ 
            SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''), '00000000-0000-0000-0000-000000000000')::uuid; 
        $$;
    """)

    # Langkah 5: Bootstrap role
    logger.info("Langkah 5: Inisialisasi peran Supabase (anon, authenticated, service_role)...")
    cur_admin.execute("""
        DO $$ 
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
        END $$;
    """)

    # Langkah 6: Bootstrap rumah_sakit
    logger.info("Langkah 6: Inisialisasi tabel fondasi rumah_sakit...")
    cur_admin.execute("""
        CREATE TABLE IF NOT EXISTS public.rumah_sakit (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            kode_rs VARCHAR(50) UNIQUE NOT NULL,
            nama_rs VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT clock_timestamp()
        );
    """)

    # Langkah 7: Bootstrap hospital_memberships
    logger.info("Langkah 7: Inisialisasi tabel fondasi hospital_memberships...")
    cur_admin.execute("""
        CREATE TABLE IF NOT EXISTS public.hospital_memberships (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
            hospital_id BIGINT NOT NULL REFERENCES public.rumah_sakit(id) ON DELETE CASCADE,
            role VARCHAR(50) NOT NULL DEFAULT 'operator',
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT clock_timestamp(),
            CONSTRAINT uq_hospital_memberships_user_hospital UNIQUE (user_id, hospital_id)
        );
    """)

    # Langkah 8: Bootstrap tabel operasional
    logger.info("Langkah 8: Inisialisasi tabel operasional pengiriman_linen...")
    cur_admin.execute("""
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
    """)

    cur_admin.execute("""
        CREATE TABLE IF NOT EXISTS public.detail_pengiriman_linen (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            pengiriman_id BIGINT NOT NULL REFERENCES public.pengiriman_linen(id) ON DELETE RESTRICT,
            epc TEXT NOT NULL,
            kategori VARCHAR NULL,
            nama_linen VARCHAR NULL,
            created_at TIMESTAMPTZ NULL DEFAULT clock_timestamp()
        );
    """)

    # Langkah 9: Jalankan DDL R2.10.4
    logger.info("Langkah 9: Menjalankan DDL Konsolidasi R2.10.4...")
    with open(ddl_path, "r", encoding="utf-8") as f:
        ddl_sql = f.read()
    ddl_sql = ddl_sql.replace(r"\v", r"\x0B")
    cur_admin.execute("SET search_path TO pg_catalog, pg_temp;")
    cur_admin.execute(ddl_sql)
    cur_admin.execute('SET search_path TO "$user", public;')
    logger.info("DDL R2.10.4 berhasil diaplikasikan.")

    # Langkah 10: Jalankan SQL fixture harness
    logger.info("Langkah 10: Menjalankan SQL Fixture Harness R2.10.4...")
    with open(harness_path, "r", encoding="utf-8") as f:
        harness_sql = f.read()
    cur_admin.execute(harness_sql)
    logger.info("Fixture harness R2.10.4 berhasil diaplikasikan.")

    # Ekstraksi label fixture dari _test_fixture_tracker
    cur_admin.execute("SELECT label, entity_id, entity_uuid FROM _test_fixture_tracker;")
    fixtures = {}
    for row in cur_admin.fetchall():
        lbl, eid, euuid = row
        fixtures[lbl] = eid if eid is not None else euuid

    # P0-02 & P0-04: Administrator pre-loads verification_code and manifest_hash for fixture packages
    # Authenticated role cannot select verification_code or execute compute_package_manifest_hash directly.
    cur_admin.execute("""
        SELECT id, verification_code, public.compute_package_manifest_hash(id)
        FROM public.pengiriman_linen;
    """)
    pkg_admin_data = {}
    for row in cur_admin.fetchall():
        pid, vc, mhash = row
        pkg_admin_data[pid] = {
            "verification_code": vc,
            "manifest_hash": mhash
        }
    cur_admin.close()
    admin_conn.close()

    # Buka 3 koneksi pengujian: Conn1 (Supplier/Operator), Conn2 (Competitor), Monitor (Admin)
    conn1 = psycopg2.connect(host="127.0.0.1", port=port, user="postgres", password=db_pass, dbname=disposable_db_name)
    conn2 = psycopg2.connect(host="127.0.0.1", port=port, user="postgres", password=db_pass, dbname=disposable_db_name)
    monitor_conn = psycopg2.connect(host="127.0.0.1", port=port, user="postgres", password=db_pass, dbname=disposable_db_name)
    monitor_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

    pid1 = conn1.get_backend_pid()
    pid2 = conn2.get_backend_pid()

    results = {}
    actual_sqlstates = {}

    try:
        # =====================================================================
        # ROLE & PRIVILEGE TEST SUITE (P0-02 Fix)
        # =====================================================================
        logger.info("Menjalankan Role & Privilege Test Suite (P0-02)...")
        p_cur = conn1.cursor()

        # P-01: service_role dapat INSERT
        p_cur.execute("SET ROLE service_role;")
        p_cur.execute("BEGIN;")
        p_cur.execute("""
            INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id)
            VALUES ('VC-ROLE-TEST', 'Supplier Role Test', 'pending', 101) RETURNING id;
        """)
        p_pkg_id = p_cur.fetchone()[0]
        p_cur.execute("""
            INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc)
            VALUES (%s, '3000E280116060000219ABCDEFAA');
        """, (p_pkg_id,))
        p_cur.execute("COMMIT;")
        p_cur.execute("RESET ROLE;")
        results["Role P-01 (service_role INSERT capability)"] = "PASS"

        # P-02: authenticated DILARANG INSERT langsung
        p02_code = None
        try:
            p_cur.execute("SET ROLE authenticated;")
            p_cur.execute("BEGIN;")
            p_cur.execute("INSERT INTO public.pengiriman_linen (verification_code, supplier) VALUES ('VC-ILLEGAL', 'Bad');")
            p_cur.execute("COMMIT;")
        except psycopg2.Error as err:
            p02_code = err.pgcode
            conn1.rollback()
        finally:
            p_cur.execute("RESET ROLE;")
        results["Role P-02 (authenticated direct INSERT blocked 42501)"] = "PASS" if p02_code == "42501" else f"FAIL: {p02_code}"

        # P-03: authenticated DILARANG membaca verification_code
        p03_code = None
        try:
            p_cur.execute("SET ROLE authenticated;")
            p_cur.execute("SELECT verification_code FROM public.pengiriman_linen;")
            p_cur.fetchall()
        except psycopg2.Error as err:
            p03_code = err.pgcode
            conn1.rollback()
        finally:
            p_cur.execute("RESET ROLE;")
        results["Role P-03 (authenticated verification_code masked 42501)"] = "PASS" if p03_code == "42501" else f"FAIL: {p03_code}"

        # P-04: anon DILARANG menjalankan claim_package RPC
        p04_code = None
        try:
            p_cur.execute("SET ROLE anon;")
            p_cur.execute("SELECT public.claim_package(1, 'op', 'inst', 'vc', 'hash', 101);")
        except psycopg2.Error as err:
            p04_code = err.pgcode
            conn1.rollback()
        finally:
            p_cur.execute("RESET ROLE;")
        results["Role P-04 (anon RPC execute blocked 42501)"] = "PASS" if p04_code == "42501" else f"FAIL: {p04_code}"

        # P-05: authenticated DAPAT menjalankan RPC yang diizinkan (P0-03 & P1-01 Fix)
        p_cur.execute("SET ROLE authenticated;")
        p_cur.execute("SET request.jwt.claim.sub = %s;", (str(fixtures.get("USER_A_RS_A")),))
        p_cur.execute("SELECT public.get_package_info(%s);", (p_pkg_id,))
        p05_info = p_cur.fetchone()[0]
        conn1.commit()
        p_cur.execute("RESET ROLE;")
        p_cur.close()
        assert_idle_connection(conn1, "conn1 (P-05)")

        p05_ok = (
            isinstance(p05_info, dict)
            and p05_info.get("status") == "ok"
            and p05_info.get("package_id") == p_pkg_id
            and p05_info.get("hospital_id") == 101
            and isinstance(p05_info.get("manifest_items"), list)
            and len(p05_info.get("manifest_items")) > 0
            and "verification_code" not in p05_info
        )
        results["Role P-05 (authenticated RPC execution authorized)"] = "PASS" if p05_ok else f"FAIL: {p05_info}"


        # =====================================================================
        # SKENARIO 1: Supplier uncommitted INSERT vs Claim (P0-01 & P0-02 Fix)
        # =====================================================================
        logger.info("Menjalankan Skenario 1: Supplier uncommitted INSERT vs Claim...")
        pkg1_id = fixtures.get("SCEN1_PKG")
        user_a_uuid = fixtures.get("USER_A_RS_A")
        rs_a_id = fixtures.get("RS_A")
        evt_c1_inserted = threading.Event()
        s1_claim_res = [None]
        s1_err = [None, None]
        s1_blocked_observed = [False]

        # P0-02 Fix: Administrator / Python computes canonical manifest hash & prepares parameters
        scen1_items = [
            {"epc": "3000E280116060000219ABCDEF01", "kategori": "Sprei", "nama_linen": "Sprei Standar"},
            {"epc": "3000E280116060000219ABCDEF11", "kategori": "Sprei", "nama_linen": "Sprei Tambahan 1"}
        ]
        s1_vc = pkg_admin_data[pkg1_id]["verification_code"]
        s1_expected_hash = compute_canonical_manifest_hash(scen1_items)

        def s1_conn1():
            try:
                c1 = conn1.cursor()
                c1.execute("SET ROLE service_role;")
                c1.execute("BEGIN;")
                c1.execute("""
                    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
                    VALUES (%s, '3000E280116060000219ABCDEF11', 'Sprei', 'Sprei Tambahan 1');
                """, (pkg1_id,))
                evt_c1_inserted.set()
                time.sleep(0.6)
                c1.execute("COMMIT;")
                c1.execute("RESET ROLE;")
                c1.close()
            except Exception as e:
                conn1.rollback()
                s1_err[0] = e

        def s1_conn2():
            try:
                if not evt_c1_inserted.wait(timeout=5.0):
                    raise TimeoutError("Conn1 tidak berhasil melakukan insert dalam 5 detik.")
                c2 = conn2.cursor()
                c2.execute("SET ROLE authenticated;")
                c2.execute("SET request.jwt.claim.sub = %s;", (str(user_a_uuid),))
                # P0-01: Explicit BEGIN
                c2.execute("BEGIN;")
                c2.execute("""
                    SELECT public.claim_package(
                        %s, 'op_scen1', 'inst_node_1',
                        %s, %s, %s
                    );
                """, (pkg1_id, s1_vc, s1_expected_hash, rs_a_id))
                res = c2.fetchone()[0]
                s1_claim_res[0] = res

                # P0-01: Validasi JSON SUCCESS & Explicit COMMIT
                if res.get("status") == "SUCCESS":
                    c2.execute("COMMIT;")
                else:
                    c2.execute("ROLLBACK;")
                c2.execute("RESET ROLE;")
                c2.close()
            except Exception as e:
                conn2.rollback()
                s1_err[1] = e

        t1 = threading.Thread(target=s1_conn1, name="s1_thread_conn1")
        t2 = threading.Thread(target=s1_conn2, name="s1_thread_conn2")
        t1.start()
        t2.start()

        if wait_for_blocking(monitor_conn, pid2, pid1, timeout_seconds=3.0):
            s1_blocked_observed[0] = True

        # P1-02: Safe join thread
        s1_t1_alive = not safe_join_thread(t1, conn1, timeout=10.0)
        s1_t2_alive = not safe_join_thread(t2, conn2, timeout=10.0)

        # P0-01: Assert koneksi IDLE (tidak INTRANS)
        assert_idle_connection(conn1, "conn1 (Skenario 1)")
        assert_idle_connection(conn2, "conn2 (Skenario 1)")

        # P0-01: Monitor membaca status claimed dan claimed_by_operation_id
        c_chk = monitor_conn.cursor()
        c_chk.execute("SELECT status, claimed_by_operation_id FROM public.pengiriman_linen WHERE id = %s;", (pkg1_id,))
        pkg1_row = c_chk.fetchone()
        c_chk.close()

        claim_json = s1_claim_res[0] or {}
        s1_json_ok = (claim_json.get("status") == "SUCCESS" and claim_json.get("code") in ("CLAIM_SUCCESS", "ALREADY_CLAIMED_BY_SELF"))
        s1_db_ok = (pkg1_row and pkg1_row[0] == "claimed" and pkg1_row[1] == "op_scen1")
        s1_threads_ok = not (s1_t1_alive or s1_t2_alive)

        if s1_blocked_observed[0] and s1_json_ok and s1_db_ok and s1_threads_ok and not any(s1_err):
            results["Skenario 1 (Supplier uncommitted INSERT vs Claim)"] = "PASS"
            actual_sqlstates["Skenario 1"] = "00000 (BLOCKED_THEN_SUCCESS)"
        else:
            results["Skenario 1 (Supplier uncommitted INSERT vs Claim)"] = f"FAIL (blocked={s1_blocked_observed[0]}, json={claim_json}, err={s1_err})"
            actual_sqlstates["Skenario 1"] = "FAILED"

        # =====================================================================
        # SKENARIO 2: Claim uncommitted FOR UPDATE vs Supplier INSERT (P0-02 Fix)
        # =====================================================================
        logger.info("Menjalankan Skenario 2: Claim uncommitted FOR UPDATE vs Supplier INSERT...")
        pkg2_id = fixtures.get("SCEN2_PKG")
        evt_c1_claimed = threading.Event()
        s2_err = [None, None]
        s2_blocked_observed = [False]
        s2_rejected_code = [None]

        # P0-02 Fix: Administrator pre-fetches verification_code & manifest_hash
        s2_vc = pkg_admin_data[pkg2_id]["verification_code"]
        s2_hash = pkg_admin_data[pkg2_id]["manifest_hash"]

        def s2_conn1():
            try:
                c1 = conn1.cursor()
                c1.execute("SET ROLE authenticated;")
                c1.execute("SET request.jwt.claim.sub = %s;", (str(user_a_uuid),))
                c1.execute("BEGIN;")
                c1.execute("""
                    SELECT public.claim_package(
                        %s, 'op_scen2', 'inst_node_1',
                        %s, %s, %s
                    );
                """, (pkg2_id, s2_vc, s2_hash, rs_a_id))
                res = c1.fetchone()[0]
                if res.get("status") != "SUCCESS":
                    raise RuntimeError(f"Claim gagal pada Skenario 2: {res}")
                evt_c1_claimed.set()
                time.sleep(0.6)
                c1.execute("COMMIT;")
                c1.execute("RESET ROLE;")
                c1.close()
            except Exception as e:
                conn1.rollback()
                s2_err[0] = e

        def s2_conn2():
            try:
                if not evt_c1_claimed.wait(timeout=5.0):
                    raise TimeoutError("Conn1 tidak berhasil claim dalam 5 detik.")
                c2 = conn2.cursor()
                c2.execute("SET ROLE service_role;")
                c2.execute("BEGIN;")
                c2.execute("""
                    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
                    VALUES (%s, '3000E280116060000219ABCDEF22', 'Selimut', 'Selimut Tambahan 2');
                """, (pkg2_id,))
                c2.execute("COMMIT;")
                c2.execute("RESET ROLE;")
                c2.close()
            except psycopg2.Error as e:
                s2_rejected_code[0] = e.pgcode
                conn2.rollback()
            except Exception as e:
                conn2.rollback()
                s2_err[1] = e

        t1 = threading.Thread(target=s2_conn1, name="s2_thread_conn1")
        t2 = threading.Thread(target=s2_conn2, name="s2_thread_conn2")
        t1.start()
        t2.start()

        if wait_for_blocking(monitor_conn, pid2, pid1, timeout_seconds=3.0):
            s2_blocked_observed[0] = True

        safe_join_thread(t1, conn1, timeout=10.0)
        safe_join_thread(t2, conn2, timeout=10.0)

        assert_idle_connection(conn1, "conn1 (Skenario 2)")
        assert_idle_connection(conn2, "conn2 (Skenario 2)")

        c_chk2 = monitor_conn.cursor()
        c_chk2.execute("SELECT status FROM public.pengiriman_linen WHERE id = %s;", (pkg2_id,))
        pkg2_status = c_chk2.fetchone()[0]
        c_chk2.execute("SELECT COUNT(*) FROM public.detail_pengiriman_linen WHERE pengiriman_id = %s AND epc = '3000E280116060000219ABCDEF22';", (pkg2_id,))
        pkg2_item_count = c_chk2.fetchone()[0]
        c_chk2.close()

        s2_ok = (s2_blocked_observed[0] and s2_rejected_code[0] == "23514" and pkg2_status == "claimed" and pkg2_item_count == 0)
        results["Skenario 2 (Claim FOR UPDATE vs Supplier INSERT)"] = "PASS" if s2_ok else f"FAIL (blocked={s2_blocked_observed[0]}, code={s2_rejected_code[0]}, status={pkg2_status}, count={pkg2_item_count})"
        actual_sqlstates["Skenario 2"] = s2_rejected_code[0] or "NONE"

        # =====================================================================
        # SKENARIO 3: UPDATE detail immutability terpisah (P0-03 & P1-05 Fix)
        # =====================================================================
        logger.info("Menjalankan Skenario 3: UPDATE detail immutability pada paket claimed...")
        pkg3_id = fixtures.get("SCEN3_PKG")
        c3 = conn1.cursor()
        c3.execute("SET ROLE service_role;")
        s3_code = None
        try:
            c3.execute("BEGIN;")
            c3.execute("UPDATE public.detail_pengiriman_linen SET nama_linen = 'Modif Ilegal' WHERE pengiriman_id = %s;", (pkg3_id,))
            c3.execute("COMMIT;")
        except psycopg2.Error as e:
            s3_code = e.pgcode
            conn1.rollback()
        finally:
            c3.execute("RESET ROLE;")
        c3.close()
        assert_idle_connection(conn1, "conn1 (Skenario 3)")

        c_chk3 = monitor_conn.cursor()
        c_chk3.execute("SELECT nama_linen FROM public.detail_pengiriman_linen WHERE pengiriman_id = %s;", (pkg3_id,))
        pkg3_nama = c_chk3.fetchone()[0]
        c_chk3.close()

        s3_ok = (s3_code == "23514" and pkg3_nama == "Handuk Mandi")
        results["Skenario 3 (UPDATE detail immutability trigger - Claimed)"] = "PASS" if s3_ok else f"FAIL (code={s3_code}, nama={pkg3_nama})"
        actual_sqlstates["Skenario 3"] = s3_code or "NONE"

        # =====================================================================
        # SKENARIO 3B: UPDATE detail pada paket pending (P1-05 Kontrak Supplier)
        # =====================================================================
        logger.info("Menjalankan Skenario 3B: UPDATE detail immutability pada paket pending...")
        c3b = conn1.cursor()
        c3b.execute("SET ROLE service_role;")
        s3b_code = None
        try:
            c3b.execute("BEGIN;")
            c3b.execute("UPDATE public.detail_pengiriman_linen SET nama_linen = 'Modif Pending' WHERE pengiriman_id = %s;", (p_pkg_id,))
            c3b.execute("COMMIT;")
        except psycopg2.Error as e:
            s3b_code = e.pgcode
            conn1.rollback()
        finally:
            c3b.execute("RESET ROLE;")
        c3b.close()
        assert_idle_connection(conn1, "conn1 (Skenario 3B)")

        s3b_ok = (s3b_code == "23514")
        results["Skenario 3B (UPDATE detail immutability trigger - Pending)"] = "PASS" if s3b_ok else f"FAIL (code={s3b_code})"

        # =====================================================================
        # SKENARIO 4: DELETE detail immutability terpisah (P0-03 Fix)
        # =====================================================================
        logger.info("Menjalankan Skenario 4: DELETE detail immutability pada paket claimed...")
        pkg4_id = fixtures.get("SCEN4_PKG")
        c4 = conn1.cursor()
        c4.execute("SET ROLE service_role;")
        s4_code = None
        try:
            c4.execute("BEGIN;")
            c4.execute("DELETE FROM public.detail_pengiriman_linen WHERE pengiriman_id = %s;", (pkg4_id,))
            c4.execute("COMMIT;")
        except psycopg2.Error as e:
            s4_code = e.pgcode
            conn1.rollback()
        finally:
            c4.execute("RESET ROLE;")
        c4.close()
        assert_idle_connection(conn1, "conn1 (Skenario 4)")

        c_chk4 = monitor_conn.cursor()
        c_chk4.execute("SELECT COUNT(*) FROM public.detail_pengiriman_linen WHERE pengiriman_id = %s;", (pkg4_id,))
        pkg4_cnt = c_chk4.fetchone()[0]
        c_chk4.close()

        s4_ok = (s4_code == "23514" and pkg4_cnt == 1)
        results["Skenario 4 (DELETE detail immutability trigger)"] = "PASS" if s4_ok else f"FAIL (code={s4_code}, count={pkg4_cnt})"
        actual_sqlstates["Skenario 4"] = s4_code or "NONE"

        # =====================================================================
        # SKENARIO 5: Lock Ordering Ascending (P1 Fix: observasi blocking)
        # =====================================================================
        logger.info("Menjalankan Skenario 5: Lock Ordering Ascending...")
        pkg5_id = fixtures.get("SCEN5_PKG_A")
        pkg6_id = fixtures.get("SCEN5_PKG_B")
        low_id, high_id = sorted([pkg5_id, pkg6_id])
        evt_s5_c1_locked = threading.Event()
        s5_err = [None, None]
        s5_blocked_observed = [False]

        def s5_s1():
            try:
                c = conn1.cursor()
                c.execute("BEGIN;")
                c.execute("SELECT * FROM public.pengiriman_linen WHERE id IN (%s, %s) ORDER BY id ASC FOR UPDATE;", (low_id, high_id))
                evt_s5_c1_locked.set()
                time.sleep(0.5)
                c.execute("COMMIT;")
                c.close()
            except Exception as e:
                conn1.rollback()
                s5_err[0] = e

        def s5_s2():
            try:
                if not evt_s5_c1_locked.wait(timeout=5.0):
                    raise TimeoutError("Conn1 tidak berhasil lock dalam 5 detik.")
                c = conn2.cursor()
                c.execute("BEGIN;")
                c.execute("SELECT * FROM public.pengiriman_linen WHERE id IN (%s, %s) ORDER BY id ASC FOR UPDATE;", (low_id, high_id))
                c.execute("COMMIT;")
                c.close()
            except Exception as e:
                conn2.rollback()
                s5_err[1] = e

        t1 = threading.Thread(target=s5_s1, name="s5_thread_conn1")
        t2 = threading.Thread(target=s5_s2, name="s5_thread_conn2")
        t1.start()
        t2.start()

        if wait_for_blocking(monitor_conn, pid2, pid1, timeout_seconds=3.0):
            s5_blocked_observed[0] = True

        safe_join_thread(t1, conn1, timeout=10.0)
        safe_join_thread(t2, conn2, timeout=10.0)

        assert_idle_connection(conn1, "conn1 (Skenario 5)")
        assert_idle_connection(conn2, "conn2 (Skenario 5)")

        s5_ok = (s5_blocked_observed[0] and not any(s5_err))
        results["Skenario 5 (Ascending lock order deadlock-free)"] = "PASS" if s5_ok else f"FAIL (blocked={s5_blocked_observed[0]}, err={s5_err})"
        actual_sqlstates["Skenario 5"] = "00000 (NO_DEADLOCK)"

        # =====================================================================
        # SKENARIO 6: Cross-Lock Deadlock Detection 40P01 (P1-03 Fix)
        # =====================================================================
        logger.info("Menjalankan Skenario 6: Cross-Lock Deadlock Detection (40P01)...")
        b6 = threading.Barrier(2)
        s6_results = {"session_1": None, "session_2": None}
        s6_errors = {"session_1": None, "session_2": None}
        s6_committed = {"session_1": False, "session_2": False}

        def s6_s1():
            try:
                c = conn1.cursor()
                c.execute("BEGIN;")
                c.execute("SELECT * FROM public.pengiriman_linen WHERE id = %s FOR UPDATE;", (low_id,))
                b6.wait(timeout=5.0)
                time.sleep(0.1)
                c.execute("SELECT * FROM public.pengiriman_linen WHERE id = %s FOR UPDATE;", (high_id,))
                c.execute("COMMIT;")
                c.close()
                s6_results["session_1"] = "COMMIT_SUCCESS"
                s6_committed["session_1"] = True
            except psycopg2.Error as e:
                s6_errors["session_1"] = e.pgcode
                s6_results["session_1"] = f"PG_ERROR_{e.pgcode}"
                conn1.rollback()
            except Exception as e:
                s6_results["session_1"] = f"EXCEPTION_{type(e).__name__}"
                conn1.rollback()

        def s6_s2():
            try:
                c = conn2.cursor()
                c.execute("BEGIN;")
                c.execute("SELECT * FROM public.pengiriman_linen WHERE id = %s FOR UPDATE;", (high_id,))
                b6.wait(timeout=5.0)
                time.sleep(0.1)
                c.execute("SELECT * FROM public.pengiriman_linen WHERE id = %s FOR UPDATE;", (low_id,))
                c.execute("COMMIT;")
                c.close()
                s6_results["session_2"] = "COMMIT_SUCCESS"
                s6_committed["session_2"] = True
            except psycopg2.Error as e:
                s6_errors["session_2"] = e.pgcode
                s6_results["session_2"] = f"PG_ERROR_{e.pgcode}"
                conn2.rollback()
            except Exception as e:
                s6_results["session_2"] = f"EXCEPTION_{type(e).__name__}"
                conn2.rollback()

        t1 = threading.Thread(target=s6_s1, name="s6_thread_conn1")
        t2 = threading.Thread(target=s6_s2, name="s6_thread_conn2")
        t1.start()
        t2.start()

        s6_t1_alive = not safe_join_thread(t1, conn1, timeout=10.0)
        s6_t2_alive = not safe_join_thread(t2, conn2, timeout=10.0)

        assert_idle_connection(conn1, "conn1 (Skenario 6)")
        assert_idle_connection(conn2, "conn2 (Skenario 6)")

        committed_count = sum(1 for v in s6_committed.values() if v is True)
        deadlock_count = sum(1 for v in s6_errors.values() if v == "40P01")
        other_errors = [v for v in s6_errors.values() if v is not None and v != "40P01"]
        no_dangling_threads = not (s6_t1_alive or s6_t2_alive)

        s6_ok = (committed_count == 1 and deadlock_count == 1 and len(other_errors) == 0 and no_dangling_threads)
        results["Skenario 6 (Deadlock detection 40P01 - Per-session audit)"] = "PASS" if s6_ok else f"FAIL (committed={committed_count}, deadlock={deadlock_count}, other={other_errors}, res={s6_results})"
        actual_sqlstates["Skenario 6"] = "40P01" if deadlock_count == 1 else "FAILED"

        # =====================================================================
        # SKENARIO 7: Concurrent Duplicate Clean EPC INSERT (P0-02 & P0-03 Fix)
        # =====================================================================
        logger.info("Menjalankan Skenario 7: Unique Functional Index Concurrency (Event-based)...")
        pkg7_id = fixtures.get("SCEN7_PKG")
        evt_s7_c1_inserted = threading.Event()
        s7_blocked_observed = [False]
        s7_c2_code = [None]
        s7_c2_constraint = [None]
        s7_err = [None, None]

        def s7_conn1():
            try:
                c1 = conn1.cursor()
                c1.execute("SET ROLE service_role;")
                c1.execute("BEGIN;")
                c1.execute("""
                    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc)
                    VALUES (%s, '3000E280116060000219ABCDEF99');
                """, (pkg7_id,))
                evt_s7_c1_inserted.set()
                time.sleep(0.6)
                c1.execute("COMMIT;")
                c1.execute("RESET ROLE;")
                c1.close()
            except Exception as e:
                conn1.rollback()
                s7_err[0] = e

        def s7_conn2():
            try:
                if not evt_s7_c1_inserted.wait(timeout=5.0):
                    raise TimeoutError("Conn1 Skenario 7 tidak selesai insert dalam 5 detik.")
                c2 = conn2.cursor()
                c2.execute("SET ROLE service_role;")
                c2.execute("BEGIN;")
                c2.execute("""
                    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc)
                    VALUES (%s, 'E280116060000219ABCDEF99');
                """, (pkg7_id,))
                c2.execute("COMMIT;")
                c2.execute("RESET ROLE;")
                c2.close()
            except psycopg2.Error as e:
                s7_c2_code[0] = e.pgcode
                s7_c2_constraint[0] = getattr(e.diag, "constraint_name", None)
                conn2.rollback()
            except Exception as e:
                conn2.rollback()
                s7_err[1] = e

        t1 = threading.Thread(target=s7_conn1, name="s7_thread_conn1")
        t2 = threading.Thread(target=s7_conn2, name="s7_thread_conn2")
        t1.start()
        t2.start()

        if wait_for_blocking(monitor_conn, pid2, pid1, timeout_seconds=3.0):
            s7_blocked_observed[0] = True

        safe_join_thread(t1, conn1, timeout=10.0)
        safe_join_thread(t2, conn2, timeout=10.0)

        assert_idle_connection(conn1, "conn1 (Skenario 7)")
        assert_idle_connection(conn2, "conn2 (Skenario 7)")

        c_chk7 = monitor_conn.cursor()
        c_chk7.execute("""
            SELECT COUNT(*) FROM public.detail_pengiriman_linen
            WHERE pengiriman_id = %s AND public.clean_epc(epc) = 'E280116060000219ABCDEF99';
        """, (pkg7_id,))
        final_epc_count = c_chk7.fetchone()[0]
        c_chk7.close()

        s7_ok = (s7_blocked_observed[0] and s7_c2_code[0] == "23505" and s7_c2_constraint[0] == "uq_detail_pengiriman_linen_epc_clean" and final_epc_count == 1)
        results["Skenario 7 (Unique functional index clean_epc 23505)"] = "PASS" if s7_ok else f"FAIL (blocked={s7_blocked_observed[0]}, code={s7_c2_code[0]}, constraint={s7_c2_constraint[0]}, count={final_epc_count})"
        actual_sqlstates["Skenario 7"] = s7_c2_code[0] or "NONE"

        # =====================================================================
                # =====================================================================
        # OPTION A ACCEPTANCE TEST SUITE (A s.d. J)
        # =====================================================================
        logger.info("Menjalankan Option A Acceptance Test Suite (A s.d. J)...")
        mt_cur = conn1.cursor()
        mt_cur.execute("SET ROLE authenticated;")

        pkg_rsa_id = fixtures.get("PKG_RS_A")
        pkg_rsb_id = fixtures.get("PKG_RS_B")
        pkg_unbound_id = fixtures.get("PKG_UNBOUND")

        vc_rsa = pkg_admin_data[pkg_rsa_id]["verification_code"]
        hash_rsa = pkg_admin_data[pkg_rsa_id]["manifest_hash"]

        vc_rsb = pkg_admin_data[pkg_rsb_id]["verification_code"]
        hash_rsb = pkg_admin_data[pkg_rsb_id]["manifest_hash"]

        vc_unbound = pkg_admin_data[pkg_unbound_id]["verification_code"]
        hash_unbound = pkg_admin_data[pkg_unbound_id]["manifest_hash"]

        user_a1_uuid = fixtures.get("USER_A_RS_A")
        user_a2_uuid = fixtures.get("USER_A2_RS_A")
        user_b_uuid = fixtures.get("USER_B_RS_B")
        user_inact_uuid = fixtures.get("USER_INACTIVE_MEM")
        user_nomem_uuid = fixtures.get("USER_NO_MEMBERSHIP")
        user_deact_uuid = fixtures.get("USER_DEACTIVATED")

        # ---------------------------------------------------------------------
        # Acceptance Test A: Akun RS A mengambil paket RS A -> SUCCESS
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_a', 'inst_node_a1',
                %s, %s, 101
            );
        """, (pkg_rsa_id, vc_rsa, hash_rsa))
        test_a_res = mt_cur.fetchone()[0]
        if test_a_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test A)")
        test_a_ok = (test_a_res.get("status") == "SUCCESS" and test_a_res.get("code") == "CLAIM_SUCCESS")
        results["Acceptance Test A (Akun RS A mengambil paket RS A: SUCCESS)"] = "PASS" if test_a_ok else f"FAIL: {test_a_res}"

        # ---------------------------------------------------------------------
        # Acceptance Test B: Akun RS A mengambil paket RS B dengan kode benar -> FORBIDDEN (CROSS_TENANT_ACCESS_DENIED)
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_b', 'inst_node_a1',
                %s, %s, 101
            );
        """, (pkg_rsb_id, vc_rsb, hash_rsb))
        test_b_res = mt_cur.fetchone()[0]
        if test_b_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test B)")
        test_b_ok = (test_b_res.get("status") == "FORBIDDEN" and test_b_res.get("code") == "CROSS_TENANT_ACCESS_DENIED")

        # Verifikasi integritas status dan audit paket RS B di database tidak berubah
        c_ver = monitor_conn.cursor()
        c_ver.execute("SELECT status, hospital_id, claimed_at FROM public.pengiriman_linen WHERE id = %s;", (pkg_rsb_id,))
        rsb_row_b = c_ver.fetchone()
        c_ver.close()
        test_b_audit_preserved = (rsb_row_b[0] == "pending" and rsb_row_b[1] == 202 and rsb_row_b[2] is None)

        results["Acceptance Test B (RS A ambil paket RS B dengan kode benar: FORBIDDEN)"] = (
            "PASS" if (test_b_ok and test_b_audit_preserved) else f"FAIL: {test_b_res} (audit_preserved={test_b_audit_preserved})"
        )

        # ---------------------------------------------------------------------
        # Acceptance Test C: Akun RS A mengambil paket RS B dengan kode salah -> FORBIDDEN identik, tanpa bocor validitas kode
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_c', 'inst_node_a1',
                'WRONG_VERIF_CODE_9999', %s, 101
            );
        """, (pkg_rsb_id, hash_rsb))
        test_c_res = mt_cur.fetchone()[0]
        if test_c_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test C)")

        # Evaluasi paritas respons antara Test B dan Test C (Mencegah Timing/Oracle Leakage)
        test_c_identical = (
            test_c_res.get("status") == test_b_res.get("status") == "FORBIDDEN"
            and test_c_res.get("code") == test_b_res.get("code") == "CROSS_TENANT_ACCESS_DENIED"
        )
        c_ver = monitor_conn.cursor()
        c_ver.execute("SELECT status, hospital_id, claimed_at FROM public.pengiriman_linen WHERE id = %s;", (pkg_rsb_id,))
        rsb_row_c = c_ver.fetchone()
        c_ver.close()
        test_c_audit_preserved = (rsb_row_c[0] == "pending" and rsb_row_c[1] == 202 and rsb_row_c[2] is None)

        results["Acceptance Test C (RS A ambil paket RS B kode salah: FORBIDDEN identik, no code leak)"] = (
            "PASS" if (test_c_identical and test_c_audit_preserved) else f"FAIL: {test_c_res}"
        )

        # ---------------------------------------------------------------------
        # Acceptance Test D & P1-01 Lock Contention Prevention (Anti-DoS):
        # 1. RS A (conn1) mencoba claim paket RS B dalam transaksi terbuka.
        # 2. Karena validasi tenant menolak SEBELUM SELECT FOR UPDATE, RS A
        #    TIDAK memperoleh row lock pada paket RS B.
        # 3. RS B (conn2) mengeksekusi claim_package dengan statement_timeout aktif (3000ms).
        #    Jika RS B tertahan akibat lock RS A:
        #    - Catat SQLSTATE 57014.
        #    - Tandai Anti-DoS test FAIL.
        #    - Rollback transaksi, tutup koneksi/cursor.
        #    - Jangan biarkan runner menggantung.
        # 4. Jika RS A ditolak sebelum row lock:
        #    - RS B dapat Claim tanpa menunggu.
        #    - Validasi status SUCCESS.
        #    - Validasi final database state via monitor_conn.
        # ---------------------------------------------------------------------
        # Langkah 1: RS A memanggil claim_package pada paket RS B
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_d_dos_attempt', 'inst_node_a1',
                %s, %s, 101
            );
        """, (pkg_rsb_id, vc_rsb, hash_rsb))
        dos_rsa_res = mt_cur.fetchone()[0]
        # JANGAN COMMIT ATAU ROLLBACK conn1 DULU! Transaksi conn1 tetap aktif.

        # Langkah 2: RS B (conn2) mengeksekusi claim_package dengan statement_timeout
        c2_dos = conn2.cursor()
        c2_dos.execute("SET statement_timeout = '3000ms';")
        c2_dos.execute("SET ROLE authenticated;")
        c2_dos.execute("SET request.jwt.claim.sub = %s;", (str(user_b_uuid),))

        dos_timeout_occurred = False
        dos_sqlstate = None
        test_d_res = None

        try:
            c2_dos.execute("""
                SELECT public.claim_package(
                    %s, 'op_opt_d', 'inst_node_b1',
                    %s, %s, 202
                );
            """, (pkg_rsb_id, vc_rsb, hash_rsb))
            test_d_res = c2_dos.fetchone()[0]
            if test_d_res.get("status") == "SUCCESS":
                conn2.commit()
            else:
                conn2.rollback()
        except psycopg2.Error as pge:
            conn2.rollback()
            dos_timeout_occurred = True
            dos_sqlstate = pge.pgcode or "57014"
            logger.error("RS B tertahan oleh row lock RS A! SQLSTATE=%s: %s", dos_sqlstate, pge)
        finally:
            try:
                c2_dos.execute("SET statement_timeout = 0;")
            except Exception:
                pass
            c2_dos.close()
            assert_idle_connection(conn2, "conn2 (Test D)")

        # Langkah 3: Rollback transaksi terbuka conn1
        conn1.rollback()
        assert_idle_connection(conn1, "conn1 (post-dos-test)")

        # Langkah 4: Validasi final database state via monitor_conn
        c_mon_d = monitor_conn.cursor()
        c_mon_d.execute("""
            SELECT status, hospital_id, claimed_by_user_id
            FROM public.pengiriman_linen
            WHERE id = %s;
        """, (pkg_rsb_id,))
        final_rsb_row = c_mon_d.fetchone()
        c_mon_d.close()

        final_db_ok = (
            final_rsb_row is not None
            and final_rsb_row[0] == "claimed"
            and final_rsb_row[1] == 202
            and str(final_rsb_row[2]) == str(user_b_uuid)
        )

        dos_rsa_blocked = (dos_rsa_res.get("status") == "FORBIDDEN" and dos_rsa_res.get("code") == "CROSS_TENANT_ACCESS_DENIED")
        test_d_ok = (test_d_res is not None and test_d_res.get("status") == "SUCCESS" and test_d_res.get("code") == "CLAIM_SUCCESS")

        anti_dos_pass = dos_rsa_blocked and not dos_timeout_occurred and test_d_ok and final_db_ok
        if dos_timeout_occurred:
            results["P0-02 Lock Contention Prevention (Anti-DoS: RS A tidak menahan lock paket RS B)"] = (
                f"FAIL (RS B tertahan oleh lock RS A: SQLSTATE={dos_sqlstate})"
            )
            actual_sqlstates["Anti-DoS Lock Contention"] = dos_sqlstate
        else:
            results["P0-02 Lock Contention Prevention (Anti-DoS: RS A tidak menahan lock paket RS B)"] = (
                "PASS" if anti_dos_pass else f"FAIL (rsa_res={dos_rsa_res}, rsb_res={test_d_res}, db_ok={final_db_ok})"
            )

        results["Acceptance Test D (Akun RS B mengambil paket RS B: SUCCESS)"] = (
            "PASS" if (test_d_ok and final_db_ok) else f"FAIL: {test_d_res} (db_ok={final_db_ok})"
        )

        # ---------------------------------------------------------------------
        # Acceptance Test E: Akun tanpa membership aktif -> FORBIDDEN
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_nomem_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_e', 'inst_node_e',
                %s, %s, 101
            );
        """, (pkg_rsa_id, vc_rsa, hash_rsa))
        test_e_res = mt_cur.fetchone()[0]
        if test_e_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test E)")
        test_e_ok = (test_e_res.get("status") == "FORBIDDEN" and test_e_res.get("code") == "INVALID_HOSPITAL_MEMBERSHIP")
        results["Acceptance Test E (Akun tanpa membership aktif: FORBIDDEN)"] = "PASS" if test_e_ok else f"FAIL: {test_e_res}"

        # ---------------------------------------------------------------------
        # Acceptance Test F: Membership nonaktif -> FORBIDDEN
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_inact_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_f', 'inst_node_f',
                %s, %s, 101
            );
        """, (pkg_rsa_id, vc_rsa, hash_rsa))
        test_f_res = mt_cur.fetchone()[0]
        if test_f_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test F)")
        test_f_ok = (test_f_res.get("status") == "FORBIDDEN" and test_f_res.get("code") == "INVALID_HOSPITAL_MEMBERSHIP")
        results["Acceptance Test F (Membership nonaktif: FORBIDDEN)"] = "PASS" if test_f_ok else f"FAIL: {test_f_res}"

        # ---------------------------------------------------------------------
        # Acceptance Test G: Paket tanpa hospital_id -> PACKAGE_NOT_BOUND
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_g', 'inst_node_a1',
                %s, %s, 101
            );
        """, (pkg_unbound_id, vc_unbound, hash_unbound))
        test_g_res = mt_cur.fetchone()[0]
        if test_g_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test G)")

        c_ver = monitor_conn.cursor()
        c_ver.execute("SELECT status, hospital_id, claimed_at FROM public.pengiriman_linen WHERE id = %s;", (pkg_unbound_id,))
        unbound_row = c_ver.fetchone()
        c_ver.close()
        test_g_ok = (test_g_res.get("status") == "FORBIDDEN" and test_g_res.get("code") == "PACKAGE_NOT_BOUND")
        test_g_unbound_preserved = (unbound_row[0] == "pending" and unbound_row[1] is None and unbound_row[2] is None)

        results["Acceptance Test G (Paket tanpa hospital_id: PACKAGE_NOT_BOUND)"] = (
            "PASS" if (test_g_ok and test_g_unbound_preserved) else f"FAIL: {test_g_res} (unbound_preserved={test_g_unbound_preserved})"
        )

        # ---------------------------------------------------------------------
        # Acceptance Test H: Dua pegawai RS A dapat mengakses paket RS A sesuai hak akses
        # ---------------------------------------------------------------------
        # Pegawai 1 (user_a1_uuid) sudah mengklaim paket PKG_RS_A di Test A.
        # Pegawai 2 (user_a2_uuid) sekarang mengakses get_package_info dan get_package_claim_status
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a2_uuid),))
        mt_cur.execute("SELECT public.get_package_info(%s);", (pkg_rsa_id,))
        info_a2 = mt_cur.fetchone()[0]
        mt_cur.execute("SELECT public.get_package_claim_status(%s, 101);", (pkg_rsa_id,))
        status_a2 = mt_cur.fetchone()[0]
        conn1.commit()
        assert_idle_connection(conn1, "conn1 (Test H)")

        test_h_info_ok = (
            isinstance(info_a2, dict)
            and info_a2.get("status") == "ok"
            and info_a2.get("package_id") == pkg_rsa_id
            and isinstance(info_a2.get("manifest_items"), list)
            and len(info_a2.get("manifest_items")) > 0
            and "verification_code" not in info_a2
        )
        test_h_status_ok = (
            isinstance(status_a2, dict)
            and status_a2.get("status") == "FOUND"
            and status_a2.get("status_paket") == "claimed"
            and status_a2.get("hospital_id") == 101
        )
        test_h_ok = test_h_info_ok and test_h_status_ok
        results["Acceptance Test H (Dua pegawai RS A dapat mengakses paket RS A)"] = (
            "PASS" if test_h_ok else f"FAIL: (info_ok={test_h_info_ok}, status_ok={test_h_status_ok})"
        )

        # ---------------------------------------------------------------------
        # Acceptance Test I: Pegawai RS A tidak dapat membaca manifest RS B
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("SELECT public.get_package_info(%s);", (pkg_rsb_id,))
        test_i_res = mt_cur.fetchone()[0]
        conn1.commit()
        assert_idle_connection(conn1, "conn1 (Test I)")

        test_i_ok = (
            test_i_res.get("status") == "FORBIDDEN"
            and test_i_res.get("code") == "CROSS_TENANT_ACCESS_DENIED"
            and "manifest_items" not in test_i_res
        )
        results["Acceptance Test I (Pegawai RS A tidak dapat membaca manifest RS B)"] = (
            "PASS" if test_i_ok else f"FAIL: {test_i_res}"
        )

        # ---------------------------------------------------------------------
        # Acceptance Test J: Pegawai yang dinonaktifkan tidak dapat Claim maupun Confirm
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_deact_uuid),))
        mt_cur.execute("""
            SELECT public.claim_package(
                %s, 'op_opt_j', 'inst_node_j',
                %s, %s, 101
            );
        """, (pkg_rsa_id, vc_rsa, hash_rsa))
        test_j_claim_res = mt_cur.fetchone()[0]
        if test_j_claim_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()

        mt_cur.execute("""
            SELECT public.confirm_package(
                %s, 101, 'op_opt_j_conf', 'inst_node_j',
                %s, %s
            );
        """, (pkg_rsa_id, vc_rsa, hash_rsa))
        test_j_conf_res = mt_cur.fetchone()[0]
        if test_j_conf_res.get("status") == "SUCCESS":
            conn1.commit()
        else:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Test J)")

        test_j_claim_blocked = (
            test_j_claim_res.get("status") == "FORBIDDEN"
            and test_j_claim_res.get("code") == "INVALID_HOSPITAL_MEMBERSHIP"
        )
        test_j_conf_blocked = (
            test_j_conf_res.get("status") == "FORBIDDEN"
            and test_j_conf_res.get("code") == "INVALID_HOSPITAL_MEMBERSHIP"
        )
        test_j_ok = test_j_claim_blocked and test_j_conf_blocked
        results["Acceptance Test J (Pegawai dinonaktifkan tidak dapat Claim/Confirm)"] = (
            "PASS" if test_j_ok else f"FAIL: (claim={test_j_claim_res}, conf={test_j_conf_res})"
        )

        # ---------------------------------------------------------------------
        # Multi-Tenant Read & Column Privilege Assertions
        # ---------------------------------------------------------------------
        mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
        mt_cur.execute("SELECT id, hospital_id FROM public.pengiriman_linen;")
        visible_rows = mt_cur.fetchall()
        conn1.commit()
        assert_idle_connection(conn1, "conn1 (RLS)")
        visible_ids = {r[0] for r in visible_rows}
        visible_hosp_ids = {r[1] for r in visible_rows if r[1] is not None}

        rls_has_rows = len(visible_rows) > 0
        rls_rsa_visible = pkg_rsa_id in visible_ids
        rls_rsb_hidden = (pkg_rsb_id not in visible_ids) and (202 not in visible_hosp_ids)
        rls_ok = rls_has_rows and rls_rsa_visible and rls_rsb_hidden
        results["Multi-Tenant RLS (Cross-Tenant Read Isolation)"] = (
            "PASS" if rls_ok else f"FAIL (rows={len(visible_rows)}, rsa_visible={rls_rsa_visible}, rsb_hidden={rls_rsb_hidden})"
        )

        # Verification Code Masking
        v_mask_code = None
        try:
            mt_cur.execute("SET request.jwt.claim.sub = %s;", (str(user_a1_uuid),))
            mt_cur.execute("SELECT verification_code FROM public.pengiriman_linen;")
            mt_cur.fetchall()
        except psycopg2.Error as err:
            v_mask_code = err.pgcode
        finally:
            conn1.rollback()
        assert_idle_connection(conn1, "conn1 (Masking)")
        results["Multi-Tenant Masking (Verification Code Column Masked 42501)"] = (
            "PASS" if v_mask_code == "42501" else f"FAIL (code={v_mask_code})"
        )

        # ---------------------------------------------------------------------
        # P0-01 GUC Bypass Rejection Test: NULL hospital_id Always Rejected (23502)
        # Mencoba bypass dengan setting custom GUC clients.allow_unbound_fixture.
        # Immutability trigger wajib menolak keras dengan error 23502.
        # ---------------------------------------------------------------------
        guc_cur = monitor_conn.cursor()
        guc_rejected = False
        guc_code = None
        guc_msg = ""
        try:
            guc_cur.execute("SET clients.allow_unbound_fixture = 'on';")
            guc_cur.execute("""
                INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
                VALUES ('VC-BYPASS-TEST', 'Supplier Gamma', 'pending', NULL, clock_timestamp());
            """)
            monitor_conn.commit()
        except psycopg2.Error as err:
            monitor_conn.rollback()
            guc_rejected = True
            guc_code = err.pgcode
            guc_msg = str(err)
        finally:
            guc_cur.close()
        assert_idle_connection(monitor_conn, "monitor_conn (GUC Bypass)")

        p0_01_ok = guc_rejected and guc_code == "23502" and "hospital_id wajib diisi" in guc_msg
        results["P0-01 Eliminasi Custom GUC Bypass (NULL hospital_id ditolak 23502)"] = (
            "PASS" if p0_01_ok else f"FAIL (rejected={guc_rejected}, code={guc_code}, msg={guc_msg})"
        )

        mt_cur.execute("RESET ROLE;")
        mt_cur.close()
        assert_idle_connection(conn1, "conn1 (post-multi-tenant)")
    finally:
        conn1.close()
        conn2.close()
        monitor_conn.close()

    return {
        "all_passed": all(v.startswith("PASS") for v in results.values()),
        "scenario_results": results,
        "actual_sqlstates": actual_sqlstates,
    }


def run_tests():
    print("=" * 80)
    print("RUNNER KONKURENSI MULTI-SESI & MULTI-TENANT POSTGRESQL R2.10.4")
    print("=" * 80)

    is_docker = check_docker_available()
    if not is_docker:
        print("\n[STATUS] SKIPPED (Docker engine tidak tersedia pada host environment)")
        print("[REASON] Host Windows saat ini tidak memiliki instalasi Docker CLI / daemon aktif.")
        print("[MANDAT KEBIJAKAN AUDIT R2.10.4 (SECTION 5 & SECTION 16)]:")
        print("   - Runner DILARANG menghubungkan ke PGHOST remote.")
        print("   - Runner DILARANG menerima koneksi ke Supabase staging atau produksi.")
        print("   - Jika Docker tidak tersedia, hasilkan SKIPPED dengan EXIT CODE 2.")
        print("   - SKIPPED BUKAN PASS dan TIDAK BOLEH dianggap sebagai PASS.")
        print("   - Sistem dinyatakan: NOT READY FOR ISOLATED END-TO-END TESTING sampai")
        print("     pengujian konkurensi dijalankan pada runner yang memiliki PostgreSQL 15+.")
        print("=" * 80)
        print("HASIL RUNNER: SKIPPED | EXIT CODE 2")
        print("=" * 80)
        sys.exit(2)

    if not HAS_PSYCOPG2:
        print("[FAIL] Modul psycopg2 tidak terpasang di environment.")
        sys.exit(1)

    ddl_candidates = [
        "database_migrations/cloud/07_package_communication_rpc_consolidated_r2_10_4.sql",
        "database_migrations/cloud/07_package_communication_rpc_consolidated_r2_10_4.sql",
    ]
    ddl_path = next((cand for cand in ddl_candidates if os.path.exists(cand)), None)

    harness_candidates = [
        "database_migrations/cloud/test_concurrency_harness_r2_10_4.sql",
        "database_migrations/cloud/test_concurrency_harness_r2_10_4.sql",
    ]
    harness_path = next((cand for cand in harness_candidates if os.path.exists(cand)), None)

    port = find_free_port()
    db_pass = f"disp_pass_{uuid.uuid4().hex[:12]}"
    disposable_db_name = f"clients_disposable_{uuid.uuid4().hex[:12]}"
    container_name = f"clients_pg_concurrency_{uuid.uuid4().hex[:8]}"

    logger.info("Memulai container PostgreSQL 15+ disposable: %s pada port 127.0.0.1:%d", container_name, port)
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
        logger.info("Menunggu kesiapan engine PostgreSQL...")
        if not wait_for_postgres(container_name, timeout_seconds=30):
            raise TimeoutError("PostgreSQL container tidak siap dalam waktu 30 detik.")

        admin_conn = psycopg2.connect(host="127.0.0.1", port=port, user="postgres", password=db_pass, dbname="postgres")
        admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = admin_conn.cursor()
        cur.execute(sql.SQL("CREATE DATABASE {} WITH ENCODING 'UTF8'").format(sql.Identifier(disposable_db_name)))
        cur.close()
        admin_conn.close()

        res = run_full_concurrency_suite(port, db_pass, disposable_db_name, ddl_path, harness_path)
        print("\n" + "=" * 80)
        print("HASIL PENGUJIAN KONKURENSI & MULTI-TENANT R2.10.4")
        print("=" * 80)
        for k, v in res["scenario_results"].items():
            print(f"- {k}: {v}")
        print("=" * 80)
        if res["all_passed"]:
            print("STATUS: PASS (100% Skenario Konkurensi & Multi-Tenant Lolos)")
            sys.exit(0)
        else:
            print("STATUS: FAIL (Terdapat skenario konkurensi/multi-tenant yang gagal)")
            sys.exit(1)

    except Exception as exc:
        logger.error("Terjadi kegagalan selama runner konkurensi: %s", exc)
        sys.exit(1)
    finally:
        logger.info("Menghancurkan container PostgreSQL disposable: %s", container_name)
        subprocess.run(["docker", "rm", "-f", container_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    run_tests()

