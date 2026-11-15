-- =============================================================================
-- FILE: test_concurrency_harness_r2_10_4_4.sql
-- REVISI: TAHAP 4D.2A.3-R2.10.4.4 (SQL FIXTURE HARNESS)
-- LINGKUNGAN: DISPOSABLE DOCKER POSTGRESQL 15+ (DIBUAT OLEH RUNNER R2.10.4.4)
-- =============================================================================
--
-- ATURAN KESELAMATAN & ISOLASI PRODUKSI:
-- 1. DILARANG KERAS MENJALANKAN FILE INI DI SUPABASE PRODUKSI ATAU STAGING!
-- 2. Wajib dijalankan di atas disposable local database yang dibuat oleh runner.
-- 3. Identitas fixture murni dinamis (gen_random_uuid & INSERT RETURNING).
-- 4. Teardown menghancurkan seluruh data fixture atau container disposable.
-- =============================================================================

-- =============================================================================
-- 0. SAFETY PRECONDITION GUARD (FAIL-FAST)
-- =============================================================================
DO $$
DECLARE
    v_dbname TEXT;
BEGIN
    v_dbname := current_database();
    
    -- Penolakan mutlak terhadap database non-disposable
    IF v_dbname IN ('postgres', 'supabase_admin', 'production', 'staging', 'clients_prod', 'clients_staging') THEN
        RAISE EXCEPTION 'SAFETY_GUARD_REJECTION: Concurrency test harness dilarang dijalankan pada database: "%". Wajib menggunakan database disposable lokal!', v_dbname
            USING ERRCODE = '55000';
    END IF;

    -- Validasi bahwa nama database pengujian terisolasi
    IF v_dbname NOT LIKE 'clients_disposable_%' AND v_dbname NOT LIKE '%test%' THEN
        RAISE EXCEPTION 'SAFETY_GUARD_REJECTION: Database "%" tidak memiliki prefix/penanda database pengujian disposable!', v_dbname
            USING ERRCODE = '55000';
    END IF;

    RAISE NOTICE 'Safety guard PASSED: Database target valid sebagai lingkungan disposable: "%"', v_dbname;
END $$;

-- =============================================================================
-- 1. TABEL TRACKER PELACAK FIXTURE
-- =============================================================================
CREATE TABLE IF NOT EXISTS _test_fixture_tracker (
    id SERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id BIGINT NULL,
    entity_uuid UUID NULL,
    label TEXT NOT NULL UNIQUE,
    metadata JSONB NULL,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp()
);

-- =============================================================================
-- 2. SETUP PROVISIONING FIXTURE DINAMIS (STEP 10)
-- =============================================================================
DO $$
DECLARE
    -- Multi-Tenant Identifiers
    v_rs_a_id BIGINT := 101;
    v_rs_b_id BIGINT := 202;
    v_rs_inact_id BIGINT := 303;
    v_user_a_uuid UUID;
    v_user_a2_uuid UUID;
    v_user_b_uuid UUID;
    v_user_inact_uuid UUID;
    v_user_nomem_uuid UUID;
    v_user_deact_uuid UUID;
    v_mem_a_id BIGINT;
    v_mem_a2_id BIGINT;
    v_mem_b_id BIGINT;
    v_mem_inact_id BIGINT;
    v_mem_deact_id BIGINT;
    -- Tenant Packages
    v_pkg_rsa_id BIGINT;
    v_pkg_rsb_id BIGINT;
    v_pkg_unbound_id BIGINT;
    -- Concurrency Packages
    v_pkg_1_id BIGINT;
    v_pkg_2_id BIGINT;
    v_pkg_3_id BIGINT;
    v_pkg_4_id BIGINT;
    v_pkg_5_id BIGINT;
    v_pkg_6_id BIGINT;
    v_pkg_7_id BIGINT;
    v_hash_3 TEXT;
    v_hash_4 TEXT;
