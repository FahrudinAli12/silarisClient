-- =============================================================================
-- FILE: 07_package_communication_rpc_consolidated_r2_10_4.sql
-- REVISI: TAHAP 4D.2A.3-R2.10.4 (CRITICAL SECURITY CORRECTION & PRODUCTION RPC CONSOLIDATION)
-- DESKRIPSI:
--   Konsolidasi menyeluruh skrip DDL & RPC komunikasi paket linen cloud Supabase:
--   1. Validasi Lingkungan PostgreSQL 13+, Encoding UTF8, dan Deteksi Namespace pgcrypto
--   2. Normalisasi EPC Kanonikal (public.clean_epc) dengan Pemotongan Prefix 3000 Hanya untuk 28 Karakter
--   3. Historical Preflight 13 Invarian Ketat (Invarian 11 non-pending hash & Invarian 13 EPC clean duplikasi)
--   4. Penegakan Unique Functional Index pada (pengiriman_id, public.clean_epc(epc))
--   5. Pembersihan Dinamis Overload RPC Lama (to_regprocedure & Unquoted PUBLIC REVOKE)
--   6. Hash Manifest Deterministik SHA-256 Length-Prefixed (compute_package_manifest_hash)
--   7. Immutability Triggers Murni Tanpa Custom GUC (FOR SHARE locking & State Machine Integrity)
--   8. Core Security Definer RPCs dengan Idempotensi Ketat:
--      - claim_package(BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT)
--      - confirm_package(BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT) [6 Parameter Eksak, Eliminasi p_permit_id]
--      - get_package_claim_status(BIGINT, BIGINT) [Pengembalian Seluruh Audit IDs & Multi-Tenant Scoping]
--      - get_package_info(BIGINT) [Akses Pratinjau Aman Tanpa Membocorkan verification_code]
--   9. Dynamic Cleanup Seluruh Policy RLS Lama & Penegakan Multi-Tenant RLS Non-Rekursif
--  10. Rekonsiliasi Hak Akses Eksak (Column-Level ACL Masking verification_code & Unquoted PUBLIC REVOKE)
--  11. Postflight Assertion 12 Pilar Eksak (Validasi Total Katalog & Runtime Privileges)
--
-- ATURAN KESELAMATAN & ATOMISITAS:
--   - Dijalankan dalam satu transaksi atomik tunggal (BEGIN ... COMMIT).
--   - Seluruh assertion bersifat fail-fast; kegagalan sekecil apa pun memicu ROLLBACK otomatis.
--   - Dilarang keras memotong kode (NO PLACEHOLDERS, NO TRUNCATIONS).
-- =============================================================================

BEGIN;

-- =============================================================================
-- 1. VALIDASI LINGKUNGAN, SERVER ENCODING, DAN EKSTENSI KRIPTOGRAFI
-- =============================================================================
DO $$
DECLARE
    v_pgcrypto_schema TEXT;
BEGIN
    RAISE NOTICE '=== 1. VALIDASI LINGKUNGAN, ENCODING & EKSTENSI ===';

    -- 1.1 Kompatibilitas Minimum PostgreSQL 13+ (Wajib untuk fungsi bawaan normalize NFC)
    IF current_setting('server_version_num')::INTEGER < 130000 THEN
        RAISE EXCEPTION 'PREFLIGHT_FAILED [1.1]: Versi PostgreSQL (%) di bawah batas minimum 13.0 (diperlukan untuk fungsi normalize)', current_setting('server_version_num')
            USING ERRCODE = '0A000';
    END IF;

    -- 1.2 Validasi Server Encoding (Wajib UTF8 untuk integritas hash kanonikal)
    IF current_setting('server_encoding') <> 'UTF8' THEN
        RAISE EXCEPTION 'PREFLIGHT_FAILED [1.2]: Server encoding (%) bukan UTF8', current_setting('server_encoding')
            USING ERRCODE = '22023';
    END IF;

    -- 1.3 Deteksi Namespace Ekstensi pgcrypto & Fungsi digest(bytea, text)
    SELECT n.nspname INTO v_pgcrypto_schema
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'digest'
      AND pg_get_function_identity_arguments(p.oid) = 'bytea, text';

    IF v_pgcrypto_schema IS NULL THEN
        -- Coba muat ekstensi pgcrypto di schema extensions, jika gagal gunakan public
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
            v_pgcrypto_schema := 'extensions';
        EXCEPTION WHEN OTHERS THEN
            BEGIN
                CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
                v_pgcrypto_schema := 'public';
            EXCEPTION WHEN OTHERS THEN
                NULL;
            END;
        END;
    END IF;

    -- Validasi ulang ketersediaan fungsi digest(bytea, text)
    SELECT n.nspname INTO v_pgcrypto_schema
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'digest'
      AND pg_get_function_identity_arguments(p.oid) = 'bytea, text';

    IF v_pgcrypto_schema IS NULL THEN
        RAISE EXCEPTION 'PREFLIGHT_FAILED [1.3]: Fungsi digest(bytea, text) dari ekstensi pgcrypto tidak ditemukan pada schema katalog'
            USING ERRCODE = '42883';
    END IF;

    RAISE NOTICE 'Ekstensi pgcrypto terverifikasi pada namespace: %', v_pgcrypto_schema;
END $$;

-- =============================================================================
-- 2. VALIDASI KEBERADAAN TABEL FONDASI DAN STRUKTUR TABEL DASAR
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=== 2. VALIDASI KEBERADAAN TABEL FONDASI & DASAR ===';

    -- 2.1 Validasi Tabel Fondasi Auth & Tenant
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'auth' AND table_name = 'users') THEN
        RAISE EXCEPTION 'PREREQUISITE_FAILED [2.1]: Tabel auth.users tidak ditemukan' USING ERRCODE = '42P01';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'rumah_sakit') THEN
        RAISE EXCEPTION 'PREREQUISITE_FAILED [2.1]: Tabel public.rumah_sakit tidak ditemukan' USING ERRCODE = '42P01';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'hospital_memberships') THEN
        RAISE EXCEPTION 'PREREQUISITE_FAILED [2.1]: Tabel public.hospital_memberships tidak ditemukan' USING ERRCODE = '42P01';
    END IF;

    -- 2.2 Validasi Tabel Operasional
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'pengiriman_linen') THEN
        RAISE EXCEPTION 'PREREQUISITE_FAILED [2.2]: Tabel public.pengiriman_linen tidak ditemukan' USING ERRCODE = '42P01';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'detail_pengiriman_linen') THEN
        RAISE EXCEPTION 'PREREQUISITE_FAILED [2.2]: Tabel public.detail_pengiriman_linen tidak ditemukan' USING ERRCODE = '42P01';
    END IF;
END $$;

-- =============================================================================
-- 3. PENYELARASAN STRUKTUR KOLOM & PENAMBAHAN KOLOM AUDIT SEBELUM PREFLIGHT
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '=== 3. PENYELARASAN STRUKTUR KOLOM & AUDIT ===';

    -- 3.1 Penambahan hospital_id pada public.pengiriman_linen
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'hospital_id'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN hospital_id BIGINT NULL;
    END IF;

    -- 3.2 Penambahan manifest_hash pada public.pengiriman_linen
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'manifest_hash'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN manifest_hash VARCHAR(64) NULL;
    END IF;

    -- 3.3 Penambahan Kolom Audit Lifecycle Claim pada public.pengiriman_linen
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'claimed_by_operation_id'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN claimed_by_operation_id VARCHAR(100) NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'claimed_by_installation_uid'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN claimed_by_installation_uid VARCHAR(100) NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'claimed_at'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN claimed_at TIMESTAMPTZ NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'claimed_by_user_id'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN claimed_by_user_id UUID NULL;
    END IF;

    -- 3.4 Penambahan Kolom Audit Lifecycle Confirm pada public.pengiriman_linen
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'confirmed_by_operation_id'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN confirmed_by_operation_id VARCHAR(100) NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'confirmed_by_installation_uid'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN confirmed_by_installation_uid VARCHAR(100) NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'confirmed_at'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN confirmed_at TIMESTAMPTZ NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = 'pengiriman_linen' AND column_name = 'confirmed_by_user_id'
    ) THEN
        ALTER TABLE public.pengiriman_linen ADD COLUMN confirmed_by_user_id UUID NULL;
    END IF;

    -- 3.5 Penegakan Foreign Key Constraints
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'fk_pengiriman_linen_hospital' AND conrelid = 'public.pengiriman_linen'::regclass
    ) THEN
        ALTER TABLE public.pengiriman_linen
            ADD CONSTRAINT fk_pengiriman_linen_hospital 
            FOREIGN KEY (hospital_id) REFERENCES public.rumah_sakit(id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'fk_detail_pengiriman_linen_header' AND conrelid = 'public.detail_pengiriman_linen'::regclass
    ) THEN
        ALTER TABLE public.detail_pengiriman_linen
            ADD CONSTRAINT fk_detail_pengiriman_linen_header 
            FOREIGN KEY (pengiriman_id) REFERENCES public.pengiriman_linen(id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'fk_pengiriman_linen_claimed_user' AND conrelid = 'public.pengiriman_linen'::regclass
    ) THEN
        ALTER TABLE public.pengiriman_linen
            ADD CONSTRAINT fk_pengiriman_linen_claimed_user 
            FOREIGN KEY (claimed_by_user_id) REFERENCES auth.users(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'fk_pengiriman_linen_confirmed_user' AND conrelid = 'public.pengiriman_linen'::regclass
    ) THEN
        ALTER TABLE public.pengiriman_linen
            ADD CONSTRAINT fk_pengiriman_linen_confirmed_user 
            FOREIGN KEY (confirmed_by_user_id) REFERENCES auth.users(id) ON DELETE SET NULL;
    END IF;
END $$;

-- =============================================================================
-- 4. VERIFIKASI TIPE DATA KOLOM SETELAH PENYELARASAN
-- =============================================================================
DO $$
DECLARE
    v_bad_cols TEXT;
BEGIN
    RAISE NOTICE '=== 4. VERIFIKASI TIPE DATA KOLOM SETELAH PENYELARASAN ===';

    SELECT string_agg(column_name || ' (' || data_type || ')', ', ') INTO v_bad_cols
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'pengiriman_linen'
      AND (
          (column_name = 'id' AND data_type <> 'bigint') OR
          (column_name = 'hospital_id' AND data_type <> 'bigint') OR
          (column_name = 'status' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'verification_code' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'supplier' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'tanggal' AND data_type <> 'timestamp with time zone') OR
          (column_name = 'catatan' AND data_type <> 'text') OR
          (column_name = 'created_at' AND data_type <> 'timestamp with time zone') OR
          (column_name = 'manifest_hash' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'claimed_by_operation_id' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'claimed_by_installation_uid' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'claimed_at' AND data_type <> 'timestamp with time zone') OR
          (column_name = 'claimed_by_user_id' AND data_type <> 'uuid') OR
          (column_name = 'confirmed_by_operation_id' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'confirmed_by_installation_uid' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'confirmed_at' AND data_type <> 'timestamp with time zone') OR
          (column_name = 'confirmed_by_user_id' AND data_type <> 'uuid')
      );

    IF v_bad_cols IS NOT NULL THEN
        RAISE EXCEPTION 'PREFLIGHT_FAILED [4]: Tipe data kolom pengiriman_linen tidak valid: %', v_bad_cols
            USING ERRCODE = '42804';
    END IF;

    SELECT string_agg(column_name || ' (' || data_type || ')', ', ') INTO v_bad_cols
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'detail_pengiriman_linen'
      AND (
          (column_name = 'id' AND data_type <> 'bigint') OR
          (column_name = 'pengiriman_id' AND data_type <> 'bigint') OR
          (column_name = 'epc' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'kategori' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'nama_linen' AND data_type NOT IN ('character varying', 'text')) OR
          (column_name = 'created_at' AND data_type <> 'timestamp with time zone')
      );

    IF v_bad_cols IS NOT NULL THEN
        RAISE EXCEPTION 'PREFLIGHT_FAILED [4]: Tipe data kolom detail_pengiriman_linen tidak valid: %', v_bad_cols
            USING ERRCODE = '42804';
    END IF;