BEGIN
    RAISE NOTICE '=== PROVISIONING IDENTITAS FIXTURE DINAMIS R2.10.4.4 ===';

    -- 2.1 Buat Master Rumah Sakit (Dua Aktif: 101, 202; Satu Non-Aktif: 303)
    INSERT INTO public.rumah_sakit (id, kode_rs, nama_rs, is_active)
    OVERRIDING SYSTEM VALUE
    VALUES 
        (101, 'RS-A-' || substr(gen_random_uuid()::text, 1, 8), 'Rumah Sakit A (Active)', TRUE),
        (202, 'RS-B-' || substr(gen_random_uuid()::text, 1, 8), 'Rumah Sakit B (Active)', TRUE),
        (303, 'RS-INACT-' || substr(gen_random_uuid()::text, 1, 8), 'Rumah Sakit Non-Aktif', FALSE)
    ON CONFLICT (id) DO UPDATE SET is_active = EXCLUDED.is_active;

    INSERT INTO _test_fixture_tracker (entity_type, entity_id, label)
    VALUES 
        ('public.rumah_sakit', 101, 'RS_A'),
        ('public.rumah_sakit', 202, 'RS_B'),
        ('public.rumah_sakit', 303, 'RS_INACTIVE')
    ON CONFLICT (label) DO UPDATE SET entity_id = EXCLUDED.entity_id;

    -- 2.2 Buat Pengguna Dinamis di auth.users (4 Kategori)
    v_user_a_uuid := gen_random_uuid();
    v_user_a2_uuid := gen_random_uuid();
    v_user_b_uuid := gen_random_uuid();
    v_user_inact_uuid := gen_random_uuid();
    v_user_nomem_uuid := gen_random_uuid();
    v_user_deact_uuid := gen_random_uuid();

    INSERT INTO auth.users (id, email, raw_user_meta_data)
    VALUES 
        (v_user_a_uuid, 'user_a_' || substr(v_user_a_uuid::text, 1, 8) || '@disp.local', '{"role":"operator"}'::jsonb),
        (v_user_a2_uuid, 'user_a2_' || substr(v_user_a2_uuid::text, 1, 8) || '@disp.local', '{"role":"operator"}'::jsonb),
        (v_user_b_uuid, 'user_b_' || substr(v_user_b_uuid::text, 1, 8) || '@disp.local', '{"role":"operator"}'::jsonb),
        (v_user_inact_uuid, 'user_inact_' || substr(v_user_inact_uuid::text, 1, 8) || '@disp.local', '{"role":"operator"}'::jsonb),
        (v_user_nomem_uuid, 'user_nomem_' || substr(v_user_nomem_uuid::text, 1, 8) || '@disp.local', '{"role":"operator"}'::jsonb),
        (v_user_deact_uuid, 'user_deact_' || substr(v_user_deact_uuid::text, 1, 8) || '@disp.local', '{"role":"operator"}'::jsonb);

    INSERT INTO _test_fixture_tracker (entity_type, entity_uuid, label)
    VALUES 
        ('auth.users', v_user_a_uuid, 'USER_A_RS_A'),
        ('auth.users', v_user_a2_uuid, 'USER_A2_RS_A'),
        ('auth.users', v_user_b_uuid, 'USER_B_RS_B'),
        ('auth.users', v_user_inact_uuid, 'USER_INACTIVE_MEM'),
        ('auth.users', v_user_nomem_uuid, 'USER_NO_MEMBERSHIP'),
        ('auth.users', v_user_deact_uuid, 'USER_DEACTIVATED')
    ON CONFLICT (label) DO UPDATE SET entity_uuid = EXCLUDED.entity_uuid;

    -- 2.3 Buat Keanggotaan Rumah Sakit (Memberships)
    INSERT INTO public.hospital_memberships (user_id, hospital_id, role, is_active)
    VALUES (v_user_a_uuid, 101, 'operator', TRUE)
    RETURNING id INTO v_mem_a_id;

    INSERT INTO public.hospital_memberships (user_id, hospital_id, role, is_active)
    VALUES (v_user_a2_uuid, 101, 'operator', TRUE)
    RETURNING id INTO v_mem_a2_id;

    INSERT INTO public.hospital_memberships (user_id, hospital_id, role, is_active)
    VALUES (v_user_b_uuid, 202, 'operator', TRUE)
    RETURNING id INTO v_mem_b_id;

    INSERT INTO public.hospital_memberships (user_id, hospital_id, role, is_active)
    VALUES (v_user_inact_uuid, 101, 'operator', FALSE)
    RETURNING id INTO v_mem_inact_id;

    INSERT INTO public.hospital_memberships (user_id, hospital_id, role, is_active)
    VALUES (v_user_deact_uuid, 101, 'operator', FALSE)
    RETURNING id INTO v_mem_deact_id;

    INSERT INTO _test_fixture_tracker (entity_type, entity_id, label)
    VALUES 
        ('public.hospital_memberships', v_mem_a_id, 'MEM_A'),
        ('public.hospital_memberships', v_mem_a2_id, 'MEM_A2'),
        ('public.hospital_memberships', v_mem_b_id, 'MEM_B'),
        ('public.hospital_memberships', v_mem_inact_id, 'MEM_INACTIVE'),
        ('public.hospital_memberships', v_mem_deact_id, 'MEM_DEACTIVATED')
    ON CONFLICT (label) DO UPDATE SET entity_id = EXCLUDED.entity_id;

    -- 2.4 Paket Multi-Tenant Testing
    -- Paket RS A (pending)
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-RSA-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_rsa_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_rsa_id, 'E280116060000219ABCDEFA1', 'Sprei', 'Sprei RS A');

    -- Paket RS B (pending)
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-RSB-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Beta', 'pending', 202, clock_timestamp())
    RETURNING id INTO v_pkg_rsb_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_rsb_id, 'E280116060000219ABCDEFB1', 'Selimut', 'Selimut RS B');

    -- Paket Unbound Pending (hospital_id NULL, historical quarantine fixture)
    -- P0-01: Disiapkan dalam database pengujian terisolasi dengan menonaktifkan trigger sementara
    EXECUTE 'ALTER TABLE public.pengiriman_linen DISABLE TRIGGER trg_pengiriman_linen_immutability';
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-UNBOUND-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Gamma', 'pending', NULL, clock_timestamp())
    RETURNING id INTO v_pkg_unbound_id;
    EXECUTE 'ALTER TABLE public.pengiriman_linen ENABLE TRIGGER trg_pengiriman_linen_immutability';

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_unbound_id, 'E280116060000219ABCDEFC1', 'Handuk', 'Handuk Unbound');

    INSERT INTO _test_fixture_tracker (entity_type, entity_id, label)
    VALUES 
        ('public.pengiriman_linen', v_pkg_rsa_id, 'PKG_RS_A'),
        ('public.pengiriman_linen', v_pkg_rsb_id, 'PKG_RS_B'),
        ('public.pengiriman_linen', v_pkg_unbound_id, 'PKG_UNBOUND')
    ON CONFLICT (label) DO UPDATE SET entity_id = EXCLUDED.entity_id;

    -- 2.5 Paket Concurrency Testing (Skenario 1 - 7)
    -- Skenario 1 (Supplier uncommitted insert vs Claim)
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN1-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_1_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_1_id, '3000E280116060000219ABCDEF01', 'Sprei', 'Sprei Standar');

    -- Skenario 2 (Claim FOR UPDATE vs Supplier insert)
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN2-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_2_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_2_id, '3000E280116060000219ABCDEF02', 'Selimut', 'Selimut Hangat');

    -- Skenario 3 (Detail UPDATE immutability pada paket claimed)
    -- P0-03: Siapkan paket dengan status non-pending (claimed) secara sah
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN3-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_3_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_3_id, '3000E280116060000219ABCDEF03', 'Handuk', 'Handuk Mandi');

    v_hash_3 := public.compute_package_manifest_hash(v_pkg_3_id);
    UPDATE public.pengiriman_linen
    SET 
        status = 'claimed',
        manifest_hash = v_hash_3,
        claimed_at = clock_timestamp(),
        claimed_by_operation_id = 'op_scen3_init',
        claimed_by_installation_uid = 'inst_node_1',
        claimed_by_user_id = v_user_a_uuid
    WHERE id = v_pkg_3_id;

    -- Skenario 4 (Detail DELETE immutability pada paket claimed)
    -- P0-03: Siapkan paket dengan status non-pending (claimed) secara sah
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN4-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_4_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_4_id, '3000E280116060000219ABCDEF04', 'Baju', 'Baju Kamar');

    v_hash_4 := public.compute_package_manifest_hash(v_pkg_4_id);
    UPDATE public.pengiriman_linen
    SET 
        status = 'claimed',
        manifest_hash = v_hash_4,
        claimed_at = clock_timestamp(),
        claimed_by_operation_id = 'op_scen4_init',
        claimed_by_installation_uid = 'inst_node_1',
        claimed_by_user_id = v_user_a_uuid
    WHERE id = v_pkg_4_id;

    -- Skenario 5 & 6 (Ascending Lock & Cross-Lock Deadlock 40P01)
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN5-A-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_5_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_5_id, '3000E280116060000219ABCDEF05', 'Perlak', 'Perlak A');

    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN5-B-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_6_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_6_id, '3000E280116060000219ABCDEF06', 'Perlak', 'Perlak B');

    -- Skenario 7 (Dua Supplier INSERT Normalized EPC Bersamaan)
    INSERT INTO public.pengiriman_linen (verification_code, supplier, status, hospital_id, tanggal)
    VALUES ('VC-SCEN7-' || substr(gen_random_uuid()::text, 1, 6), 'Supplier Alpha', 'pending', 101, clock_timestamp())
    RETURNING id INTO v_pkg_7_id;

    INSERT INTO public.detail_pengiriman_linen (pengiriman_id, epc, kategori, nama_linen)
    VALUES (v_pkg_7_id, '3000E280116060000219ABCDEF07', 'Sarung Bantal', 'Sarung Bantal Standard');

    INSERT INTO _test_fixture_tracker (entity_type, entity_id, label)
    VALUES 
        ('public.pengiriman_linen', v_pkg_1_id, 'SCEN1_PKG'),
        ('public.pengiriman_linen', v_pkg_2_id, 'SCEN2_PKG'),
        ('public.pengiriman_linen', v_pkg_3_id, 'SCEN3_PKG'),
        ('public.pengiriman_linen', v_pkg_4_id, 'SCEN4_PKG'),
        ('public.pengiriman_linen', v_pkg_5_id, 'SCEN5_PKG_A'),
        ('public.pengiriman_linen', v_pkg_6_id, 'SCEN5_PKG_B'),
        ('public.pengiriman_linen', v_pkg_7_id, 'SCEN7_PKG')
    ON CONFLICT (label) DO UPDATE SET entity_id = EXCLUDED.entity_id;

    RAISE NOTICE 'Provisioning fixture dinamis R2.10.4.4 SELESAI.';
END $$;