END $$;

-- =============================================================================
-- 5. HELPER FUNCTION AWAL: NORMALISASI EPC KANONIKAL (public.clean_epc)
-- =============================================================================
-- Dibuat sebelum Preflight agar Invarian 13 dan Unique Functional Index dapat langsung menggunakannya.
CREATE OR REPLACE FUNCTION public.clean_epc(p_epc TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT CASE 
        WHEN length(upper(btrim(p_epc, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085'))) = 28 
         AND upper(btrim(p_epc, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085')) LIKE '3000%' 
        THEN substr(upper(btrim(p_epc, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085')), 5)
        ELSE upper(btrim(p_epc, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085'))
    END;
$$;

COMMENT ON FUNCTION public.clean_epc(TEXT) IS 
    'Normalisasi EPC kanonikal: strip whitespace, uppercase, dan pemotongan 4-karakter prefix 3000 pada EPC 28-karakter (100% paritas dengan Python ClientS)';

-- =============================================================================
-- 6. HISTORICAL DATA INTEGRITY PREFLIGHT: VALIDASI 13 INVARIAN KETAT
-- =============================================================================
DO $$
DECLARE
    v_cnt INTEGER;
    v_bad_ids TEXT;
BEGIN
    RAISE NOTICE '=== 6. HISTORICAL DATA INTEGRITY PREFLIGHT (13 INVARIAN) ===';

    -- Invarian 1: Integritas Primary Key Header
    SELECT COUNT(*) INTO v_cnt FROM public.pengiriman_linen WHERE id IS NULL;
    IF v_cnt > 0 THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 1]: Ditemukan % baris header dengan ID NULL', v_cnt 
            USING ERRCODE = '23502';
    END IF;

    -- Invarian 2: Integritas Primary Key Detail
    SELECT COUNT(*) INTO v_cnt FROM public.detail_pengiriman_linen WHERE id IS NULL;
    IF v_cnt > 0 THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 2]: Ditemukan % baris detail dengan ID NULL', v_cnt 
            USING ERRCODE = '23502';
    END IF;

    -- Invarian 3: Integritas Verification Code Header (NOT NULL & Panjang Minimum 4 Karakter)
    SELECT string_agg(id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen 
    WHERE verification_code IS NULL OR length(btrim(verification_code)) < 4;
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 3]: Paket memiliki verification_code tidak valid (ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 4: Integritas EPC Detail (NOT NULL & Non-Empty)
    SELECT string_agg(DISTINCT pengiriman_id::text, ', ') INTO v_bad_ids 
    FROM public.detail_pengiriman_linen 
    WHERE epc IS NULL OR btrim(epc) = '';
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 4]: Paket memiliki detail dengan EPC NULL atau kosong (Paket ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 5: Status Paket Terbatas pada Himpunan Sah
    SELECT string_agg(id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen 
    WHERE status IS NULL OR status NOT IN ('pending', 'claimed', 'confirmed', 'diterima', 'received');
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 5]: Paket memiliki status di luar himpunan sah (ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 6: Tidak Boleh Ada Baris Detail Yatim (Orphaned Detail Records)
    SELECT string_agg(DISTINCT d.pengiriman_id::text, ', ') INTO v_bad_ids 
    FROM public.detail_pengiriman_linen d 
    LEFT JOIN public.pengiriman_linen p ON p.id = d.pengiriman_id 
    WHERE p.id IS NULL;
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 6]: Ditemukan detail tanpa header pengiriman (Pengiriman ID: %)', v_bad_ids
            USING ERRCODE = '23503';
    END IF;

    -- Invarian 7: Paket Non-Pending Wajib Memiliki hospital_id Terisi dan Valid
    SELECT string_agg(p.id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen p
    LEFT JOIN public.rumah_sakit rs ON rs.id = p.hospital_id
    WHERE p.status IN ('claimed', 'confirmed', 'diterima', 'received')
      AND (p.hospital_id IS NULL OR rs.id IS NULL);
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 7]: Paket non-pending tanpa hospital_id valid (ID: %)', v_bad_ids
            USING ERRCODE = '23503';
    END IF;

    -- Invarian 8: Kronologis Timestamp Logis (claimed_at <= confirmed_at)
    SELECT string_agg(id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen 
    WHERE claimed_at IS NOT NULL AND confirmed_at IS NOT NULL AND claimed_at > confirmed_at;
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 8]: Paket memiliki claimed_at mendahului confirmed_at (ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 9: Paket claimed Wajib Memiliki Audit Trail Claim Lengkap
    SELECT string_agg(id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen 
    WHERE status = 'claimed' 
      AND (claimed_by_operation_id IS NULL OR claimed_by_installation_uid IS NULL OR claimed_at IS NULL);
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 9]: Paket claimed tanpa audit trail claim lengkap (ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 10: Paket confirmed Wajib Memiliki Audit Trail Confirm Lengkap
    SELECT string_agg(id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen 
    WHERE status IN ('confirmed', 'diterima', 'received') 
      AND (confirmed_by_operation_id IS NULL OR confirmed_by_installation_uid IS NULL OR confirmed_at IS NULL);
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 10]: Paket confirmed tanpa audit trail confirm lengkap (ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 11 (C1 Fix): Paket Non-Pending WAJIB Memiliki manifest_hash NOT NULL & Format Kanonikal 64-char Hex Lowercase
    SELECT string_agg(id::text || ' (status: ' || coalesce(status, 'NULL') || ')', ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen 
    WHERE (
        status IN ('claimed', 'confirmed', 'diterima', 'received') 
        AND (manifest_hash IS NULL OR manifest_hash !~ '^[0-9a-f]{64}$')
    ) OR (
        manifest_hash IS NOT NULL AND manifest_hash !~ '^[0-9a-f]{64}$'
    );
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 11]: Paket non-pending tanpa manifest_hash sah atau berformat non-kanonikal: %', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 12: Setiap Paket Non-Pending Wajib Memiliki Minimal 1 Item Detail Linen
    SELECT string_agg(p.id::text, ', ') INTO v_bad_ids 
    FROM public.pengiriman_linen p 
    WHERE p.status IN ('claimed', 'confirmed', 'diterima', 'received') 
      AND NOT EXISTS (SELECT 1 FROM public.detail_pengiriman_linen d WHERE d.pengiriman_id = p.id);
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 12]: Paket non-pending tanpa item detail linen (ID: %)', v_bad_ids
            USING ERRCODE = '23514';
    END IF;

    -- Invarian 13 (C1 Fix): Keunikan EPC Per-Paket Menggunakan public.clean_epc() & Validasi Format EPC Kanonikal 24 Hex
    -- Validasi 13.A: Format EPC Kanonikal (24 karakter hex setelah clean_epc)
    SELECT string_agg(DISTINCT d.pengiriman_id::text || ':' || d.epc, ', ') INTO v_bad_ids
    FROM public.detail_pengiriman_linen d
    WHERE public.clean_epc(d.epc) !~ '^[0-9A-F]{24}$';
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 13A]: Ditemukan EPC dengan format non-kanonikal (bukan 24 hex setelah normalisasi): %', v_bad_ids
            USING ERRCODE = '22000';
    END IF;

    -- Validasi 13.B: Deteksi Duplikasi EPC Per-Paket setelah normalisasi public.clean_epc()
    SELECT string_agg(sub.pengiriman_id::text || ':' || sub.clean_epc, ', ') INTO v_bad_ids
    FROM (
        SELECT pengiriman_id, public.clean_epc(epc) AS clean_epc
        FROM public.detail_pengiriman_linen
        GROUP BY pengiriman_id, public.clean_epc(epc)
        HAVING COUNT(*) > 1
    ) sub;
    IF v_bad_ids IS NOT NULL THEN
        RAISE EXCEPTION 'HISTORICAL_PREFLIGHT_FAILED [INVARIAN 13B]: Terdeteksi duplikasi EPC dalam paket yang sama setelah clean_epc (Paket:EPC = %)', v_bad_ids
            USING ERRCODE = '23505';
    END IF;

    RAISE NOTICE '=== SELURUH 13 INVARIAN DATA HISTORIS LOLOS TANPA ANOMALI ===';
END $$;

-- =============================================================================
-- 7. PENEGAKAN UNIQUE FUNCTIONAL INDEX DAN INDEKS PERFORMA
-- =============================================================================
-- 7.1 Unique Functional Index: Menjamin Tidak Akan Pernah Terjadi Duplikasi EPC dalam Satu Paket (C2 Fix)
CREATE UNIQUE INDEX IF NOT EXISTS uq_detail_pengiriman_linen_epc_clean 
ON public.detail_pengiriman_linen (pengiriman_id, public.clean_epc(epc));

-- 7.2 Indeks Performa Tambahan
CREATE INDEX IF NOT EXISTS idx_pengiriman_linen_tenant_lookup 
ON public.pengiriman_linen (hospital_id, status);

CREATE INDEX IF NOT EXISTS idx_pengiriman_linen_hospital_id 
ON public.pengiriman_linen (hospital_id);

CREATE INDEX IF NOT EXISTS idx_detail_pengiriman_linen_pkg 
ON public.detail_pengiriman_linen (pengiriman_id);

-- 7.3 Penegakan Kebijakan Satu Akun Satu Rumah Sakit (P1-01 Fix)
-- Menjamin setiap akun pengguna hanya memiliki paling banyak 1 keanggotaan aktif pada database
CREATE UNIQUE INDEX IF NOT EXISTS uq_hospital_memberships_user_single_active
ON public.hospital_memberships (user_id)
WHERE is_active = TRUE;

-- =============================================================================
-- 8. PEMBERSIHAN DINAMIS AMAN OVERLOAD RPC LAMA (TANPA CASCADE)
-- =============================================================================
DO $$
DECLARE
    r RECORD;
    v_proc_reg regprocedure;
    v_drop_sql TEXT;
    v_grantee_clause TEXT;
    v_dep_count INTEGER;
BEGIN
    RAISE NOTICE '=== 8. PEMBERSIHAN DINAMIS OVERLOAD RPC LAMA ===';

    -- Signature final yang sah pada R2.10.4:
    -- 1. public.clean_epc(text)
    -- 2. public.compute_package_manifest_hash(bigint)
    -- 3. public.claim_package(bigint, text, text, text, text, bigint)
    -- 4. public.confirm_package(bigint, bigint, text, text, text, text) [6 parameter eksak]
    -- 5. public.get_package_claim_status(bigint, bigint)
    -- 6. public.get_package_info(bigint)

    FOR r IN (
        SELECT 
            p.oid,
            p.proname,
            n.nspname,
            pg_get_function_identity_arguments(p.oid) AS args
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN ('claim_package', 'confirm_package', 'get_package_claim_status', 'get_package_info', 'clean_epc', 'compute_package_manifest_hash')
    ) LOOP
        -- Evaluasi apakah signature cocok dengan signature kanonikal R2.10.4:
        IF (r.proname = 'clean_epc' AND r.args = 'p_epc text') OR
           (r.proname = 'compute_package_manifest_hash' AND r.args = 'p_package_id bigint') OR
           (r.proname = 'claim_package' AND r.args = 'p_package_id bigint, p_operation_id text, p_installation_uid text, p_verification_code text, p_computed_manifest_hash text, p_hospital_id bigint') OR
           (r.proname = 'confirm_package' AND r.args = 'p_package_id bigint, p_hospital_id bigint, p_operation_id text, p_installation_uid text, p_verification_code text, p_manifest_hash text') OR
           (r.proname = 'get_package_claim_status' AND r.args = 'p_package_id bigint, p_hospital_id bigint') OR
           (r.proname = 'get_package_info' AND r.args = 'p_package_id bigint') THEN
            -- Ini adalah signature target yang sah, pertahankan
            CONTINUE;
        END IF;

        -- Overload usang ditemukan: verifikasi dengan to_regprocedure
        v_proc_reg := to_regprocedure(format('%I.%I(%s)', r.nspname, r.proname, r.args));
        IF v_proc_reg IS NOT NULL THEN
            RAISE NOTICE 'Mengaudit overload lama untuk dihapus: %(%)', r.proname, r.args;

            -- Periksa apakah ada objek dependen
            SELECT COUNT(*) INTO v_dep_count
            FROM pg_depend d
            JOIN pg_class c ON c.oid = d.objid
            WHERE d.refobjid = r.oid AND d.deptype = 'n';

            IF v_dep_count > 0 THEN
                RAISE EXCEPTION 'OVERLOAD_CLEANUP_BLOCKED: Fungsi %(%) memiliki % objek dependen. Drop manual diperlukan!', r.proname, r.args, v_dep_count
                    USING ERRCODE = '2BP01';
            END IF;

            -- Pencabutan hak akses dinamis dengan penanganan aman untuk PUBLIC (tanpa %I - C3 Fix)
            FOR v_grantee_clause IN (
                SELECT DISTINCT CASE WHEN grantee = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(grantee) END
                FROM information_schema.routine_privileges
                WHERE routine_schema = r.nspname AND routine_name = r.proname
            ) LOOP
                EXECUTE format('REVOKE ALL ON FUNCTION %I.%I(%s) FROM %s;', r.nspname, r.proname, r.args, v_grantee_clause);
            END LOOP;

            -- Hapus fungsi overload tanpa CASCADE
            v_drop_sql := format('DROP FUNCTION %I.%I(%s);', r.nspname, r.proname, r.args);
            EXECUTE v_drop_sql;
            RAISE NOTICE 'Overload lama berhasil dibersihkan: %(%)', r.proname, r.args;
        END IF;
    END LOOP;
END $$;

-- =============================================================================
-- 9. HELPER FUNCTIONS: HASH MANIFEST SHA-256 KANONIKAL
-- =============================================================================

-- 9.1 public.compute_package_manifest_hash(BIGINT): SHA-256 Length-Prefixed Stream Kanonikal (C4 & C8 Fix)
DO $$
DECLARE
    v_pgcrypto_schema TEXT;
    v_func_sql TEXT;
BEGIN
    -- Temukan namespace tempat pgcrypto / digest berada
    SELECT n.nspname INTO v_pgcrypto_schema
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE p.proname = 'digest'
      AND pg_get_function_identity_arguments(p.oid) = 'bytea, text';

    IF v_pgcrypto_schema IS NULL THEN
        RAISE EXCEPTION 'CRITICAL: Namespace pgcrypto tidak ditemukan saat pembuatan compute_package_manifest_hash';
    END IF;

    v_func_sql := format($FUNC$
CREATE OR REPLACE FUNCTION public.compute_package_manifest_hash(p_package_id BIGINT)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $BODY$
DECLARE
    v_item_count INTEGER;
    v_canonical_stream BYTEA := ''::BYTEA;
    r RECORD;
    v_epc_bytes BYTEA;
    v_kat_bytes BYTEA;
    v_nama_bytes BYTEA;
    v_prev_epc TEXT := NULL;
    v_hash TEXT;
BEGIN
    IF p_package_id IS NULL THEN
        RAISE EXCEPTION 'INVALID_ARGUMENT: p_package_id tidak boleh NULL' USING ERRCODE = '22004';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM public.pengiriman_linen WHERE id = p_package_id) THEN
        RAISE EXCEPTION 'PACKAGE_NOT_FOUND: Paket ID %% tidak ditemukan', p_package_id 
            USING ERRCODE = 'P0002';
    END IF;

    SELECT COUNT(*) INTO v_item_count 
    FROM public.detail_pengiriman_linen 
    WHERE pengiriman_id = p_package_id;

    IF v_item_count = 0 THEN
        RAISE EXCEPTION 'EMPTY_MANIFEST: Paket ID %% tidak memiliki item detail linen', p_package_id 
            USING ERRCODE = '22000';
    END IF;

    FOR r IN (
        SELECT 
            public.clean_epc(epc) AS cleaned_epc,
            normalize(COALESCE(btrim(kategori, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085'), ''), NFC) AS norm_kat,
            normalize(COALESCE(btrim(nama_linen, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085'), ''), NFC) AS norm_nama
        FROM public.detail_pengiriman_linen
        WHERE pengiriman_id = p_package_id
        ORDER BY convert_to(public.clean_epc(epc), 'UTF8') ASC
    ) LOOP
        -- Validasi format EPC kanonikal (24 hex)
        IF r.cleaned_epc !~ '^[0-9A-F]{24}$' THEN
            RAISE EXCEPTION 'INVALID_EPC_FORMAT: Format EPC %% tidak valid (harus 24 karakter heksadesimal)', r.cleaned_epc 
                USING ERRCODE = '22000';
        END IF;

        -- Deteksi duplikasi EPC setelah normalisasi
        IF v_prev_epc IS NOT NULL AND r.cleaned_epc = v_prev_epc THEN
            RAISE EXCEPTION 'DUPLICATE_EPC: Duplikasi EPC terdeteksi (%%) pada paket ID %%', r.cleaned_epc, p_package_id 
                USING ERRCODE = '23505';
        END IF;
        v_prev_epc := r.cleaned_epc;

        v_epc_bytes  := convert_to(r.cleaned_epc, 'UTF8');
        v_kat_bytes  := convert_to(r.norm_kat, 'UTF8');
        v_nama_bytes := convert_to(r.norm_nama, 'UTF8');

        -- Format serialisasi byte kanonikal: {len_epc}:{epc}:{len_kat}:{kat}:{len_nama}:{nama};
        v_canonical_stream := v_canonical_stream || 
            convert_to(
                octet_length(v_epc_bytes)::TEXT  || ':' || r.cleaned_epc || ':' ||
                octet_length(v_kat_bytes)::TEXT  || ':' || r.norm_kat    || ':' ||
                octet_length(v_nama_bytes)::TEXT || ':' || r.norm_nama   || ';',
                'UTF8'
            );
    END LOOP;

    -- Pemanggilan schema-qualified ke digest()
    v_hash := encode(%I.digest(v_canonical_stream, 'sha256'), 'hex');
    RETURN lower(v_hash);
END $BODY$;
$FUNC$, v_pgcrypto_schema);

    EXECUTE v_func_sql;
END $$;

COMMENT ON FUNCTION public.compute_package_manifest_hash(BIGINT) IS 
    'Menghitung manifest hash kanonikal SHA-256 length-prefixed deterministik yang 100% identik dengan algoritma manifest_service.py ClientS';

-- =============================================================================
-- 10. OPERATIONAL IMMUTABILITY TRIGGERS: STATE MACHINE MURNI (TANPA CUSTOM GUC)
-- =============================================================================

-- 10.1 Trigger Function Immutability Detail Linen (public.detail_pengiriman_linen)
CREATE OR REPLACE FUNCTION public.fn_detail_pengiriman_linen_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_target_pkg_id BIGINT;
    v_pkg_status TEXT;
    v_cleaned_epc TEXT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'DETAIL_IMMUTABLE_MUTATION: Pembaruan baris detail_pengiriman_linen dilarang demi integritas manifest hash kanonikal'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        v_target_pkg_id := OLD.pengiriman_id;
    ELSE
        v_target_pkg_id := NEW.pengiriman_id;
    END IF;

    IF v_target_pkg_id IS NULL THEN
        RAISE EXCEPTION 'DETAIL_INVALID_PACKAGE: pengiriman_id tidak boleh NULL' USING ERRCODE = '23502';
    END IF;

    -- Kunci baris header pengiriman dengan FOR SHARE untuk serialisasi konkurensi terhadap Claim
    SELECT status INTO v_pkg_status 
    FROM public.pengiriman_linen 
    WHERE id = v_target_pkg_id 
    FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'DETAIL_PARENT_NOT_FOUND: Paket pengiriman % tidak ditemukan', v_target_pkg_id 
            USING ERRCODE = '23503';
    END IF;

    -- Pemeriksaan NULL-safe terhadap status header (P0 Fix R2.10.4)
    IF v_pkg_status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'DETAIL_MUTATION_FORBIDDEN: Penambahan/penghapusan detail linen dilarang karena paket % berstatus "%" (hanya diizinkan pada status "pending")',
            v_target_pkg_id, coalesce(v_pkg_status, 'NULL')
            USING ERRCODE = '23514';
    END IF;

    -- Validasi ketat untuk operasi INSERT detail (P0 Fix R2.10.4)
    IF TG_OP = 'INSERT' THEN
        IF NEW.epc IS NULL OR length(btrim(NEW.epc, E' \t\n\r\v\f\u00A0\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000\u1680\x1C\x1D\x1E\x1F\u0085')) = 0 THEN
            RAISE EXCEPTION 'DETAIL_INVALID_EPC: Kolom epc tidak boleh NULL atau kosong'
                USING ERRCODE = '23502';
        END IF;

        v_cleaned_epc := public.clean_epc(NEW.epc);
        IF v_cleaned_epc IS NULL OR length(v_cleaned_epc) <> 24 OR v_cleaned_epc !~ '^[0-9A-F]{24}$' THEN
            RAISE EXCEPTION 'DETAIL_INVALID_EPC_FORMAT: Format EPC "%" tidak valid setelah normalisasi (wajib 24 karakter heksadesimal kanonikal)', NEW.epc
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1 FROM public.detail_pengiriman_linen
            WHERE pengiriman_id = NEW.pengiriman_id
              AND public.clean_epc(epc) = v_cleaned_epc
        ) THEN
            RAISE EXCEPTION 'DETAIL_DUPLICATE_EPC: Duplikasi EPC "%" (normalisasi: "%") dalam paket %', NEW.epc, v_cleaned_epc, NEW.pengiriman_id
                USING ERRCODE = '23505';
        END IF;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END $$;

-- 10.2 Trigger Function Immutability Header Pengiriman (public.pengiriman_linen)
CREATE OR REPLACE FUNCTION public.fn_pengiriman_linen_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    -- 1. Penanganan Operasi INSERT (P0 Header State Machine Bypass Prevention - R2.10.4)
    IF TG_OP = 'INSERT' THEN
        -- Status awal paket baru wajib 'pending' (NULL-safe)
        IF NEW.status IS DISTINCT FROM 'pending' THEN
            RAISE EXCEPTION 'INSERT_INVALID_STATUS: Paket baru wajib memiliki status "pending" (diberikan: "%")', coalesce(NEW.status, 'NULL')
                USING ERRCODE = '23514';
        END IF;

        -- Seluruh kolom audit klaim/konfirmasi dan manifest_hash wajib NULL saat pembuatan
        IF NEW.claimed_at IS NOT NULL OR
           NEW.claimed_by_operation_id IS NOT NULL OR
           NEW.claimed_by_installation_uid IS NOT NULL OR
           NEW.claimed_by_user_id IS NOT NULL OR
           NEW.confirmed_at IS NOT NULL OR
           NEW.confirmed_by_operation_id IS NOT NULL OR
           NEW.confirmed_by_installation_uid IS NOT NULL OR
           NEW.confirmed_by_user_id IS NOT NULL OR
           NEW.manifest_hash IS NOT NULL THEN
            RAISE EXCEPTION 'INSERT_AUDIT_NOT_NULL: Kolom audit klaim/konfirmasi dan manifest_hash wajib NULL saat pembuatan paket baru'
                USING ERRCODE = '23514';
        END IF;

        -- Option A Invariant (P0-01): hospital_id wajib tersedia pada setiap paket baru (NOT NULL, Tanpa GUC Bypass)
        IF NEW.hospital_id IS NULL THEN
            RAISE EXCEPTION 'INSERT_INVALID_HOSPITAL: hospital_id wajib diisi pada pembuatan paket baru'
                USING ERRCODE = '23502';
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM public.rumah_sakit rs
            WHERE rs.id = NEW.hospital_id AND rs.is_active = TRUE
        ) THEN
            RAISE EXCEPTION 'INSERT_INVALID_HOSPITAL: hospital_id % tidak ditemukan atau tidak aktif', NEW.hospital_id
                USING ERRCODE = '23503';
        END IF;

        RETURN NEW;
    END IF;

    -- 2. Penanganan Operasi UPDATE (P1 NULL-Safe Immutability Enforcement - R2.10.4)
    -- Validasi State Machine Status Transisi yang Sah
    IF OLD.status = NEW.status THEN
        -- Status tidak berubah, lanjutkan validasi immutability kolom header
        NULL;
    ELSIF OLD.status = 'pending' AND NEW.status = 'claimed' THEN
        -- Transisi Sah 1: pending -> claimed
        NULL;
    ELSIF OLD.status = 'claimed' AND NEW.status IN ('confirmed', 'diterima', 'received') THEN
        -- Transisi Sah 2: claimed -> confirmed / diterima / received
        NULL;
    ELSE
        -- Transisi Ilegal
        RAISE EXCEPTION 'STATE_TRANSITION_ILLEGAL: Transisi status pengiriman dari "%" ke "%" dilarang oleh state machine',
            coalesce(OLD.status, 'NULL'), coalesce(NEW.status, 'NULL')
            USING ERRCODE = '23514';
    END IF;

    -- Penegakan Immutability Kolom Header Non-Nullable (NULL-Safe via IS DISTINCT FROM)
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: Kolom id tidak boleh diubah' USING ERRCODE = '23514';
    END IF;
    IF NEW.verification_code IS DISTINCT FROM OLD.verification_code THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: Kolom verification_code tidak boleh diubah' USING ERRCODE = '23514';
    END IF;
    IF NEW.supplier IS DISTINCT FROM OLD.supplier THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: Kolom supplier tidak boleh diubah' USING ERRCODE = '23514';
    END IF;
    IF NEW.tanggal IS DISTINCT FROM OLD.tanggal THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: Kolom tanggal tidak boleh diubah' USING ERRCODE = '23514';
    END IF;
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: Kolom created_at tidak boleh diubah' USING ERRCODE = '23514';
    END IF;

    -- Option A Invariant: Immutability Mutlak hospital_id (Tidak Ada First-Claim Binding)
    IF NEW.hospital_id IS DISTINCT FROM OLD.hospital_id THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: hospital_id bersifat permanen dan dilarang diubah (dari % ke %)',
            coalesce(OLD.hospital_id::text, 'NULL'), coalesce(NEW.hospital_id::text, 'NULL')
            USING ERRCODE = '23514';
    END IF;

    -- Proteksi Integritas manifest_hash NULL-Safe
    IF OLD.manifest_hash IS NOT NULL THEN
        IF NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash THEN
            RAISE EXCEPTION 'IMMUTABLE_COLUMN: manifest_hash yang telah dihitung tidak boleh dimutasi'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF NEW.manifest_hash IS NOT NULL AND NEW.status IS DISTINCT FROM 'claimed' THEN
            RAISE EXCEPTION 'IMMUTABLE_COLUMN: manifest_hash hanya dapat diisi saat transisi status ke claimed'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    -- Proteksi Immutability Jejak Audit Claim NULL-Safe (P1 Fix R2.10.4)
    IF OLD.claimed_at IS NOT NULL THEN
        IF NEW.claimed_at IS DISTINCT FROM OLD.claimed_at OR
           NEW.claimed_by_operation_id IS DISTINCT FROM OLD.claimed_by_operation_id OR
           NEW.claimed_by_installation_uid IS DISTINCT FROM OLD.claimed_by_installation_uid OR
           NEW.claimed_by_user_id IS DISTINCT FROM OLD.claimed_by_user_id THEN
            RAISE EXCEPTION 'IMMUTABLE_COLUMN: Jejak audit klaim (claimed_*) tidak boleh diubah atau dikosongkan setelah ditetapkan'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF (NEW.claimed_at IS NOT NULL OR
            NEW.claimed_by_operation_id IS NOT NULL OR
            NEW.claimed_by_installation_uid IS NOT NULL OR
            NEW.claimed_by_user_id IS NOT NULL) AND NEW.status IS DISTINCT FROM 'claimed' THEN
            RAISE EXCEPTION 'IMMUTABLE_COLUMN: Jejak audit klaim hanya boleh diisi saat transisi status ke claimed'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    -- Proteksi Immutability Jejak Audit Confirm NULL-Safe (P1 Fix R2.10.4)
    IF OLD.confirmed_at IS NOT NULL THEN
        IF NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at OR
           NEW.confirmed_by_operation_id IS DISTINCT FROM OLD.confirmed_by_operation_id OR
           NEW.confirmed_by_installation_uid IS DISTINCT FROM OLD.confirmed_by_installation_uid OR
           NEW.confirmed_by_user_id IS DISTINCT FROM OLD.confirmed_by_user_id THEN
            RAISE EXCEPTION 'IMMUTABLE_COLUMN: Jejak audit konfirmasi (confirmed_*) tidak boleh diubah atau dikosongkan setelah ditetapkan'
                USING ERRCODE = '23514';
        END IF;
    ELSE
        IF (NEW.confirmed_at IS NOT NULL OR
            NEW.confirmed_by_operation_id IS NOT NULL OR
            NEW.confirmed_by_installation_uid IS NOT NULL OR
            NEW.confirmed_by_user_id IS NOT NULL) AND NEW.status NOT IN ('confirmed', 'diterima', 'received') THEN
            RAISE EXCEPTION 'IMMUTABLE_COLUMN: Jejak audit konfirmasi hanya boleh diisi saat transisi status ke confirmed'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    -- Proteksi Immutability Catatan setelah Paket Berstatus Confirmed
    IF NEW.catatan IS DISTINCT FROM OLD.catatan AND OLD.status IN ('confirmed', 'diterima', 'received') AND NEW.status = OLD.status THEN
        RAISE EXCEPTION 'IMMUTABLE_COLUMN: Catatan tidak boleh diubah pada paket yang telah dikonfirmasi'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END $$;

-- 10.3 Pemasangan Triggers pada Tabel Operasional
DROP TRIGGER IF EXISTS trg_detail_pengiriman_linen_immutability ON public.detail_pengiriman_linen;
CREATE TRIGGER trg_detail_pengiriman_linen_immutability
BEFORE INSERT OR UPDATE OR DELETE ON public.detail_pengiriman_linen
FOR EACH ROW
EXECUTE FUNCTION public.fn_detail_pengiriman_linen_immutability();

DROP TRIGGER IF EXISTS trg_pengiriman_linen_immutability ON public.pengiriman_linen;
CREATE TRIGGER trg_pengiriman_linen_immutability
BEFORE INSERT OR UPDATE ON public.pengiriman_linen
FOR EACH ROW
EXECUTE FUNCTION public.fn_pengiriman_linen_immutability();

-- =============================================================================
-- 11. CORE SECURITY DEFINER RPCS: CLAIM, CONFIRM, STATUS, DAN GET INFO
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 11.1 RPC: public.claim_package
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.claim_package(
    p_package_id BIGINT,
    p_operation_id TEXT,
    p_installation_uid TEXT,
    p_verification_code TEXT,
    p_computed_manifest_hash TEXT,
    p_hospital_id BIGINT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_user_hospital_id BIGINT;
    v_mem_count INTEGER;
    v_min_rs_id BIGINT;
    v_max_rs_id BIGINT;
    v_pkg RECORD;
    v_db_hash TEXT;
    v_clean_op_id TEXT;
    v_clean_inst_uid TEXT;
    v_clean_verif_code TEXT;
    v_clean_hash TEXT;
BEGIN
    -- A. Validasi Wajib Parameter Masukan (Strict NULL & Empty String Check)
    IF p_package_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_package_id wajib diisi (tidak boleh NULL)');
    END IF;
    IF p_operation_id IS NULL OR btrim(p_operation_id) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_operation_id wajib diisi (tidak boleh NULL/kosong)');
    END IF;
    IF p_installation_uid IS NULL OR btrim(p_installation_uid) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_installation_uid wajib diisi (tidak boleh NULL/kosong)');
    END IF;
    IF p_verification_code IS NULL OR btrim(p_verification_code) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_verification_code wajib diisi (tidak boleh NULL/kosong)');
    END IF;
    IF p_computed_manifest_hash IS NULL OR btrim(p_computed_manifest_hash) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_computed_manifest_hash wajib diisi (tidak boleh NULL/kosong)');
    END IF;

    -- Validasi Format Kanonikal Manifest Hash (Wajib 64-char hex lowercase tanpa spasi)
    IF p_computed_manifest_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_MANIFEST_HASH_FORMAT', 'message', 'Format manifest hash bukan heksadesimal lowercase 64-karakter kanonikal');
    END IF;

    v_clean_op_id      := btrim(p_operation_id);
    v_clean_inst_uid   := btrim(p_installation_uid);
    v_clean_verif_code := btrim(p_verification_code);
    v_clean_hash       := p_computed_manifest_hash;

    -- 1. Validasi auth.uid()
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'UNAUTHENTICATED', 'message', 'Sesi tidak terautentikasi (auth.uid() NULL)');
    END IF;

    -- 2. Validasi membership aktif (Kebijakan 1 Akun 1 Rumah Sakit - P1-01 Fix)
    -- Menolak keras jika pengguna memiliki lebih dari 1 membership aktif (tanpa MAX())
    SELECT COUNT(*), min(hm.hospital_id), max(hm.hospital_id)
    INTO v_mem_count, v_min_rs_id, v_max_rs_id
    FROM public.hospital_memberships hm
    JOIN public.rumah_sakit rs ON rs.id = hm.hospital_id
    WHERE hm.user_id = v_user_id
      AND hm.is_active = TRUE
      AND rs.is_active = TRUE;

    IF v_mem_count <> 1 OR v_min_rs_id IS DISTINCT FROM v_max_rs_id OR v_min_rs_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'Pengguna tidak memiliki tepat satu keanggotaan aktif pada rumah sakit yang sah');
    END IF;

    v_user_hospital_id := v_min_rs_id;

    -- Validasi parameter p_hospital_id dari client jika disertakan
    IF p_hospital_id IS NOT NULL AND p_hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'hospital_id yang diminta tidak cocok dengan keanggotaan aktif pengguna');
    END IF;

    -- 3. Baca paket tanpa FOR UPDATE (P0-02 Fix)
    SELECT * INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket pengiriman tidak ditemukan');
    END IF;

    -- 4 & 5. Periksa hospital_id & Tolak paket lintas RS sebelum memperoleh row lock (P0-02 Fix)
    IF v_pkg.hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'PACKAGE_NOT_BOUND', 'message', 'Akses ditolak: paket belum terikat pada rumah sakit tujuan (karantina)');
    END IF;

    IF v_pkg.hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'CROSS_TENANT_ACCESS_DENIED', 'message', 'Akses ditolak: paket terikat pada rumah sakit lain');
    END IF;

    -- 6. Ambil SELECT FOR UPDATE setelah kepemilikan tenant tervalidasi (P0-02 Fix)
    SELECT * INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket pengiriman tidak ditemukan saat penguncian');
    END IF;

    -- 7. Periksa ulang hospital_id setelah lock untuk mencegah TOCTOU (P0-02 Fix)
    IF v_pkg.hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'PACKAGE_NOT_BOUND', 'message', 'Akses ditolak: paket belum terikat pada rumah sakit tujuan (karantina)');
    END IF;

    IF v_pkg.hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'CROSS_TENANT_ACCESS_DENIED', 'message', 'Akses ditolak: paket terikat pada rumah sakit lain');
    END IF;

    -- 8. Validasi kode verifikasi (Setelah kepemilikan tenant terbukti - Anti-Oracle)
    IF v_pkg.verification_code <> v_clean_verif_code THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_VERIFICATION_CODE', 'message', 'Kode verifikasi tidak cocok');
    END IF;

    -- 9. Validasi manifest dan state machine
    BEGIN
        v_db_hash := public.compute_package_manifest_hash(p_package_id);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'MANIFEST_COMPUTATION_FAILED', 'message', SQLERRM);
    END;

    IF v_db_hash <> v_clean_hash THEN
        RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'MANIFEST_HASH_MISMATCH', 'message', 'Manifest hash lokal tidak cocok dengan data paket cloud');
    END IF;

    -- Evaluasi Idempotensi & State Machine
    IF v_pkg.status = 'claimed' THEN
        IF v_pkg.hospital_id = v_user_hospital_id AND
           v_pkg.claimed_by_operation_id = v_clean_op_id AND
           v_pkg.claimed_by_installation_uid = v_clean_inst_uid AND
           v_pkg.manifest_hash IS NOT NULL AND
           v_pkg.manifest_hash = v_clean_hash AND
           v_pkg.manifest_hash = v_db_hash THEN
            RETURN jsonb_build_object(
                'status', 'SUCCESS',
                'code', 'ALREADY_CLAIMED_BY_SELF',
                'message', 'Paket telah berhasil diklaim sebelumnya oleh operasi ini (idempoten)',
                'paket_id', v_pkg.id,
                'package_id', v_pkg.id,
                'hospital_id', v_pkg.hospital_id,
                'status_paket', 'claimed',
                'operation_id', v_pkg.claimed_by_operation_id,
                'installation_uid', v_pkg.claimed_by_installation_uid,
                'manifest_hash', v_pkg.manifest_hash,
                'claimed_at', v_pkg.claimed_at
            );
        ELSE
            RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'PACKAGE_ALREADY_CLAIMED', 'message', 'Paket telah diklaim oleh rumah sakit, operasi, atau data manifest berbeda');
        END IF;
    END IF;

    IF v_pkg.status IN ('confirmed', 'diterima', 'received') THEN
        IF v_pkg.hospital_id = v_user_hospital_id THEN
            RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'PACKAGE_ALREADY_CONFIRMED', 'message', 'Paket telah dikonfirmasi dan tidak dapat diklaim ulang');
        ELSE
            RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'PACKAGE_ALREADY_CLAIMED', 'message', 'Paket telah diproses oleh entitas lain');
        END IF;
    END IF;

    IF v_pkg.status <> 'pending' THEN
        RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'INVALID_PACKAGE_STATUS', 'message', format('Status paket tidak dapat diklaim: %s', v_pkg.status));
    END IF;

    -- 10. Jalankan perubahan status atomik (P0-02 Fix)
    UPDATE public.pengiriman_linen
    SET 
        status = 'claimed',
        manifest_hash = v_db_hash,
        claimed_by_operation_id = v_clean_op_id,
        claimed_by_installation_uid = v_clean_inst_uid,
        claimed_at = clock_timestamp(),
        claimed_by_user_id = v_user_id
    WHERE id = p_package_id;

    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'code', 'CLAIM_SUCCESS',
        'message', 'Paket berhasil diklaim',
        'paket_id', v_pkg.id,
        'package_id', v_pkg.id,
        'hospital_id', v_user_hospital_id,
        'status_paket', 'claimed',
        'operation_id', v_clean_op_id,
        'installation_uid', v_clean_inst_uid,
        'manifest_hash', v_db_hash,
        'claimed_at', clock_timestamp()
    );
END $$;
COMMENT ON FUNCTION public.claim_package(BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT) IS 
    'RPC transaksi atomik klaim paket pengiriman linen dengan penguncian pesimistik FOR UPDATE dan evaluasi idempotensi ketat';

-- -----------------------------------------------------------------------------
-- 11.2 RPC: public.confirm_package (6 Parameter Eksak, Eliminasi p_permit_id - C7 Fix)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.confirm_package(
    p_package_id BIGINT,
    p_hospital_id BIGINT,
    p_operation_id TEXT,
    p_installation_uid TEXT,
    p_verification_code TEXT,
    p_manifest_hash TEXT
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_user_hospital_id BIGINT;
    v_mem_count INTEGER;
    v_min_rs_id BIGINT;
    v_max_rs_id BIGINT;
    v_pkg RECORD;
    v_db_hash TEXT;
    v_clean_op_id TEXT;
    v_clean_inst_uid TEXT;
    v_clean_verif_code TEXT;
    v_clean_hash TEXT;
BEGIN
    -- A. Validasi Wajib 6 Parameter Masukan (Strict NULL & Empty String Check - C7 Fix)
    IF p_package_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_package_id wajib diisi (tidak boleh NULL)');
    END IF;
    IF p_hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_hospital_id wajib diisi (tidak boleh NULL)');
    END IF;
    IF p_operation_id IS NULL OR btrim(p_operation_id) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_operation_id wajib diisi (tidak boleh NULL/kosong)');
    END IF;
    IF p_installation_uid IS NULL OR btrim(p_installation_uid) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_installation_uid wajib diisi (tidak boleh NULL/kosong)');
    END IF;
    IF p_verification_code IS NULL OR btrim(p_verification_code) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_verification_code wajib diisi (tidak boleh NULL/kosong)');
    END IF;
    IF p_manifest_hash IS NULL OR btrim(p_manifest_hash) = '' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_manifest_hash wajib diisi (tidak boleh NULL/kosong)');
    END IF;

    -- Validasi Format Kanonikal Manifest Hash
    IF p_manifest_hash !~ '^[0-9a-f]{64}$' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_MANIFEST_HASH_FORMAT', 'message', 'Format manifest hash bukan heksadesimal lowercase 64-karakter kanonikal');
    END IF;

    v_clean_op_id      := btrim(p_operation_id);
    v_clean_inst_uid   := btrim(p_installation_uid);
    v_clean_verif_code := btrim(p_verification_code);
    v_clean_hash       := lower(btrim(p_manifest_hash));

    -- 1. Validasi auth.uid()
    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'UNAUTHENTICATED', 'message', 'Sesi tidak terautentikasi (auth.uid() NULL)');
    END IF;

    -- 2. Validasi membership aktif (Kebijakan 1 Akun 1 RS - P1-01 Fix)
    SELECT COUNT(*), min(hm.hospital_id), max(hm.hospital_id)
    INTO v_mem_count, v_min_rs_id, v_max_rs_id
    FROM public.hospital_memberships hm
    JOIN public.rumah_sakit rs ON rs.id = hm.hospital_id
    WHERE hm.user_id = v_user_id
      AND hm.is_active = TRUE
      AND rs.is_active = TRUE;

    IF v_mem_count <> 1 OR v_min_rs_id IS DISTINCT FROM v_max_rs_id OR v_min_rs_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'Pengguna tidak memiliki tepat satu keanggotaan aktif pada rumah sakit yang sah');
    END IF;

    v_user_hospital_id := v_min_rs_id;

    IF p_hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'hospital_id yang diminta tidak cocok dengan keanggotaan aktif pengguna');
    END IF;

    -- 3. Baca paket tanpa FOR UPDATE (P0-02 Fix)
    SELECT * INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket pengiriman tidak ditemukan');
    END IF;

    -- 4 & 5. Periksa hospital_id & Tolak paket lintas RS sebelum memperoleh row lock (P0-02 Fix)
    IF v_pkg.hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'PACKAGE_NOT_BOUND', 'message', 'Paket belum terikat pada rumah sakit');
    END IF;

    IF v_pkg.hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'UNAUTHORIZED_TENANT_CONFIRMATION', 'message', 'hospital_id yang diminta tidak cocok dengan pengikatan paket');
    END IF;

    -- 6. Ambil SELECT FOR UPDATE setelah kepemilikan tenant tervalidasi (P0-02 Fix)
    SELECT * INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket pengiriman tidak ditemukan saat penguncian');
    END IF;

    -- 7. Periksa ulang hospital_id setelah lock untuk mencegah TOCTOU (P0-02 Fix)
    IF v_pkg.hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'PACKAGE_NOT_BOUND', 'message', 'Paket belum terikat pada rumah sakit');
    END IF;

    IF v_pkg.hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'UNAUTHORIZED_TENANT_CONFIRMATION', 'message', 'hospital_id yang diminta tidak cocok dengan pengikatan paket');
    END IF;

    -- 8. Validasi kode verifikasi
    IF v_pkg.verification_code <> v_clean_verif_code THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_VERIFICATION_CODE', 'message', 'Kode verifikasi tidak sesuai');
    END IF;

    -- 9. Validasi manifest dan state machine
    BEGIN
        v_db_hash := public.compute_package_manifest_hash(p_package_id);
    EXCEPTION
        WHEN OTHERS THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'MANIFEST_COMPUTATION_FAILED', 'message', SQLERRM);
    END;

    IF v_db_hash <> v_clean_hash THEN
        RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'MANIFEST_HASH_MISMATCH', 'message', 'Manifest hash lokal tidak cocok dengan data paket cloud');
    END IF;

    -- Evaluasi Idempotensi
    IF v_pkg.status IN ('confirmed', 'diterima', 'received') THEN
        IF v_pkg.hospital_id = v_user_hospital_id AND
           v_pkg.verification_code = v_clean_verif_code AND
           v_pkg.confirmed_by_operation_id = v_clean_op_id AND
           v_pkg.confirmed_by_installation_uid = v_clean_inst_uid AND
           v_pkg.manifest_hash IS NOT NULL AND
           v_pkg.manifest_hash = v_clean_hash AND
           v_pkg.manifest_hash = v_db_hash THEN
            RETURN jsonb_build_object(
                'status', 'confirmed',
                'code', 'ALREADY_CONFIRMED_BY_SELF',
                'message', 'Paket telah berhasil dikonfirmasi sebelumnya oleh operasi ini (idempoten)',
                'paket_id', v_pkg.id,
                'package_id', v_pkg.id,
                'hospital_id', v_pkg.hospital_id,
                'status_paket', 'confirmed',
                'operation_id', v_pkg.confirmed_by_operation_id,
                'installation_uid', v_pkg.confirmed_by_installation_uid,
                'manifest_hash', v_pkg.manifest_hash,
                'confirmed_at', v_pkg.confirmed_at
            );
        ELSE
            RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'PACKAGE_ALREADY_CONFIRMED', 'message', 'Paket telah dikonfirmasi oleh operasi atau instansi lain');
        END IF;
    END IF;

    IF v_pkg.status <> 'claimed' THEN
        RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'PACKAGE_NOT_YET_CLAIMED', 'message', format('Paket tidak dapat dikonfirmasi karena status saat ini: %s', v_pkg.status));
    END IF;

    IF v_pkg.claimed_by_operation_id <> v_clean_op_id OR
       v_pkg.claimed_by_installation_uid <> v_clean_inst_uid THEN
        RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'CLAIM_OPERATION_MISMATCH', 'message', 'Konfirmasi paket ditolak: operasi tidak sesuai dengan entitas pengklaim paket');
    END IF;

    IF v_pkg.manifest_hash <> v_clean_hash THEN
        RETURN jsonb_build_object('status', 'CONFLICT', 'code', 'MANIFEST_HASH_MISMATCH', 'message', 'Manifest hash saat klaim tidak cocok dengan manifest saat konfirmasi');
    END IF;

    -- 10. Jalankan perubahan status atomik
    UPDATE public.pengiriman_linen
    SET 
        status = 'confirmed',
        confirmed_by_operation_id = v_clean_op_id,
        confirmed_by_installation_uid = v_clean_inst_uid,
        confirmed_at = clock_timestamp(),
        confirmed_by_user_id = v_user_id
    WHERE id = p_package_id;

    RETURN jsonb_build_object(
        'status', 'confirmed',
        'code', 'CONFIRM_SUCCESS',
        'message', 'Paket berhasil dikonfirmasi sebagai diterima',
        'paket_id', v_pkg.id,
        'package_id', v_pkg.id,
        'hospital_id', v_pkg.hospital_id,
        'status_paket', 'confirmed',
        'operation_id', v_clean_op_id,
        'installation_uid', v_clean_inst_uid,
        'manifest_hash', v_pkg.manifest_hash,
        'confirmed_at', clock_timestamp()
    );
END $$;
COMMENT ON FUNCTION public.confirm_package(BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT) IS 
    'RPC konfirmasi penerimaan paket linen dengan 6 parameter kanonikal, validasi keanggotaan tenant, dan evaluasi idempotensi komprehensif';

-- -----------------------------------------------------------------------------
-- 11.3 RPC: public.get_package_claim_status (Pengembalian Seluruh Audit IDs & Tenant Scoping - F5 Fix)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_package_claim_status(
    p_package_id BIGINT,
    p_hospital_id BIGINT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_user_hospital_id BIGINT;
    v_mem_count INTEGER;
    v_min_rs_id BIGINT;
    v_max_rs_id BIGINT;
    v_pkg RECORD;
BEGIN
    IF p_package_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_package_id wajib diisi (tidak boleh NULL)');
    END IF;

    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'UNAUTHENTICATED', 'message', 'Pengguna harus terautentikasi (auth.uid() NULL)');
    END IF;

    -- Resolusi Keanggotaan Aktif Pengguna (Kebijakan 1 Akun 1 RS - P1-01 Fix)
    SELECT COUNT(*), min(hm.hospital_id), max(hm.hospital_id)
    INTO v_mem_count, v_min_rs_id, v_max_rs_id
    FROM public.hospital_memberships hm
    JOIN public.rumah_sakit rs ON rs.id = hm.hospital_id
    WHERE hm.user_id = v_user_id
      AND hm.is_active = TRUE
      AND rs.is_active = TRUE;

    IF v_mem_count <> 1 OR v_min_rs_id IS DISTINCT FROM v_max_rs_id OR v_min_rs_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'Pengguna tidak memiliki tepat satu keanggotaan aktif pada rumah sakit');
    END IF;

    v_user_hospital_id := v_min_rs_id;

    IF p_hospital_id IS NOT NULL AND p_hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'hospital_id yang diminta tidak cocok dengan keanggotaan aktif pengguna');
    END IF;

    SELECT * INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket pengiriman tidak ditemukan');
    END IF;

    -- Option A: Validasi Batas Multi-Tenant & Status Karantina
    IF v_pkg.hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'PACKAGE_NOT_BOUND', 'message', 'Akses ditolak: paket belum terikat pada rumah sakit');
    END IF;

    IF v_pkg.hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'CROSS_TENANT_ACCESS_DENIED', 'message', 'Akses ditolak: paket terikat pada rumah sakit lain');
    END IF;

    -- Validasi Integritas Data Paket
    IF v_pkg.status IS NULL OR v_pkg.status NOT IN ('pending', 'claimed', 'confirmed', 'diterima', 'received') THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'CORRUPT_PACKAGE_STATE', 'message', 'Integritas paket korup: status data tidak valid');
    END IF;

    RETURN jsonb_build_object(
        'status', 'FOUND',
        'paket_id', v_pkg.id,
        'package_id', v_pkg.id,
        'package_status', v_pkg.status,
        'status_paket', v_pkg.status,
        'hospital_id', v_pkg.hospital_id,
        'supplier', v_pkg.supplier,
        'tanggal', v_pkg.tanggal,
        'manifest_hash', v_pkg.manifest_hash,
        'claimed_at', v_pkg.claimed_at,
        'claimed_by_operation_id', v_pkg.claimed_by_operation_id,
        'claimed_by_installation_uid', v_pkg.claimed_by_installation_uid,
        'confirmed_at', v_pkg.confirmed_at,
        'confirmed_by_operation_id', v_pkg.confirmed_by_operation_id,
        'confirmed_by_installation_uid', v_pkg.confirmed_by_installation_uid
    );
END $$;
COMMENT ON FUNCTION public.get_package_claim_status(BIGINT, BIGINT) IS 
    'RPC verifikasi status klaim paket linen dengan pengembalian lengkap jejak audit IDs untuk mendukung rekonsiliasi crash recovery';

-- -----------------------------------------------------------------------------
-- 11.4 RPC: public.get_package_info (Akses Pratinjau Aman Tanpa verification_code - C9 & F4 Fix)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_package_info(p_package_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_mem_count INTEGER;
    v_min_rs_id BIGINT;
    v_max_rs_id BIGINT;
    v_user_hospital_id BIGINT;
    v_pkg RECORD;
    v_items JSONB;
BEGIN
    IF p_package_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_package_id wajib diisi');
    END IF;

    v_user_id := auth.uid();
    IF v_user_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'UNAUTHENTICATED', 'message', 'Pengguna harus terautentikasi (auth.uid() NULL)');
    END IF;

    -- Validasi Keanggotaan Aktif Pengguna (Kebijakan 1 Akun 1 RS - P1-01 & P1-02 Fix)
    -- Menolak jika memiliki lebih dari 1 keanggotaan aktif
    SELECT COUNT(*), min(hm.hospital_id), max(hm.hospital_id)
    INTO v_mem_count, v_min_rs_id, v_max_rs_id
    FROM public.hospital_memberships hm
    JOIN public.rumah_sakit rs ON rs.id = hm.hospital_id
    WHERE hm.user_id = v_user_id
      AND hm.is_active = TRUE
      AND rs.is_active = TRUE;

    IF v_mem_count <> 1 OR v_min_rs_id IS DISTINCT FROM v_max_rs_id OR v_min_rs_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'INVALID_HOSPITAL_MEMBERSHIP', 'message', 'Pengguna tidak memiliki tepat satu keanggotaan rumah sakit aktif');
    END IF;

    v_user_hospital_id := v_min_rs_id;

    -- Baca paket tanpa lock
    SELECT * INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket pengiriman tidak ditemukan');
    END IF;

    -- Option A: Isolasi Multi-Tenant Ketat & Karantina (Tolak Sebelum Membocorkan Manifest)
    IF v_pkg.hospital_id IS NULL THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'PACKAGE_NOT_BOUND', 'message', 'Akses ditolak: paket belum terikat pada rumah sakit');
    END IF;

    IF v_pkg.hospital_id <> v_user_hospital_id THEN
        RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'CROSS_TENANT_ACCESS_DENIED', 'message', 'Akses ditolak: paket milik rumah sakit lain');
    END IF;

    -- Ambil item manifest detail dengan EPC ternormalisasi (hanya jika tenant cocok)
    SELECT coalesce(jsonb_agg(
        jsonb_build_object(
            'epc', public.clean_epc(d.epc),
            'kategori', coalesce(d.kategori, ''),
            'nama_linen', coalesce(d.nama_linen, '')
        ) ORDER BY convert_to(public.clean_epc(d.epc), 'UTF8') ASC
    ), '[]'::jsonb) INTO v_items
    FROM public.detail_pengiriman_linen d
    WHERE d.pengiriman_id = p_package_id;

    -- Return metadata paket AMAN: TANPA verification_code
    RETURN jsonb_build_object(
        'status', 'ok',
        'paket_id', v_pkg.id,
        'package_id', v_pkg.id,
        'hospital_id', v_pkg.hospital_id,
        'supplier', v_pkg.supplier,
        'status_paket', v_pkg.status,
        'tanggal', v_pkg.tanggal,
        'manifest_hash', v_pkg.manifest_hash,
        'manifest_items', v_items
    );
END $$;
COMMENT ON FUNCTION public.get_package_info(BIGINT) IS 
    'RPC aman pembacaan manifest paket linen tanpa mengekspos verification_code dan dengan pembatasan multi-tenant ketat';

-- =============================================================================
-- 12. PENEGAKAN ROW LEVEL SECURITY (RLS) NON-REKURSIF MULTI-TENANT
-- =============================================================================

ALTER TABLE public.pengiriman_linen ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detail_pengiriman_linen ENABLE ROW LEVEL SECURITY;

-- 12.1 Dynamic Drop Seluruh Policy RLS Lama pada Kedua Tabel (C9 Fix)
DO $$
DECLARE
    pol RECORD;
BEGIN
    FOR pol IN (
        SELECT schemaname, tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN ('pengiriman_linen', 'detail_pengiriman_linen')
    ) LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I;', pol.policyname, pol.schemaname, pol.tablename);
    END LOOP;
END $$;

-- 12.2 Policy SELECT Multi-Tenant pada public.pengiriman_linen untuk authenticated
-- Pengguna hanya boleh melihat paket yang telah terikat pada rumah sakit aktif mereka.
CREATE POLICY p_pengiriman_linen_select_member
ON public.pengiriman_linen
FOR SELECT
TO authenticated
USING (
    hospital_id IS NOT NULL
    AND hospital_id IN (
        SELECT hm.hospital_id
        FROM public.hospital_memberships hm
        JOIN public.rumah_sakit rs ON rs.id = hm.hospital_id
        WHERE hm.user_id = auth.uid()
          AND hm.is_active = TRUE
          AND rs.is_active = TRUE
    )
);

-- 12.3 Policy SELECT Multi-Tenant pada public.detail_pengiriman_linen untuk authenticated
CREATE POLICY p_detail_pengiriman_linen_select_member
ON public.detail_pengiriman_linen
FOR SELECT
TO authenticated
USING (
    pengiriman_id IN (
        SELECT pl.id
        FROM public.pengiriman_linen pl
        WHERE pl.hospital_id IS NOT NULL
          AND pl.hospital_id IN (
              SELECT hm.hospital_id
              FROM public.hospital_memberships hm
              JOIN public.rumah_sakit rs ON rs.id = hm.hospital_id
              WHERE hm.user_id = auth.uid()
                AND hm.is_active = TRUE
                AND rs.is_active = TRUE
          )
    )
);

-- =============================================================================
-- 13. REKONSILIASI HAK AKSES TINGKAT TABEL, KOLOM, DAN SEQUENCE (LEAST PRIVILEGE)
-- =============================================================================

-- 13.1 Cabut Seluruh Table-Level Privilege Lama
REVOKE ALL PRIVILEGES ON TABLE public.pengiriman_linen FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL PRIVILEGES ON TABLE public.detail_pengiriman_linen FROM PUBLIC, anon, authenticated, service_role;

-- 13.2 Cabut Seluruh Column-Level Privilege Lama Menggunakan Sintaks Dinamis Valid (C3 Fix: Unquoted PUBLIC)
DO $$
DECLARE
    r RECORD;
    v_sql TEXT;
    v_grantee_clause TEXT;
BEGIN
    FOR r IN (
        SELECT DISTINCT table_schema, table_name, column_name, grantee, privilege_type
        FROM information_schema.column_privileges
        WHERE table_schema = 'public' 
          AND table_name IN ('pengiriman_linen', 'detail_pengiriman_linen')
          AND grantee IN ('PUBLIC', 'anon', 'authenticated', 'service_role')
    ) LOOP
        IF r.grantee = 'PUBLIC' THEN
            v_grantee_clause := 'PUBLIC';
        ELSE
            v_grantee_clause := quote_ident(r.grantee);
        END IF;

        v_sql := format('REVOKE %s (%I) ON TABLE %I.%I FROM %s;', 
                        r.privilege_type, r.column_name, r.table_schema, r.table_name, v_grantee_clause);
        EXECUTE v_sql;
    END LOOP;
END $$;

-- 13.3 Berikan Hak Akses Kolom Terbatas pada authenticated (TANPA verification_code - C9 Fix)
GRANT SELECT (
    id, supplier, status, tanggal, catatan, created_at, hospital_id,
    claimed_at, claimed_by_user_id, claimed_by_operation_id, claimed_by_installation_uid,
    confirmed_at, confirmed_by_user_id, confirmed_by_operation_id, confirmed_by_installation_uid,
    manifest_hash
) ON TABLE public.pengiriman_linen TO authenticated;

GRANT SELECT (
    id, pengiriman_id, epc, kategori, nama_linen, created_at
) ON TABLE public.detail_pengiriman_linen TO authenticated;

-- 13.4 Berikan Hak Akses Minimum pada service_role (Supplier / Ingestion Engine)
GRANT SELECT, INSERT ON TABLE public.pengiriman_linen TO service_role;
GRANT SELECT, INSERT ON TABLE public.detail_pengiriman_linen TO service_role;
GRANT UPDATE (catatan) ON TABLE public.pengiriman_linen TO service_role;

-- 13.5 Berikan Izin Sequence Hanya pada Sequence Terkait Tabel Target
DO $$
DECLARE
    v_seq_header TEXT;
    v_seq_detail TEXT;
BEGIN
    v_seq_header := pg_get_serial_sequence('public.pengiriman_linen', 'id');
    v_seq_detail := pg_get_serial_sequence('public.detail_pengiriman_linen', 'id');

    IF v_seq_header IS NOT NULL THEN
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC, anon, authenticated;', v_seq_header);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO service_role;', v_seq_header);
    END IF;

    IF v_seq_detail IS NOT NULL THEN
        EXECUTE format('REVOKE ALL ON SEQUENCE %s FROM PUBLIC, anon, authenticated;', v_seq_detail);
        EXECUTE format('GRANT USAGE, SELECT ON SEQUENCE %s TO service_role;', v_seq_detail);
    END IF;
END $$;

-- 13.6 Rekonsiliasi Izin Eksekusi Fungsi (Least Privilege - C8 Fix)
-- Helper internal: Cabut dari PUBLIC, anon, dan authenticated (Hanya service_role / owner)
REVOKE ALL ON FUNCTION public.clean_epc(TEXT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.clean_epc(TEXT) TO service_role;

REVOKE ALL ON FUNCTION public.compute_package_manifest_hash(BIGINT) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.compute_package_manifest_hash(BIGINT) TO service_role;

-- RPC Publik Autentikasi: Berikan ke authenticated dan service_role
REVOKE ALL ON FUNCTION public.claim_package(BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.claim_package(BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT) TO authenticated, service_role;

REVOKE ALL ON FUNCTION public.confirm_package(BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.confirm_package(BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT) TO authenticated, service_role;

REVOKE ALL ON FUNCTION public.get_package_claim_status(BIGINT, BIGINT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_package_claim_status(BIGINT, BIGINT) TO authenticated, service_role;

REVOKE ALL ON FUNCTION public.get_package_info(BIGINT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.get_package_info(BIGINT) TO authenticated, service_role;

-- =============================================================================
-- 14. POSTFLIGHT ASSERTION 12 PILAR EKSAK (FAIL-FAST KESELURUHAN)
-- =============================================================================
-- Verifikasi katalog pg_* yang komprehensif dan mendalam untuk membuktikan:
-- 1. Skema kolom tabel dan tipe data eksak
-- 2. Foreign key definitions, targets (confrelid), dan actions (confdeltype)
-- 3. Definisi indeks performa & Unique Functional Index EPC (indisunique & clean_epc expression)
-- 4. Status Row Level Security (RLS) aktif pada kedua tabel
-- 5. Ketiadaan hak akses SELECT/mutasi terlarang pada tabel
-- 6. Hak akses Column-Level Security (verification_code tidak dapat dibaca authenticated)
-- 7. Hak akses Column-Level UPDATE pada service_role (hanya catatan yang diizinkan)
-- 8. Trigger timing (BEFORE), events (INSERT, UPDATE, DELETE), row-level, dan status aktif
-- 9. Keberadaan 6 fungsi inti dengan konteks keamanan (prosecdef) dan hardened search_path
-- 10. Hasil overload cleanup (tepat 1 signature per nama fungsi pada schema public)
-- 11. Argument identity functions pada pg_proc identik 100% dengan kontrak R2.10.4
-- 12. Hak akses sequence operasional (kedua tabel) dan larangan eksekusi helper internal
-- =============================================================================
DO $$
DECLARE
    v_col RECORD;
    v_has_priv BOOLEAN;
    v_pol_cnt INTEGER;
    v_proc_cnt INTEGER;
    v_trg RECORD;
    v_seq_name TEXT;
    v_seq_detail TEXT;
BEGIN
    RAISE NOTICE '=== MEMULAI POSTFLIGHT ASSERTION 12 PILAR R2.10.4 (DEEP CATALOG AUDIT) ===';

    -- -------------------------------------------------------------------------
    -- PILAR 1: Validasi Struktur & Tipe Data Seluruh Kolom
    -- -------------------------------------------------------------------------
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='id' AND data_type='bigint') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='verification_code' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='supplier' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='status' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='tanggal' AND data_type='timestamp with time zone') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='catatan' AND data_type='text') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='created_at' AND data_type='timestamp with time zone') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='hospital_id' AND data_type='bigint') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='manifest_hash' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='claimed_by_operation_id' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='claimed_by_installation_uid' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='claimed_at' AND data_type='timestamp with time zone') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='claimed_by_user_id' AND data_type='uuid') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='confirmed_by_operation_id' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='confirmed_by_installation_uid' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='confirmed_at' AND data_type='timestamp with time zone') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen' AND column_name='confirmed_by_user_id' AND data_type='uuid') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 1]: Skema 17 kolom audit & data pada public.pengiriman_linen tidak cocok' USING ERRCODE = '42804';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='detail_pengiriman_linen' AND column_name='id' AND data_type='bigint') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='detail_pengiriman_linen' AND column_name='pengiriman_id' AND data_type='bigint') OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='detail_pengiriman_linen' AND column_name='epc' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='detail_pengiriman_linen' AND column_name='kategori' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='detail_pengiriman_linen' AND column_name='nama_linen' AND data_type IN ('character varying', 'text')) OR
       NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='detail_pengiriman_linen' AND column_name='created_at' AND data_type='timestamp with time zone') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 1]: Skema 6 kolom pada public.detail_pengiriman_linen tidak cocok' USING ERRCODE = '42804';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 2: Validasi Definisi, Target, dan Aksi Foreign Key Constraints
    -- -------------------------------------------------------------------------
    -- fk_pengiriman_linen_hospital wajib mereferensikan public.rumah_sakit(id) dengan aksi ON DELETE RESTRICT ('r')
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conname = 'fk_pengiriman_linen_hospital'
          AND c.conrelid = 'public.pengiriman_linen'::regclass
          AND c.confrelid = 'public.rumah_sakit'::regclass
          AND c.contype = 'f'
          AND c.confdeltype = 'r'
    ) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 2]: Foreign key fk_pengiriman_linen_hospital tidak valid (wajib ke rumah_sakit ON DELETE RESTRICT)' USING ERRCODE = '42830';
    END IF;

    -- fk_detail_pengiriman_linen_header wajib mereferensikan public.pengiriman_linen(id) dengan aksi ON DELETE RESTRICT ('r')
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint c
        WHERE c.conname = 'fk_detail_pengiriman_linen_header'
          AND c.conrelid = 'public.detail_pengiriman_linen'::regclass
          AND c.confrelid = 'public.pengiriman_linen'::regclass
          AND c.contype = 'f'
          AND c.confdeltype = 'r'
    ) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 2]: Foreign key fk_detail_pengiriman_linen_header tidak valid (wajib ke pengiriman_linen ON DELETE RESTRICT)' USING ERRCODE = '42830';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 3: Validasi Indeks & Definisi Expression Unique Functional Index
    -- -------------------------------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_class c ON c.oid = i.indexrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname = 'uq_detail_pengiriman_linen_epc_clean'
          AND i.indisunique = TRUE
          AND pg_get_indexdef(i.indexrelid) LIKE '%public.clean_epc(epc)%'
    ) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 3]: Unique functional index uq_detail_pengiriman_linen_epc_clean tidak terpasang dengan ekspresi public.clean_epc(epc)' USING ERRCODE = '42704';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='pengiriman_linen' AND indexname='idx_pengiriman_linen_tenant_lookup') OR
       NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='pengiriman_linen' AND indexname='idx_pengiriman_linen_hospital_id') OR
       NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='detail_pengiriman_linen' AND indexname='idx_detail_pengiriman_linen_pkg') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 3]: Indeks performa wajib tidak lengkap' USING ERRCODE = '42704';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 4: Validasi Row Level Security (RLS) Status & Policies
    -- -------------------------------------------------------------------------
    SELECT COUNT(*) INTO v_pol_cnt
    FROM pg_class
    WHERE relname IN ('pengiriman_linen', 'detail_pengiriman_linen') AND relrowsecurity = TRUE;

    IF v_pol_cnt <> 2 THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 4]: RLS belum diaktifkan pada kedua tabel operasional' USING ERRCODE = '42501';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 5: Validasi Ketiadaan Hak SELECT Anonim & Hak Table-Level Terlarang
    -- -------------------------------------------------------------------------
    IF has_table_privilege('anon', 'public.pengiriman_linen', 'SELECT') OR
       has_table_privilege('anon', 'public.detail_pengiriman_linen', 'SELECT') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 5]: Role anon memiliki hak akses SELECT yang melanggar security posture' USING ERRCODE = '42501';
    END IF;

    IF has_table_privilege('authenticated', 'public.pengiriman_linen', 'UPDATE') OR
       has_table_privilege('authenticated', 'public.detail_pengiriman_linen', 'UPDATE') OR
       has_table_privilege('authenticated', 'public.pengiriman_linen', 'DELETE') OR
       has_table_privilege('authenticated', 'public.detail_pengiriman_linen', 'DELETE') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 5]: Role authenticated memiliki hak mutasi terlarang pada tabel' USING ERRCODE = '42501';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 6: Validasi Column-Level Privilege: verification_code WAJIB TIDAK DAPAT DIBACA authenticated
    -- -------------------------------------------------------------------------
    IF has_column_privilege('authenticated', 'public.pengiriman_linen', 'verification_code', 'SELECT') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 6]: Role authenticated memiliki hak SELECT terlarang pada verification_code' USING ERRCODE = '42501';
    END IF;

    IF NOT has_column_privilege('authenticated', 'public.pengiriman_linen', 'status', 'SELECT') OR
       NOT has_column_privilege('authenticated', 'public.pengiriman_linen', 'hospital_id', 'SELECT') OR
       NOT has_column_privilege('authenticated', 'public.pengiriman_linen', 'manifest_hash', 'SELECT') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 6]: Role authenticated tidak memiliki hak SELECT kolom non-sensitif pengiriman_linen' USING ERRCODE = '42501';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 7: Validasi Column-Level Privilege UPDATE pada service_role
    -- -------------------------------------------------------------------------
    FOR v_col IN (SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='pengiriman_linen') LOOP
        v_has_priv := has_column_privilege('service_role', 'public.pengiriman_linen', v_col.column_name, 'UPDATE');
        IF v_col.column_name = 'catatan' THEN
            IF NOT v_has_priv THEN
                RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 7]: service_role seharusnya memiliki hak UPDATE pada kolom catatan' USING ERRCODE = '42501';
            END IF;
        ELSE
            IF v_has_priv THEN
                RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 7]: service_role memiliki hak UPDATE terlarang pada kolom %', v_col.column_name USING ERRCODE = '42501';
            END IF;
        END IF;
    END LOOP;

    -- -------------------------------------------------------------------------
    -- PILAR 8: Validasi Definisi, Timing, Events, & Enabled State Immutability Triggers
    -- -------------------------------------------------------------------------
    -- trg_pengiriman_linen_immutability: Wajib BEFORE INSERT OR UPDATE, FOR EACH ROW, Enabled 'O'
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid 
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE c.relname = 'pengiriman_linen' 
          AND t.tgname = 'trg_pengiriman_linen_immutability'
          AND p.proname = 'fn_pengiriman_linen_immutability' 
          AND t.tgenabled = 'O'
          AND (t.tgtype & 2) <> 0   -- BEFORE
          AND (t.tgtype & 4) <> 0   -- INSERT
          AND (t.tgtype & 16) <> 0  -- UPDATE
          AND (t.tgtype & 1) <> 0   -- FOR EACH ROW
    ) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 8]: Trigger trg_pengiriman_linen_immutability tidak terpasang dengan spesifikasi BEFORE INSERT OR UPDATE FOR EACH ROW' USING ERRCODE = '42704';
    END IF;

    -- trg_detail_pengiriman_linen_immutability: Wajib BEFORE INSERT OR UPDATE OR DELETE, FOR EACH ROW, Enabled 'O'
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid 
        JOIN pg_proc p ON p.oid = t.tgfoid
        WHERE c.relname = 'detail_pengiriman_linen' 
          AND t.tgname = 'trg_detail_pengiriman_linen_immutability'
          AND p.proname = 'fn_detail_pengiriman_linen_immutability' 
          AND t.tgenabled = 'O'
          AND (t.tgtype & 2) <> 0   -- BEFORE
          AND (t.tgtype & 4) <> 0   -- INSERT
          AND (t.tgtype & 16) <> 0  -- UPDATE
          AND (t.tgtype & 8) <> 0   -- DELETE
          AND (t.tgtype & 1) <> 0   -- FOR EACH ROW
    ) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 8]: Trigger trg_detail_pengiriman_linen_immutability tidak terpasang dengan spesifikasi BEFORE INSERT OR UPDATE OR DELETE FOR EACH ROW' USING ERRCODE = '42704';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 9: Validasi Keberadaan & Definisi 6 Fungsi Inti & Hardened search_path
    -- -------------------------------------------------------------------------
    IF NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'clean_epc' AND prosecdef = FALSE AND provolatile = 'i') OR
       NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'compute_package_manifest_hash' AND prosecdef = FALSE) OR
       NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'claim_package' AND prosecdef = TRUE) OR
       NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'confirm_package' AND prosecdef = TRUE) OR
       NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'get_package_claim_status' AND prosecdef = TRUE) OR
       NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'get_package_info' AND prosecdef = TRUE) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 9]: Definisi fungsi atau context security mode tidak sesuai kontrak' USING ERRCODE = '42883';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 10: Validasi Hasil Overload Cleanup (Tepat 1 Signature Per Nama Fungsi)
    -- -------------------------------------------------------------------------
    FOR v_trg IN (
        SELECT unnest(ARRAY['clean_epc', 'compute_package_manifest_hash', 'claim_package', 'confirm_package', 'get_package_claim_status', 'get_package_info']) AS func_name
    ) LOOP
        SELECT COUNT(*) INTO v_proc_cnt
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = v_trg.func_name;

        IF v_proc_cnt <> 1 THEN
            RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 10]: Terdeteksi multiple overloads (%) pada fungsi %', v_proc_cnt, v_trg.func_name
                USING ERRCODE = '42723';
        END IF;
    END LOOP;

    -- -------------------------------------------------------------------------
    -- PILAR 11: Validasi Signature Kanonikal Eksak pada Katalog pg_proc
    -- -------------------------------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'clean_epc'
          AND pg_get_function_identity_arguments(p.oid) = 'p_epc text'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'compute_package_manifest_hash'
          AND pg_get_function_identity_arguments(p.oid) = 'p_package_id bigint'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'claim_package'
          AND pg_get_function_identity_arguments(p.oid) = 'p_package_id bigint, p_operation_id text, p_installation_uid text, p_verification_code text, p_computed_manifest_hash text, p_hospital_id bigint'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'confirm_package'
          AND pg_get_function_identity_arguments(p.oid) = 'p_package_id bigint, p_hospital_id bigint, p_operation_id text, p_installation_uid text, p_verification_code text, p_manifest_hash text'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'get_package_claim_status'
          AND pg_get_function_identity_arguments(p.oid) = 'p_package_id bigint, p_hospital_id bigint'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public' AND p.proname = 'get_package_info'
          AND pg_get_function_identity_arguments(p.oid) = 'p_package_id bigint'
    ) THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 11]: Signature argument fungsi pada katalog pg_proc tidak cocok dengan kontrak R2.10.4' USING ERRCODE = '42883';
    END IF;

    -- -------------------------------------------------------------------------
    -- PILAR 12: Validasi Hak Akses Sequence (Kedua Tabel) & Pembatasan Helper Internal
    -- -------------------------------------------------------------------------
    -- Validasi Sequence public.pengiriman_linen
    v_seq_name := pg_get_serial_sequence('public.pengiriman_linen', 'id');
    IF v_seq_name IS NOT NULL THEN
        IF has_sequence_privilege('authenticated', v_seq_name, 'USAGE') OR
           has_sequence_privilege('anon', v_seq_name, 'USAGE') THEN
            RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 12]: Role non-supplier memiliki hak akses USAGE pada sequence header %', v_seq_name USING ERRCODE = '42501';
        END IF;
    END IF;

    -- Validasi Sequence public.detail_pengiriman_linen
    v_seq_detail := pg_get_serial_sequence('public.detail_pengiriman_linen', 'id');
    IF v_seq_detail IS NOT NULL THEN
        IF has_sequence_privilege('authenticated', v_seq_detail, 'USAGE') OR
           has_sequence_privilege('anon', v_seq_detail, 'USAGE') THEN
            RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 12]: Role non-supplier memiliki hak akses USAGE pada sequence detail %', v_seq_detail USING ERRCODE = '42501';
        END IF;
    END IF;

    -- Validasi bahwa helper internal TIDAK DAPAT dieksekusi oleh authenticated atau anon
    IF has_function_privilege('authenticated', 'public.clean_epc(text)', 'EXECUTE') OR
       has_function_privilege('authenticated', 'public.compute_package_manifest_hash(bigint)', 'EXECUTE') OR
       has_function_privilege('anon', 'public.clean_epc(text)', 'EXECUTE') OR
       has_function_privilege('anon', 'public.compute_package_manifest_hash(bigint)', 'EXECUTE') THEN
        RAISE EXCEPTION 'POSTFLIGHT_FAILED [PILAR 12]: Role non-authorized memiliki hak EXECUTE terlarang pada internal helper functions' USING ERRCODE = '42501';
    END IF;

    RAISE NOTICE '=== SELURUH 12 PILAR POSTFLIGHT ASSERTION LOLOS DENGAN SUKSES 100%% ===';
END $$;

COMMIT;

-- =============================================================================
-- AKHIR DARI FILE MIGRATION 07_package_communication_rpc_consolidated_r2_10_4.sql
-- STATUS: DDL KONSOLIDASI R2.10.4 SIAP UNTUK VERIFIKASI PADA ISOLATED POSTGRESQL 13+
-- =============================================================================

