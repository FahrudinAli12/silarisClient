-- =============================================================================
-- ALL-IN-ONE SUPABASE STAGING BOOTSTRAP SCRIPT (GATE B - FINAL VERIFIED REV4)
-- =============================================================================
-- Environment      : Supabase Cloud Database (PostgreSQL 15+)
-- Target Project   : supplier-clients-staging (Ref: bgtxpdydljfeltdimmxr)
-- File Target      : 00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql
-- Versi            : REV4 (FINAL VERIFIED - MANDAT AUDIT REVISI 4)
-- Tanggal Rilis    : 15 November 2026
--
-- KOREKSI UTAMA REV4 (MEMENUHI MANDAT PERBAIKAN A.1 - A.8):
-- 1. Normalisasi Metadata Manifest Hash SQL identik Python (Whitespace regex + Unicode NFC).
-- 2. Trigger Detail Immutability aman dari race condition (Row locking FOR SHARE pada pengiriman_linen).
-- 3. confirm_package() memverifikasi ulang manifest aktual dari tabel detail sebelum konfirmasi.
-- 4. Variabel kanonikal tunggal v_canonical_payload_digest untuk validasi, audit, idempotensi, & insert.
-- 5. Relokasi ekstensi pgcrypto & uuid-ossp ke skema extensions secara aman (ALTER EXTENSION SET SCHEMA).
-- 6. Prosedur pendaftaran membership admin menggunakan UUID auth.users Supabase (Anti-UUID fiktif).
-- 7. Audit trail komprehensif, rollback atomik, & penegakan integritas foreign key.
-- 8. Audit SECURITY DEFINER, search_path eksplisit, RLS multi-tenant, dan hak akses least privilege.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. EXTENSIONS SETUP & SCHEMA ISOLATION (MANDAT REV4 A.5)
-- -----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS extensions;

DO $$
DECLARE
    v_pgcrypto_schema text;
    v_uuid_schema text;
BEGIN
    -- Periksa lokasi aktual pgcrypto dan relokasi ke schema extensions jika berada di public
    SELECT n.nspname INTO v_pgcrypto_schema
    FROM pg_extension e
    JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE e.extname = 'pgcrypto';

    IF v_pgcrypto_schema IS NULL THEN
        CREATE EXTENSION "pgcrypto" WITH SCHEMA extensions;
    ELSIF v_pgcrypto_schema != 'extensions' THEN
        EXECUTE 'ALTER EXTENSION "pgcrypto" SET SCHEMA extensions';
    END IF;

    -- Periksa lokasi aktual uuid-ossp dan relokasi ke schema extensions jika berada di public
    SELECT n.nspname INTO v_uuid_schema
    FROM pg_extension e
    JOIN pg_namespace n ON n.oid = e.extnamespace
    WHERE e.extname = 'uuid-ossp';

    IF v_uuid_schema IS NULL THEN
        CREATE EXTENSION "uuid-ossp" WITH SCHEMA extensions;
    ELSIF v_uuid_schema != 'extensions' THEN
        EXECUTE 'ALTER EXTENSION "uuid-ossp" SET SCHEMA extensions';
    END IF;
END $$;

GRANT USAGE ON SCHEMA extensions TO authenticated, service_role, anon;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA extensions TO authenticated, service_role, anon;

-- -----------------------------------------------------------------------------
-- 2. MASTER TABEL RUMAH SAKIT & MULTI-TENANT MEMBERSHIP
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.rumah_sakit (
    id BIGSERIAL PRIMARY KEY,
    kode_rs TEXT NOT NULL UNIQUE,
    nama_rs TEXT NOT NULL,
    alamat TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.hospital_memberships (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID NOT NULL,
    hospital_id BIGINT NOT NULL REFERENCES public.rumah_sakit(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'operator' CHECK (role IN ('admin', 'operator', 'auditor')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_hospital_membership_user UNIQUE (user_id, hospital_id)
);

CREATE INDEX IF NOT EXISTS idx_hospital_memberships_user_id ON public.hospital_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_hospital_memberships_hosp_active ON public.hospital_memberships(hospital_id, is_active);

-- -----------------------------------------------------------------------------
-- 2.1 PROSEDUR REGISTRASI MEMBERSHIP RUMAH SAKIT SUPABASE AUTH (MANDAT REV4 A.6)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.register_hospital_admin_membership(
    p_email TEXT,
    p_hospital_id BIGINT,
    p_role TEXT DEFAULT 'admin'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_membership_id BIGINT;
    v_clean_role TEXT;
BEGIN
    v_clean_role := lower(btrim(coalesce(p_role, 'admin')));
    IF v_clean_role NOT IN ('admin', 'operator', 'auditor') THEN
        RETURN jsonb_build_object('success', FALSE, 'error', 'INVALID_ROLE', 'message', 'Peran harus admin, operator, atau auditor');
    END IF;

    -- Cari UUID pengguna asli pada auth.users berdasarkan email
    SELECT id INTO v_user_id
    FROM auth.users
    WHERE lower(email) = lower(btrim(p_email));

    IF v_user_id IS NULL THEN
        RETURN jsonb_build_object(
            'success', FALSE,
            'error', 'USER_NOT_FOUND',
            'message', 'Pengguna dengan email ' || p_email || ' tidak ditemukan di auth.users Supabase. Pengguna wajib mendaftar akun terlebih dahulu.'
        );
    END IF;

    INSERT INTO public.hospital_memberships (
        hospital_id,
        user_id,
        role,
        is_active,
        created_at,
        updated_at
    ) VALUES (
        p_hospital_id,
        v_user_id,
        v_clean_role,
        TRUE,
        now(),
        now()
    )
    ON CONFLICT (user_id, hospital_id) DO UPDATE
    SET role = EXCLUDED.role,
        is_active = TRUE,
        updated_at = now()
    RETURNING id INTO v_membership_id;

    RETURN jsonb_build_object(
        'success', TRUE,
        'membership_id', v_membership_id,
        'hospital_id', p_hospital_id,
        'user_id', v_user_id,
        'email', p_email,
        'role', v_clean_role,
        'message', 'Membership rumah sakit berhasil didaftarkan secara sah untuk akun Supabase Auth'
    );
END;
$$;

CREATE OR REPLACE FUNCTION public.register_hospital_membership_by_uuid(
    p_user_id UUID,
    p_hospital_id BIGINT,
    p_role TEXT DEFAULT 'admin'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, auth, pg_temp
AS $$
DECLARE
    v_membership_id BIGINT;
    v_clean_role TEXT;
    v_exists BOOLEAN;
BEGIN
    v_clean_role := lower(btrim(coalesce(p_role, 'admin')));
    IF v_clean_role NOT IN ('admin', 'operator', 'auditor') THEN
        RETURN jsonb_build_object('success', FALSE, 'error', 'INVALID_ROLE', 'message', 'Peran harus admin, operator, atau auditor');
    END IF;

    SELECT EXISTS (SELECT 1 FROM auth.users WHERE id = p_user_id) INTO v_exists;
    IF NOT v_exists THEN
        RETURN jsonb_build_object(
            'success', FALSE,
            'error', 'USER_NOT_FOUND',
            'message', 'UUID pengguna tidak ditemukan di auth.users Supabase'
        );
    END IF;

    INSERT INTO public.hospital_memberships (
        hospital_id,
        user_id,
        role,
        is_active,
        created_at,
        updated_at
    ) VALUES (
        p_hospital_id,
        p_user_id,
        v_clean_role,
        TRUE,
        now(),
        now()
    )
    ON CONFLICT (user_id, hospital_id) DO UPDATE
    SET role = EXCLUDED.role,
        is_active = TRUE,
        updated_at = now()
    RETURNING id INTO v_membership_id;

    RETURN jsonb_build_object(
        'success', TRUE,
        'membership_id', v_membership_id,
        'hospital_id', p_hospital_id,
        'user_id', p_user_id,
        'role', v_clean_role,
        'message', 'Membership rumah sakit berhasil didaftarkan via UUID'
    );
END;
$$;

-- -----------------------------------------------------------------------------
-- 3. MASTER TABEL SUPPLIER, KONTRAK KERJASAMA, & PERANGKAT RFID
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.suppliers (
    supplier_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_code TEXT NOT NULL UNIQUE,
    supplier_name TEXT NOT NULL,
    contact_email TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.supplier_hospital_contracts (
    contract_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_number TEXT NOT NULL UNIQUE,
    supplier_id UUID NOT NULL REFERENCES public.suppliers(supplier_id) ON DELETE RESTRICT,
    hospital_id BIGINT NOT NULL REFERENCES public.rumah_sakit(id) ON DELETE RESTRICT,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_contract_date_range CHECK (valid_until > valid_from)
);

-- Partial Unique Index: Hanya 1 Kontrak Berstatus ACTIVE per Pasangan (Supplier, RS)
-- Desain ini mendukung histori perpanjangan kontrak (kontrak lama is_active=false tetap tersimpan)
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_hospital_single_active_contract
ON public.supplier_hospital_contracts (supplier_id, hospital_id)
WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS public.registered_devices (
    device_id TEXT PRIMARY KEY,
    supplier_id UUID NOT NULL REFERENCES public.suppliers(supplier_id) ON DELETE RESTRICT,
    device_name TEXT NOT NULL,
    device_type TEXT NOT NULL DEFAULT 'RFID_SCANNER_GATEWAY',
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'DECOMMISSIONED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.device_keys (
    key_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id TEXT NOT NULL REFERENCES public.registered_devices(device_id) ON DELETE CASCADE,
    public_key_ed25519 TEXT NOT NULL UNIQUE,
    key_algorithm TEXT NOT NULL DEFAULT 'Ed25519' CHECK (key_algorithm IN ('Ed25519')),
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'EXPIRED', 'REVOKED')),
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_device_keys_date_range CHECK (valid_until > valid_from)
);

-- Partial Unique Index: Hanya 1 Kunci Berstatus ACTIVE per Perangkat
CREATE UNIQUE INDEX IF NOT EXISTS uq_device_keys_single_active
ON public.device_keys (device_id)
WHERE status = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_device_keys_lookup ON public.device_keys(device_id, status);

CREATE TABLE IF NOT EXISTS public.device_nonces (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES public.registered_devices(device_id) ON DELETE CASCADE,
    nonce TEXT NOT NULL,
    used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_device_nonce UNIQUE (device_id, nonce)
);

CREATE INDEX IF NOT EXISTS idx_device_nonces_expiry ON public.device_nonces(expires_at);

-- -----------------------------------------------------------------------------
-- 4. TABEL PENGIRIMAN LINEN & DETAIL ITEM (CLOUD REPOSITORI)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.pengiriman_linen (
    id BIGSERIAL PRIMARY KEY,
    kode_pengiriman TEXT NOT NULL UNIQUE,
    verification_code TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'claimed', 'confirmed', 'cancelled')),
    hospital_id BIGINT NOT NULL REFERENCES public.rumah_sakit(id) ON DELETE RESTRICT,
    device_id TEXT NOT NULL REFERENCES public.registered_devices(device_id) ON DELETE RESTRICT,
    idempotency_key TEXT,
    payload_digest TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    catatan TEXT,
    claimed_by_operation_id TEXT,
    claimed_by_installation_uid TEXT,
    claimed_by_user_id UUID,
    claimed_at TIMESTAMPTZ,
    claim_expires_at TIMESTAMPTZ,
    confirmed_by_operation_id TEXT,
    confirmed_by_installation_uid TEXT,
    confirmed_by_user_id UUID,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_pengiriman_linen_payload_digest CHECK (payload_digest ~* '^[0-9a-f]{64}$'),
    CONSTRAINT chk_pengiriman_linen_manifest_hash CHECK (manifest_hash ~* '^[0-9a-f]{64}$'),
    CONSTRAINT chk_pengiriman_linen_idempotency_not_empty CHECK (idempotency_key IS NULL OR length(btrim(idempotency_key)) > 0)
);

-- Partial Unique Index untuk Idempotensi Non-Empty
CREATE UNIQUE INDEX IF NOT EXISTS uq_pengiriman_linen_device_idempotency
ON public.pengiriman_linen (device_id, idempotency_key)
WHERE idempotency_key IS NOT NULL AND idempotency_key != '';

CREATE INDEX IF NOT EXISTS idx_pengiriman_linen_hosp_status ON public.pengiriman_linen(hospital_id, status);
CREATE INDEX IF NOT EXISTS idx_pengiriman_linen_created ON public.pengiriman_linen(created_at DESC);

CREATE TABLE IF NOT EXISTS public.detail_pengiriman_linen (
    id BIGSERIAL PRIMARY KEY,
    pengiriman_id BIGINT NOT NULL REFERENCES public.pengiriman_linen(id) ON DELETE CASCADE,
    epc TEXT NOT NULL,
    nama_linen TEXT NOT NULL DEFAULT '',
    kategori TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_detail_pengiriman_linen_pkg_epc UNIQUE (pengiriman_id, epc),
    CONSTRAINT chk_detail_pengiriman_linen_epc_24_hex CHECK (epc ~ '^[0-9A-F]{24}$')
);

CREATE INDEX IF NOT EXISTS idx_detail_pengiriman_linen_epc ON public.detail_pengiriman_linen(epc);
CREATE INDEX IF NOT EXISTS idx_detail_pengiriman_linen_pkg ON public.detail_pengiriman_linen(pengiriman_id);

-- -----------------------------------------------------------------------------
-- 5. TABEL MASTER LINEN RUMAH SAKIT & AUDIT LOG
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.linen_master (
    id BIGSERIAL PRIMARY KEY,
    epc TEXT NOT NULL UNIQUE,
    nama_linen TEXT NOT NULL DEFAULT '',
    kategori TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'MAINTENANCE', 'DISCARDED')),
    hospital_id BIGINT NOT NULL REFERENCES public.rumah_sakit(id) ON DELETE RESTRICT,
    last_scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_linen_master_epc_24_hex CHECK (epc ~ '^[0-9A-F]{24}$')
);

CREATE INDEX IF NOT EXISTS idx_linen_master_hosp_status ON public.linen_master(hospital_id, status);

CREATE TABLE IF NOT EXISTS public.linen_audit_log (
    id BIGSERIAL PRIMARY KEY,
    epc TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id TEXT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_linen_audit_log_epc ON public.linen_audit_log(epc);
CREATE INDEX IF NOT EXISTS idx_linen_audit_log_created ON public.linen_audit_log(created_at DESC);

-- -----------------------------------------------------------------------------
-- 6. TABEL AUDIT INGESTION SUPPLIER (PERMANENT LOGGING)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.supplier_ingestion_audit (
    id BIGSERIAL PRIMARY KEY,
    device_id TEXT,
    package_code TEXT,
    idempotency_key TEXT,
    payload_digest TEXT,
    status TEXT NOT NULL,
    http_status_code INTEGER NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_supplier_ingestion_audit_dev ON public.supplier_ingestion_audit(device_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- 7. FUNGSI EPC NORMALIZATION (18 GOLDEN TEST VECTORS COMPLIANT - CALLED ON NULL INPUT)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.clean_epc(p_epc TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
CALLED ON NULL INPUT
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_trimmed TEXT;
    v_clean TEXT;
    v_ch TEXT;
    v_i INTEGER;
    v_code INTEGER;
BEGIN
    -- Validasi eksplisit NULL (CALLED ON NULL INPUT memastikan pengecekan ini tidak dilewati)
    IF p_epc IS NULL THEN
        RAISE EXCEPTION 'EMPTY_EPC: Tag EPC tidak boleh kosong.' USING ERRCODE = '22004';
    END IF;

    -- 1. Stripping 29 codepoints whitespace Unicode & ASCII
    -- Menggunakan representasi heksadesimal standar PostgreSQL ( untuk vertical tab)
    v_trimmed := btrim(p_epc, E' \t\n\r\x0B\x0C\u0085\u00A0\u1680\u180E\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u200B\u200C\u200D\u2028\u2029\u202F\u205F\u3000\uFEFF');
    IF length(v_trimmed) = 0 THEN
        RAISE EXCEPTION 'EMPTY_EPC: Tag EPC kosong setelah pembersihan spasi.' USING ERRCODE = '22004';
    END IF;

    -- 2. Tolak karakter non-ASCII (U+0080+) dan karakter kontrol ASCII terlarang (U+001C..U+001F)
    FOR v_i IN 1..length(v_trimmed) LOOP
        v_ch := substr(v_trimmed, v_i, 1);
        v_code := ascii(v_ch);
        IF v_code > 127 THEN
            RAISE EXCEPTION 'NON_ASCII_EPC: Tag EPC mengandung karakter non-ASCII (U+%)', to_hex(v_code) USING ERRCODE = '22021';
        END IF;
        IF v_code >= 28 AND v_code <= 31 THEN
            RAISE EXCEPTION 'INVALID_EPC_FORMAT: Mengandung karakter kontrol ASCII terlarang.' USING ERRCODE = '22023';
        END IF;
    END LOOP;

    -- 3. Konversi Uppercase
    v_clean := upper(v_trimmed);

    -- 4. Pemotongan prefix 3000 hanya pada format legacy 28 karakter
    IF length(v_clean) = 28 AND v_clean LIKE '3000%' THEN
        v_clean := substr(v_clean, 5);
    END IF;

    -- 5. Validasi akhir format tepat 24 karakter heksadesimal
    IF length(v_clean) != 24 OR v_clean !~ '^[0-9A-F]{24}$' THEN
        RAISE EXCEPTION 'INVALID_EPC_FORMAT: Format EPC tidak valid (Wajib 24 karakter hexadecimal).' USING ERRCODE = '22023';
    END IF;

    RETURN v_clean;
END;
$$;

-- -----------------------------------------------------------------------------
-- 7.2 NORMALISASI METADATA TEKS KANONIKAL (UNICODE NFC & TRIMMING WHITESPACE - MANDAT REV4 A.1)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.clean_metadata_text(p_val TEXT)
RETURNS TEXT
LANGUAGE plpgsql
IMMUTABLE
CALLED ON NULL INPUT
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_trimmed TEXT;
BEGIN
    IF p_val IS NULL THEN
        RETURN '';
    END IF;
    -- Pangkas seluruh whitespace pembuka dan penutup (spasi, tab, newline, carriage return, Unicode spaces)
    v_trimmed := regexp_replace(regexp_replace(p_val, '^\s+', '', 'g'), '\s+$', '', 'g');
    IF v_trimmed = '' THEN
        RETURN '';
    END IF;
    -- Normalisasi representasi Unicode ke Canonical Composition (NFC)
    RETURN normalize(v_trimmed, NFC);
END;
$$;

-- -----------------------------------------------------------------------------
-- 8. FUNGSI HITUNG KANONIKAL MANIFEST HASH (LENGTH-PREFIXED DETERMINISTIK)
-- -----------------------------------------------------------------------------
-- 8.1 Hitung Manifest Hash Langsung Dari Array JSONB (In-Memory Pre-computation)
CREATE OR REPLACE FUNCTION public.compute_manifest_hash_from_items(p_items JSONB)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_canonical_stream BYTEA := ''::BYTEA;
    v_prev_epc TEXT := NULL;
    r RECORD;
    v_epc_bytes BYTEA;
    v_kat_bytes BYTEA;
    v_nama_bytes BYTEA;
    v_hash TEXT;
BEGIN
    IF p_items IS NULL OR jsonb_typeof(p_items) != 'array' OR jsonb_array_length(p_items) = 0 THEN
        RAISE EXCEPTION 'EMPTY_MANIFEST: p_items wajib berupa JSON array tidak kosong' USING ERRCODE = '22023';
    END IF;

    FOR r IN (
        SELECT 
            public.clean_epc(item->>'epc') AS epc_clean,
            public.clean_metadata_text(item->>'kategori') AS kategori_clean,
            public.clean_metadata_text(item->>'nama_linen') AS nama_linen_clean
        FROM jsonb_array_elements(p_items) AS item
        ORDER BY public.clean_epc(item->>'epc') ASC
    ) LOOP
        IF v_prev_epc IS NOT NULL AND r.epc_clean = v_prev_epc THEN
            RAISE EXCEPTION 'DUPLICATE_MANIFEST_EPC: Tag EPC % duplikat dalam paket', r.epc_clean USING ERRCODE = '23505';
        END IF;
        v_prev_epc := r.epc_clean;

        v_epc_bytes := convert_to(r.epc_clean, 'UTF8');
        v_kat_bytes := convert_to(r.kategori_clean, 'UTF8');
        v_nama_bytes := convert_to(r.nama_linen_clean, 'UTF8');

        v_canonical_stream := v_canonical_stream || 
            convert_to(octet_length(v_epc_bytes)::text || ':', 'UTF8') || v_epc_bytes ||
            convert_to(':' || octet_length(v_kat_bytes)::text || ':', 'UTF8') || v_kat_bytes ||
            convert_to(':' || octet_length(v_nama_bytes)::text || ':', 'UTF8') || v_nama_bytes ||
            convert_to(';', 'UTF8');
    END LOOP;

    v_hash := encode(extensions.digest(v_canonical_stream, 'sha256'), 'hex');
    RETURN lower(v_hash);
END;
$$;

-- 8.2 Hitung Manifest Hash Berdasarkan Baris detail_pengiriman_linen (100% Paritas dengan 8.1)
CREATE OR REPLACE FUNCTION public.compute_package_manifest_hash(p_package_id BIGINT)
RETURNS TEXT
LANGUAGE plpgsql
STABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $$
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

    SELECT count(*) INTO v_item_count
    FROM public.detail_pengiriman_linen
    WHERE pengiriman_id = p_package_id;

    IF v_item_count = 0 THEN
        RAISE EXCEPTION 'EMPTY_MANIFEST: Paket % tidak memiliki item linen', p_package_id USING ERRCODE = '22023';
    END IF;

    FOR r IN (
        SELECT 
            public.clean_epc(d.epc) AS epc_clean,
            public.clean_metadata_text(d.kategori) AS kategori_clean,
            public.clean_metadata_text(d.nama_linen) AS nama_linen_clean
        FROM public.detail_pengiriman_linen d
        WHERE d.pengiriman_id = p_package_id
        ORDER BY public.clean_epc(d.epc) ASC
    ) LOOP
        IF v_prev_epc IS NOT NULL AND r.epc_clean = v_prev_epc THEN
            RAISE EXCEPTION 'DUPLICATE_MANIFEST_EPC: Tag EPC % duplikat pada paket %', r.epc_clean, p_package_id USING ERRCODE = '23505';
        END IF;
        v_prev_epc := r.epc_clean;

        v_epc_bytes := convert_to(r.epc_clean, 'UTF8');
        v_kat_bytes := convert_to(r.kategori_clean, 'UTF8');
        v_nama_bytes := convert_to(r.nama_linen_clean, 'UTF8');

        v_canonical_stream := v_canonical_stream || 
            convert_to(octet_length(v_epc_bytes)::text || ':', 'UTF8') || v_epc_bytes ||
            convert_to(':' || octet_length(v_kat_bytes)::text || ':', 'UTF8') || v_kat_bytes ||
            convert_to(':' || octet_length(v_nama_bytes)::text || ':', 'UTF8') || v_nama_bytes ||
            convert_to(';', 'UTF8');
    END LOOP;

    v_hash := encode(extensions.digest(v_canonical_stream, 'sha256'), 'hex');
    RETURN lower(v_hash);
END;
$$;

-- -----------------------------------------------------------------------------
-- 9. TRIGGER IMMUTABILITY INTEGRITAS DATA PAKET & DETAIL
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_detail_pengiriman_linen_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_pkg_status TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Mandat REV4 A.2: Kunci baris parent pengiriman_linen secara shared untuk mencegah race condition
        SELECT status INTO v_pkg_status 
        FROM public.pengiriman_linen 
        WHERE id = NEW.pengiriman_id 
        FOR SHARE;

        IF v_pkg_status IS NULL THEN
            RAISE EXCEPTION 'DETAIL_INSERT_PACKAGE_NOT_FOUND: Paket pengiriman % tidak ditemukan', NEW.pengiriman_id
                USING ERRCODE = '23503';
        ELSIF v_pkg_status != 'pending' THEN
            RAISE EXCEPTION 'DETAIL_INSERT_FORBIDDEN: Tidak dapat menambahkan item ke paket berstatus "%"', v_pkg_status
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'DETAIL_IMMUTABLE_MUTATION: Pembaruan baris detail_pengiriman_linen dilarang demi integritas manifest hash'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        -- Mandat REV4 A.2: Kunci baris parent secara shared saat penghapusan detail item
        SELECT status INTO v_pkg_status 
        FROM public.pengiriman_linen 
        WHERE id = OLD.pengiriman_id 
        FOR SHARE;

        IF v_pkg_status IS NOT NULL AND v_pkg_status != 'pending' THEN
            RAISE EXCEPTION 'DETAIL_IMMUTABLE_DELETION: Detail paket berstatus "%" dilarang dihapus', v_pkg_status
                USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_detail_pengiriman_linen_immutability ON public.detail_pengiriman_linen;
CREATE TRIGGER trg_detail_pengiriman_linen_immutability
    BEFORE INSERT OR UPDATE OR DELETE ON public.detail_pengiriman_linen
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_detail_pengiriman_linen_immutability();

CREATE OR REPLACE FUNCTION public.fn_pengiriman_linen_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- 1. Status terminal tidak dapat diubah
        IF OLD.status = 'confirmed' AND NEW.status != 'confirmed' THEN
            RAISE EXCEPTION 'HEADER_TERMINAL_MUTATION: Paket yang telah berstatus confirmed dilarang diubah statusnya'
                USING ERRCODE = '23514';
        END IF;

        IF OLD.status = 'cancelled' AND NEW.status != 'cancelled' THEN
            RAISE EXCEPTION 'HEADER_TERMINAL_MUTATION: Paket yang telah berstatus cancelled dilarang diubah statusnya'
                USING ERRCODE = '23514';
        END IF;

        -- 2. Validasi transisi status legal
        IF OLD.status = 'pending' AND NEW.status NOT IN ('pending', 'claimed', 'cancelled') THEN
            RAISE EXCEPTION 'ILLEGAL_STATE_TRANSITION: Paket pending hanya dapat berpindah ke claimed atau cancelled'
                USING ERRCODE = '23514';
        END IF;

        IF OLD.status = 'claimed' AND NEW.status NOT IN ('claimed', 'confirmed', 'cancelled', 'pending') THEN
            RAISE EXCEPTION 'ILLEGAL_STATE_TRANSITION: Paket claimed hanya dapat berpindah ke confirmed, cancelled, atau pending'
                USING ERRCODE = '23514';
        END IF;

        -- 3. Proteksi kolom identitas dan integritas paket
        IF OLD.kode_pengiriman IS DISTINCT FROM NEW.kode_pengiriman THEN
            RAISE EXCEPTION 'IMMUTABLE_FIELD: kode_pengiriman bersifat permanen dan dilarang diubah' USING ERRCODE = '23514';
        END IF;

        IF OLD.hospital_id IS DISTINCT FROM NEW.hospital_id THEN
            RAISE EXCEPTION 'IMMUTABLE_FIELD: hospital_id bersifat permanen dan dilarang diubah' USING ERRCODE = '23514';
        END IF;

        IF OLD.device_id IS DISTINCT FROM NEW.device_id THEN
            RAISE EXCEPTION 'IMMUTABLE_FIELD: device_id bersifat permanen dan dilarang diubah' USING ERRCODE = '23514';
        END IF;

        IF OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key THEN
            RAISE EXCEPTION 'IMMUTABLE_FIELD: idempotency_key bersifat permanen dan dilarang diubah' USING ERRCODE = '23514';
        END IF;

        IF OLD.payload_digest IS DISTINCT FROM NEW.payload_digest THEN
            RAISE EXCEPTION 'IMMUTABLE_FIELD: payload_digest bersifat permanen dan dilarang diubah' USING ERRCODE = '23514';
        END IF;

        IF OLD.verification_code IS DISTINCT FROM NEW.verification_code THEN
            RAISE EXCEPTION 'IMMUTABLE_FIELD: verification_code bersifat permanen dan dilarang diubah' USING ERRCODE = '23514';
        END IF;

        -- 4. Manifest hash tidak boleh diubah setelah paket dibuat
        IF OLD.manifest_hash IS NOT NULL AND NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash THEN
            RAISE EXCEPTION 'MANIFEST_HASH_MUTATION: manifest_hash bersifat immutable setelah ingestion'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END IF;

    -- Perilaku DELETE: Kembalikan OLD agar penghapusan diizinkan, atau tolak jika berstatus claimed/confirmed
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('claimed', 'confirmed') THEN
            RAISE EXCEPTION 'HEADER_DELETE_FORBIDDEN: Paket berstatus "%" tidak dapat dihapus', OLD.status USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_pengiriman_linen_immutability ON public.pengiriman_linen;
CREATE TRIGGER trg_pengiriman_linen_immutability
    BEFORE UPDATE OR DELETE ON public.pengiriman_linen
    FOR EACH ROW
    EXECUTE FUNCTION public.fn_pengiriman_linen_immutability();

-- -----------------------------------------------------------------------------
-- 10. RPC INGESTION SUPPLIER: public.ingest_supplier_package (SECURITY DEFINER)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.ingest_supplier_package(
    p_device_id TEXT,
    p_package_code TEXT,
    p_hospital_code TEXT,
    p_idempotency_key TEXT,
    p_payload_digest TEXT,
    p_catatan TEXT,
    p_items JSONB
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_clean_idempotency_key TEXT;
    v_canonical_payload_digest TEXT;
    v_supplier_id UUID;
    v_hospital_id BIGINT;
    v_contract_id UUID;
    v_existing_package RECORD;
    v_package_id BIGINT;
    v_verification_code TEXT;
    v_item JSONB;
    v_computed_manifest_hash TEXT;
BEGIN
    -- Mandat REV4 A.4: Normalisasi idempotency_key & payload_digest menjadi variabel kanonikal tunggal
    v_clean_idempotency_key := NULLIF(btrim(p_idempotency_key), '');
    v_canonical_payload_digest := lower(btrim(coalesce(p_payload_digest, '')));

    -- 1. Atomic Transaction-Level Advisory Lock untuk menjamin serialisasi idempotency & concurrency
    PERFORM pg_advisory_xact_lock(hashtext('ingest:' || coalesce(p_device_id, '') || ':' || coalesce(v_clean_idempotency_key, p_package_code, '')));

    -- 2. Validasi Argumen Wajib
    IF p_device_id IS NULL OR btrim(p_device_id) = '' THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_INVALID_ARGUMENT', 400, 'p_device_id wajib diisi'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'INVALID_ARGUMENT', 'http_status', 400, 'error', 'p_device_id wajib diisi');
    END IF;

    IF p_package_code IS NULL OR btrim(p_package_code) = '' THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_INVALID_ARGUMENT', 400, 'p_package_code wajib diisi'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'INVALID_ARGUMENT', 'http_status', 400, 'error', 'p_package_code wajib diisi');
    END IF;

    IF p_hospital_code IS NULL OR btrim(p_hospital_code) = '' THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_INVALID_ARGUMENT', 400, 'p_hospital_code wajib diisi'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'INVALID_ARGUMENT', 'http_status', 400, 'error', 'p_hospital_code wajib diisi');
    END IF;

    -- Validasi Format SHA-256 64-hex pada payload_digest (Mandat REV4 A.4)
    IF v_canonical_payload_digest = '' OR length(v_canonical_payload_digest) != 64 OR v_canonical_payload_digest !~ '^[0-9a-f]{64}$' THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_INVALID_ARGUMENT', 400, 'p_payload_digest wajib 64 hex SHA-256'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'INVALID_ARGUMENT', 'http_status', 400, 'error', 'p_payload_digest wajib 64 hex SHA-256');
    END IF;

    IF p_items IS NULL OR jsonb_typeof(p_items) != 'array' OR jsonb_array_length(p_items) = 0 THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_INVALID_ARGUMENT', 400, 'p_items wajib berupa JSON array tidak kosong'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'INVALID_ARGUMENT', 'http_status', 400, 'error', 'p_items wajib berupa JSON array tidak kosong');
    END IF;

    -- Validasi Setiap Elemen JSONB p_items
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_items) LOOP
        IF jsonb_typeof(v_item) != 'object' OR v_item->>'epc' IS NULL OR btrim(v_item->>'epc') = '' THEN
            INSERT INTO public.supplier_ingestion_audit (
                device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
            ) VALUES (
                p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_INVALID_ITEM_FORMAT', 400, 'Item JSON harus berupa objek dan memiliki atribut epc valid'
            );
            RETURN jsonb_build_object('success', FALSE, 'status', 'INVALID_ITEM_FORMAT', 'http_status', 400, 'error', 'Item JSON harus berupa objek dan memiliki atribut epc valid');
        END IF;
    END LOOP;

    -- 3. Verifikasi Status Perangkat & SUPPLIER AKTIF (Mandat A.2: suppliers.is_active wajib TRUE)
    SELECT rd.supplier_id INTO v_supplier_id
    FROM public.registered_devices rd
    JOIN public.suppliers s ON s.supplier_id = rd.supplier_id
    WHERE rd.device_id = p_device_id 
      AND rd.status = 'ACTIVE'
      AND s.is_active = TRUE;

    IF v_supplier_id IS NULL THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_DEVICE_OR_SUPPLIER_INACTIVE', 403, 'Device tidak aktif atau Supplier berstatus nonaktif'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'DEVICE_OR_SUPPLIER_INACTIVE', 'http_status', 403, 'error', 'Perangkat tidak aktif atau Supplier berstatus nonaktif');
    END IF;

    -- 4. Verifikasi Rumah Sakit Tujuan
    SELECT rs.id INTO v_hospital_id
    FROM public.rumah_sakit rs
    WHERE rs.kode_rs = p_hospital_code AND rs.is_active = TRUE;

    IF v_hospital_id IS NULL THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_HOSPITAL_NOT_FOUND', 404, 'Rumah sakit tidak ditemukan atau nonaktif'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'HOSPITAL_NOT_FOUND', 'http_status', 404, 'error', 'Rumah sakit tidak terdaftar atau non-aktif');
    END IF;

    -- 5. Verifikasi Kontrak Aktif Supplier - Rumah Sakit
    SELECT shc.contract_id INTO v_contract_id
    FROM public.supplier_hospital_contracts shc
    WHERE shc.supplier_id = v_supplier_id 
      AND shc.hospital_id = v_hospital_id 
      AND shc.is_active = TRUE
      AND now() BETWEEN shc.valid_from AND shc.valid_until;

    IF v_contract_id IS NULL THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_CONTRACT_INACTIVE', 403, 'Kontrak supplier-RS tidak aktif atau kedaluwarsa'
        );
        RETURN jsonb_build_object('success', FALSE, 'status', 'CONTRACT_INACTIVE', 'http_status', 403, 'error', 'Tidak ada kontrak aktif antara supplier dan rumah sakit');
    END IF;

    -- 6. Penanganan Idempotensi Tingkat Header Transaksi (Menggunakan v_clean_idempotency_key)
    IF v_clean_idempotency_key IS NOT NULL THEN
        SELECT id, payload_digest, manifest_hash, verification_code, status
        INTO v_existing_package
        FROM public.pengiriman_linen
        WHERE device_id = p_device_id AND idempotency_key = v_clean_idempotency_key;

        IF v_existing_package.id IS NOT NULL THEN
            IF lower(v_existing_package.payload_digest) = v_canonical_payload_digest THEN
                INSERT INTO public.supplier_ingestion_audit (
                    device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
                ) VALUES (
                    p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'ALREADY_INGESTED', 200, NULL
                );
                RETURN jsonb_build_object(
                    'success', TRUE,
                    'status', 'ALREADY_INGESTED',
                    'idempotent', TRUE,
                    'http_status', 200,
                    'package_id', v_existing_package.id,
                    'package_code', p_package_code,
                    'manifest_hash', v_existing_package.manifest_hash,
                    'verification_code', v_existing_package.verification_code,
                    'message', 'Paket telah terdaftar sebelumnya dengan payload identik (Idempotent Replay)'
                );
            ELSE
                INSERT INTO public.supplier_ingestion_audit (
                    device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
                ) VALUES (
                    p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'IDEMPOTENCY_PAYLOAD_MISMATCH', 409, 'Konflik payload digest pada idempotency key sama'
                );
                RETURN jsonb_build_object(
                    'success', FALSE,
                    'status', 'IDEMPOTENCY_PAYLOAD_MISMATCH',
                    'http_status', 409,
                    'error', 'SE001: Idempotency key sudah terdaftar dengan payload digest berbeda'
                );
            END IF;
        END IF;
    END IF;

    -- 7. Pengecekan Kode Paket Unik
    SELECT id INTO v_package_id FROM public.pengiriman_linen WHERE kode_pengiriman = p_package_code;
    IF v_package_id IS NOT NULL THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_DUPLICATE_PACKAGE_CODE', 409, 'Kode pengiriman sudah pernah terdaftar'
        );
        RETURN jsonb_build_object(
            'success', FALSE,
            'status', 'DUPLICATE_PACKAGE_CODE',
            'http_status', 409,
            'error', 'Kode pengiriman sudah pernah terdaftar'
        );
    END IF;

    -- 8. Pre-komputasi Kanonikal Manifest Hash Sebelum Header Insert
    BEGIN
        v_computed_manifest_hash := public.compute_manifest_hash_from_items(p_items);
    EXCEPTION WHEN OTHERS THEN
        INSERT INTO public.supplier_ingestion_audit (
            device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
        ) VALUES (
            p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'REJECTED_MANIFEST_ERROR', 400, SQLERRM
        );
        RETURN jsonb_build_object(
            'success', FALSE,
            'status', 'INVALID_MANIFEST',
            'http_status', 400,
            'error', SQLERRM
        );
    END;

    -- 9. Generate Verification Code Unik (Format VC-XXXXXX)
    v_verification_code := 'VC-' || upper(substr(encode(extensions.gen_random_bytes(6), 'hex'), 1, 8));

    -- 10. Insert Header Paket dengan manifest_hash final
    INSERT INTO public.pengiriman_linen (
        kode_pengiriman,
        verification_code,
        status,
        hospital_id,
        device_id,
        idempotency_key,
        payload_digest,
        manifest_hash,
        catatan,
        created_at,
        updated_at
    ) VALUES (
        p_package_code,
        v_verification_code,
        'pending',
        v_hospital_id,
        p_device_id,
        v_clean_idempotency_key,
        v_canonical_payload_digest,
        v_computed_manifest_hash,
        p_catatan,
        now(),
        now()
    )
    RETURNING id INTO v_package_id;

    -- 11. Insert Detail Item Linen (Mandat REV4 A.1: Normalisasi Unicode NFC & Whitespace via clean_metadata_text)
    FOR v_item IN SELECT * FROM jsonb_array_elements(p_items) LOOP
        INSERT INTO public.detail_pengiriman_linen (
            pengiriman_id,
            epc,
            nama_linen,
            kategori,
            created_at
        ) VALUES (
            v_package_id,
            public.clean_epc(v_item->>'epc'),
            public.clean_metadata_text(v_item->>'nama_linen'),
            public.clean_metadata_text(v_item->>'kategori'),
            now()
        );
    END LOOP;

    -- 12. Audit Catatan Sukses
    INSERT INTO public.supplier_ingestion_audit (
        device_id, package_code, idempotency_key, payload_digest, status, http_status_code, error_message
    ) VALUES (
        p_device_id, p_package_code, v_clean_idempotency_key, v_canonical_payload_digest, 'SUCCESS_INGESTED', 201, NULL
    );

    RETURN jsonb_build_object(
        'success', TRUE,
        'status', 'INGESTED',
        'http_status', 201,
        'package_id', v_package_id,
        'package_code', p_package_code,
        'manifest_hash', v_computed_manifest_hash,
        'verification_code', v_verification_code,
        'item_count', jsonb_array_length(p_items),
        'created_at', now(),
        'message', 'Paket berhasil di-ingest secara atomik ke basis data cloud'
    );
END;
$$;

-- -----------------------------------------------------------------------------
-- 11. RPC CLIENTS: get_package_info (SECURITY DEFINER)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.get_package_info(p_package_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_user_id UUID;
    v_pkg RECORD;
    v_items JSONB;
BEGIN
    IF p_package_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_package_id wajib diisi');
    END IF;

    -- Verifikasi Keanggotaan Operator terhadap Rumah Sakit Pemilik Paket
    v_user_id := auth.uid();
    SELECT p.id, p.kode_pengiriman, p.status, p.hospital_id, p.manifest_hash, p.created_at, rs.nama_rs, rs.kode_rs
    INTO v_pkg
    FROM public.pengiriman_linen p
    JOIN public.rumah_sakit rs ON rs.id = p.hospital_id
    WHERE p.id = p_package_id;

    IF v_pkg.id IS NULL THEN
        RETURN jsonb_build_object('status', 'NOT_FOUND', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket tidak ditemukan');
    END IF;

    -- Validasi Hak Akses Tenant Rumah Sakit (Auditor boleh membaca)
    IF v_user_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.hospital_memberships hm 
            WHERE hm.user_id = v_user_id AND hm.hospital_id = v_pkg.hospital_id AND hm.is_active = TRUE
        ) THEN
            RETURN jsonb_build_object('status', 'FORBIDDEN', 'code', 'ACCESS_DENIED', 'message', 'Akses ditolak: Anda tidak memiliki akses ke rumah sakit paket ini');
        END IF;
    END IF;

    SELECT jsonb_agg(jsonb_build_object(
        'epc', d.epc,
        'nama_linen', d.nama_linen,
        'kategori', d.kategori
    ) ORDER BY d.epc ASC)
    INTO v_items
    FROM public.detail_pengiriman_linen d
    WHERE d.pengiriman_id = p_package_id;

    -- Verification code SENGAJA TIDAK DIKEMBALIKAN (Prinsip Least Privilege)
    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'package_id', v_pkg.id,
        'kode_pengiriman', v_pkg.kode_pengiriman,
        'hospital_id', v_pkg.hospital_id,
        'kode_rs', v_pkg.kode_rs,
        'nama_rs', v_pkg.nama_rs,
        'status_pengiriman', v_pkg.status,
        'manifest_hash', v_pkg.manifest_hash,
        'created_at', v_pkg.created_at,
        'item_count', coalesce(jsonb_array_length(v_items), 0),
        'manifest_items', coalesce(v_items, '[]'::jsonb)
    );
END;
$$;

-- -----------------------------------------------------------------------------
-- 12. RPC CLIENTS: claim_package (SECURITY DEFINER - ROLE, EXPIRY & NULL-SAFE)
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
    v_user_role TEXT;
    v_pkg RECORD;
BEGIN
    IF p_package_id IS NULL OR p_operation_id IS NULL OR p_installation_uid IS NULL OR p_verification_code IS NULL OR p_computed_manifest_hash IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Seluruh parameter wajib diisi');
    END IF;

    v_user_id := auth.uid();

    -- Kunci baris paket secara eksklusif untuk mencegah race condition konkuren
    SELECT * INTO v_pkg 
    FROM public.pengiriman_linen 
    WHERE id = p_package_id 
    FOR UPDATE;

    IF v_pkg.id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket tidak ditemukan');
    END IF;

    -- Validasi Tenant Rumah Sakit
    IF p_hospital_id IS NOT NULL AND v_pkg.hospital_id != p_hospital_id THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'HOSPITAL_MISMATCH', 'message', 'Hospital ID tidak cocok dengan tujuan paket');
    END IF;

    -- Validasi Keanggotaan dan RBAC Role (Hanya 'admin' dan 'operator'; 'auditor' DITOLAK)
    IF v_user_id IS NOT NULL THEN
        SELECT hm.role INTO v_user_role
        FROM public.hospital_memberships hm 
        WHERE hm.user_id = v_user_id AND hm.hospital_id = v_pkg.hospital_id AND hm.is_active = TRUE;

        IF v_user_role IS NULL THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'ACCESS_DENIED', 'message', 'Operator tidak terdaftar pada rumah sakit tujuan');
        END IF;

        IF v_user_role NOT IN ('admin', 'operator') THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'INSUFFICIENT_PRIVILEGES', 'message', 'Peran ' || v_user_role || ' hanya memiliki akses baca dan tidak berwenang mengklaim paket');
        END IF;
    END IF;

    -- Validasi Kode Verifikasi Paket
    IF btrim(v_pkg.verification_code) != btrim(p_verification_code) THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_VERIFICATION_CODE', 'message', 'Kode verifikasi paket salah');
    END IF;

    -- Validasi Manifest Hash Kanonikal
    IF lower(btrim(v_pkg.manifest_hash)) != lower(btrim(p_computed_manifest_hash)) THEN
        RETURN jsonb_build_object(
            'status', 'ERROR',
            'code', 'MANIFEST_HASH_MISMATCH',
            'message', 'Manifest hash tidak cocok. Fisik linen berbeda dengan data pengiriman cloud'
        );
    END IF;

    -- Penanganan Status Paket yang Telah di-Klaim
    IF v_pkg.status = 'claimed' THEN
        -- Mandat A.3: claim_expires_at NULL atau now() > claim_expires_at dianggap KEDALUWARSA
        IF v_pkg.claim_expires_at IS NULL OR now() > v_pkg.claim_expires_at THEN
            -- Klaim lama telah kedaluwarsa: Izinkan Reclaim secara aman oleh sesi yang valid
            UPDATE public.pengiriman_linen
            SET status = 'claimed',
                claimed_by_operation_id = p_operation_id,
                claimed_by_installation_uid = p_installation_uid,
                claimed_by_user_id = v_user_id,
                claimed_at = now(),
                claim_expires_at = now() + interval '2 hours',
                updated_at = now()
            WHERE id = p_package_id;

            RETURN jsonb_build_object(
                'status', 'SUCCESS',
                'code', 'CLAIM_REACQUIRED_AFTER_EXPIRY',
                'package_id', p_package_id,
                'operation_id', p_operation_id,
                'claimed_at', now(),
                'claim_expires_at', now() + interval '2 hours',
                'manifest_hash', v_pkg.manifest_hash,
                'message', 'Klaim sebelumnya kedaluwarsa. Klaim baru berhasil diperoleh.'
            );
        END IF;

        -- Mandat A.4: Validasi identitas aman terhadap NULL dengan IS DISTINCT FROM
        IF v_pkg.claimed_by_operation_id IS DISTINCT FROM p_operation_id OR 
           v_pkg.claimed_by_installation_uid IS DISTINCT FROM p_installation_uid THEN
            RETURN jsonb_build_object(
                'status', 'ERROR',
                'code', 'PACKAGE_ALREADY_CLAIMED',
                'message', 'Paket sedang di-klaim aktif oleh sesi atau perangkat penerimaan lain. Klaim berlaku hingga: ' || coalesce(v_pkg.claim_expires_at::text, 'N/A')
            );
        END IF;

        -- Validasi konsistensi pengguna jika claimed_by_user_id terisi
        IF v_pkg.claimed_by_user_id IS NOT NULL THEN
            IF v_user_id IS NULL OR v_pkg.claimed_by_user_id IS DISTINCT FROM v_user_id THEN
                RETURN jsonb_build_object(
                    'status', 'ERROR',
                    'code', 'USER_IDENTITY_MISMATCH',
                    'message', 'Replay klaim ditolak karena identitas pengguna berbeda dari inisiator klaim'
                );
            END IF;
        END IF;

        -- Replay sah
        RETURN jsonb_build_object(
            'status', 'SUCCESS',
            'code', 'ALREADY_CLAIMED_BY_YOU',
            'package_id', v_pkg.id,
            'operation_id', v_pkg.claimed_by_operation_id,
            'claimed_at', v_pkg.claimed_at,
            'claim_expires_at', v_pkg.claim_expires_at,
            'manifest_hash', v_pkg.manifest_hash,
            'message', 'Paket telah berhasil di-klaim sebelumnya oleh sesi operasi ini (Idempotent Replay)'
        );
    END IF;

    IF v_pkg.status != 'pending' THEN
        RETURN jsonb_build_object(
            'status', 'ERROR',
            'code', 'INVALID_STATUS',
            'message', 'Paket tidak dapat di-klaim karena berstatus: ' || v_pkg.status
        );
    END IF;

    -- Eksekusi Klaim Baru Paket
    UPDATE public.pengiriman_linen
    SET status = 'claimed',
        claimed_by_operation_id = p_operation_id,
        claimed_by_installation_uid = p_installation_uid,
        claimed_by_user_id = v_user_id,
        claimed_at = now(),
        claim_expires_at = now() + interval '2 hours',
        updated_at = now()
    WHERE id = p_package_id;

    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'code', 'CLAIM_ACQUIRED',
        'package_id', p_package_id,
        'operation_id', p_operation_id,
        'claimed_at', now(),
        'claim_expires_at', now() + interval '2 hours',
        'manifest_hash', v_pkg.manifest_hash,
        'message', 'Klaim paket berhasil diperoleh secara eksklusif'
    );
END;
$$;

-- -----------------------------------------------------------------------------
-- 13. RPC CLIENTS: confirm_package (SECURITY DEFINER - EPC LOCK & NULL-SAFE)
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
    v_user_role TEXT;
    v_pkg RECORD;
    r RECORD;
    v_conflicting_epc TEXT;
    v_conflicting_hosp BIGINT;
    v_inserted_linen_count INTEGER := 0;
    v_expected_item_count INTEGER := 0;
    v_actual_manifest_hash TEXT;
BEGIN
    -- 1. Validasi Argumen Wajib
    IF p_package_id IS NULL OR p_hospital_id IS NULL OR p_operation_id IS NULL OR 
       p_installation_uid IS NULL OR p_verification_code IS NULL OR p_manifest_hash IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Seluruh 6 parameter konfirmasi wajib diisi');
    END IF;

    -- 2. Validasi Hak Akses User & Role (Admin / Operator Wajib; Auditor Ditolak)
    v_user_id := auth.uid();
    IF v_user_id IS NOT NULL THEN
        SELECT hm.role INTO v_user_role
        FROM public.hospital_memberships hm
        WHERE hm.user_id = v_user_id
          AND hm.hospital_id = p_hospital_id
          AND hm.is_active = TRUE;

        IF v_user_role IS NULL THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'HOSPITAL_ACCESS_DENIED', 'message', 'Pengguna tidak memiliki hak akses aktif pada rumah sakit ini');
        END IF;

        IF v_user_role NOT IN ('admin', 'operator') THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'INSUFFICIENT_PRIVILEGES', 'message', 'Peran ' || v_user_role || ' hanya memiliki akses baca dan dilarang mengonfirmasi paket');
        END IF;
    END IF;

    -- 3. Kunci Baris Paket Secara Eksklusif
    SELECT * INTO v_pkg 
    FROM public.pengiriman_linen 
    WHERE id = p_package_id 
    FOR UPDATE;

    IF v_pkg.id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket tidak ditemukan');
    END IF;

    -- 4. Validasi Hospital ID Tujuan Paket
    IF v_pkg.hospital_id != p_hospital_id THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'HOSPITAL_MISMATCH', 'message', 'Hospital ID tidak cocok');
    END IF;

    -- 5. Validasi Kode Verifikasi Fisik Paket
    IF btrim(v_pkg.verification_code) != btrim(p_verification_code) THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_VERIFICATION_CODE', 'message', 'Kode verifikasi tidak cocok');
    END IF;

    -- 6. Validasi Manifest Hash Kanonikal Header vs Parameter
    IF lower(btrim(v_pkg.manifest_hash)) != lower(btrim(p_manifest_hash)) THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'MANIFEST_HASH_MISMATCH', 'message', 'Manifest hash parameter tidak cocok dengan header paket');
    END IF;

    -- 6b. Mandat REV4 A.3: Verifikasi Ulang Manifest Aktual dari Tabel Detail Sebelum Konfirmasi
    BEGIN
        v_actual_manifest_hash := public.compute_package_manifest_hash(p_package_id);
    EXCEPTION WHEN OTHERS THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'ACTUAL_MANIFEST_COMPUTATION_FAILED', 'message', 'Gagal menghitung manifest aktual detail paket: ' || SQLERRM);
    END;

    IF lower(v_actual_manifest_hash) != lower(btrim(p_manifest_hash)) THEN
        RETURN jsonb_build_object(
            'status', 'ERROR',
            'code', 'ACTUAL_MANIFEST_HASH_MISMATCH',
            'message', 'Manifest hash aktual dari baris detail tidak cocok dengan manifest yang diharapkan (Integritas fisik paket tertolak)'
        );
    END IF;

    -- 7. Idempotent Replay Check (SETELAH kredensial auth, role, hospital, verifikasi fisik & manifest hash valid)
    IF v_pkg.status = 'confirmed' THEN
        IF v_pkg.confirmed_by_operation_id = p_operation_id AND v_pkg.confirmed_by_installation_uid = p_installation_uid THEN
            RETURN jsonb_build_object(
                'status', 'SUCCESS',
                'code', 'ALREADY_CONFIRMED',
                'package_id', v_pkg.id,
                'operation_id', v_pkg.confirmed_by_operation_id,
                'confirmed_at', v_pkg.confirmed_at,
                'message', 'Paket telah berhasil dikonfirmasi sebelumnya (Idempotent Replay)'
            );
        ELSE
            RETURN jsonb_build_object(
                'status', 'ERROR',
                'code', 'PACKAGE_ALREADY_CONFIRMED_BY_OTHER',
                'message', 'Paket telah dikonfirmasi oleh operasi penerimaan lain'
            );
        END IF;
    END IF;

    -- 8. Validasi Status Paket (Wajib 'claimed')
    IF v_pkg.status != 'claimed' THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_STATUS', 'message', 'Paket wajib berstatus claimed sebelum konfirmasi (Status saat ini: ' || v_pkg.status || ')');
    END IF;

    -- 9. Mandat A.3: Validasi Batas Waktu Kedaluwarsa Klaim (claim_expires_at NULL atau now() > claim_expires_at DITOLAK)
    IF v_pkg.claim_expires_at IS NULL OR now() > v_pkg.claim_expires_at THEN
        RETURN jsonb_build_object(
            'status', 'ERROR',
            'code', 'CLAIM_EXPIRED',
            'message', 'Masa berlaku klaim paket tidak valid atau telah kedaluwarsa. Silakan lakukan klaim ulang sebelum konfirmasi.'
        );
    END IF;

    -- 10. Mandat A.4: Validasi Kepemilikan Operasi Klaim (NULL-Safe dengan IS DISTINCT FROM)
    IF v_pkg.claimed_by_operation_id IS DISTINCT FROM p_operation_id OR 
       v_pkg.claimed_by_installation_uid IS DISTINCT FROM p_installation_uid THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'OPERATION_IDENTITY_MISMATCH', 'message', 'Operasi konfirmasi tidak sesuai dengan pemegang klaim paket');
    END IF;

    -- 11. Mandat A.4: Validasi Konsistensi Pengguna Pemegang Klaim (NULL-Safe)
    IF v_pkg.claimed_by_user_id IS NOT NULL THEN
        IF (v_user_id IS NULL OR v_pkg.claimed_by_user_id IS DISTINCT FROM v_user_id) AND coalesce(v_user_role, '') != 'admin' THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'USER_IDENTITY_MISMATCH', 'message', 'Paket di-klaim oleh operator lain. Hanya pemegang klaim atau Admin yang dapat mengonfirmasi.');
        END IF;
    END IF;

    -- 12. PENGUNCIAAN SERIALISASI TRANS-TENANT PER-EPC (ANTI RACE CONDITION LINTAS RUMAH SAKIT)
    FOR r IN (
        SELECT public.clean_epc(d.epc) AS epc_clean
        FROM public.detail_pengiriman_linen d
        WHERE d.pengiriman_id = p_package_id
        ORDER BY public.clean_epc(d.epc) ASC
    ) LOOP
        PERFORM pg_advisory_xact_lock(hashtext('epc_lock:' || r.epc_clean));
    END LOOP;

    -- 13. Proteksi Kepemilikan EPC Antar Rumah Sakit (Mencegah EPC Hijacking)
    SELECT lm.epc, lm.hospital_id INTO v_conflicting_epc, v_conflicting_hosp
    FROM public.detail_pengiriman_linen d
    JOIN public.linen_master lm ON lm.epc = public.clean_epc(d.epc)
    WHERE d.pengiriman_id = p_package_id
      AND lm.hospital_id != v_pkg.hospital_id
    LIMIT 1;

    IF v_conflicting_epc IS NOT NULL THEN
        RETURN jsonb_build_object(
            'status', 'ERROR',
            'code', 'EPC_CROSS_HOSPITAL_CONFLICT',
            'message', 'Tag EPC ' || v_conflicting_epc || ' telah terdaftar pada rumah sakit lain (ID: ' || v_conflicting_hosp || '). Pembajakan kepemilikan ditolak. Seluruh transaksi dibatalkan.'
        );
    END IF;

    -- 14. Mutasi Master Linen Rumah Sakit & Audit Trail (Atomic with Strict Result Validation)
    SELECT count(*) INTO v_expected_item_count
    FROM public.detail_pengiriman_linen
    WHERE pengiriman_id = p_package_id;

    FOR r IN (
        SELECT 
            public.clean_epc(d.epc) AS epc_clean, 
            public.clean_metadata_text(d.nama_linen) AS nama_linen, 
            public.clean_metadata_text(d.kategori) AS kategori
        FROM public.detail_pengiriman_linen d
        WHERE d.pengiriman_id = p_package_id
    ) LOOP
        INSERT INTO public.linen_master (epc, nama_linen, kategori, status, hospital_id, last_scanned_at, created_at, updated_at)
        VALUES (r.epc_clean, r.nama_linen, r.kategori, 'ACTIVE', v_pkg.hospital_id, now(), now(), now())
        ON CONFLICT (epc) DO UPDATE
        SET status = 'ACTIVE',
            last_scanned_at = now(),
            updated_at = now()
        WHERE public.linen_master.hospital_id = EXCLUDED.hospital_id;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'EPC_CONFLICT: EPC % gagal diperbarui karena ketidakcocokan kepemilikan rumah sakit', r.epc_clean USING ERRCODE = '23505';
        END IF;

        INSERT INTO public.linen_audit_log (epc, action, actor_id, details, created_at)
        VALUES (
            r.epc_clean,
            'PACKAGE_RECEIVE_CONFIRMED',
            coalesce(v_user_id::text, p_operation_id),
            jsonb_build_object('package_id', p_package_id, 'kode_pengiriman', v_pkg.kode_pengiriman, 'hospital_id', v_pkg.hospital_id),
            now()
        );
        v_inserted_linen_count := v_inserted_linen_count + 1;
    END LOOP;

    -- Verifikasi bahwa SELURUH item berhasil dimasukkan tanpa ada yang tertinggal
    IF v_inserted_linen_count != v_expected_item_count THEN
        RAISE EXCEPTION 'INCOMPLETE_CONFIRMATION: Jumlah item yang dimutasi (%) tidak sesuai dengan detail paket (%)', v_inserted_linen_count, v_expected_item_count USING ERRCODE = '23514';
    END IF;

    -- 15. Transisi Status Final ke 'confirmed'
    UPDATE public.pengiriman_linen
    SET status = 'confirmed',
        confirmed_at = now(),
        confirmed_by_operation_id = p_operation_id,
        confirmed_by_installation_uid = p_installation_uid,
        confirmed_by_user_id = v_user_id,
        updated_at = now()
    WHERE id = p_package_id;

    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'code', 'PACKAGE_CONFIRMED',
        'package_id', p_package_id,
        'operation_id', p_operation_id,
        'hospital_id', v_pkg.hospital_id,
        'confirmed_at', now(),
        'items_count', v_inserted_linen_count,
        'message', 'Paket berhasil dikonfirmasi dan seluruh linen telah diaktifkan di master rumah sakit'
    );
END;
$$;

-- -----------------------------------------------------------------------------
-- 14. RPC CLIENTS: get_package_claim_status (SECURITY DEFINER)
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
    v_pkg RECORD;
BEGIN
    IF p_package_id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'INVALID_ARGUMENTS', 'message', 'Parameter p_package_id wajib diisi');
    END IF;

    v_user_id := auth.uid();

    SELECT id, kode_pengiriman, status, hospital_id, manifest_hash,
           claimed_at, claim_expires_at, claimed_by_operation_id, claimed_by_installation_uid,
           confirmed_at, confirmed_by_operation_id, confirmed_by_installation_uid
    INTO v_pkg
    FROM public.pengiriman_linen
    WHERE id = p_package_id;

    IF v_pkg.id IS NULL THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'PACKAGE_NOT_FOUND', 'message', 'Paket tidak ditemukan');
    END IF;

    IF p_hospital_id IS NOT NULL AND v_pkg.hospital_id != p_hospital_id THEN
        RETURN jsonb_build_object('status', 'ERROR', 'code', 'HOSPITAL_MISMATCH', 'message', 'Hospital ID tidak cocok');
    END IF;

    IF v_user_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.hospital_memberships hm 
            WHERE hm.user_id = v_user_id AND hm.hospital_id = v_pkg.hospital_id AND hm.is_active = TRUE
        ) THEN
            RETURN jsonb_build_object('status', 'ERROR', 'code', 'ACCESS_DENIED', 'message', 'Operator tidak terdaftar pada rumah sakit paket ini');
        END IF;
    END IF;

    RETURN jsonb_build_object(
        'status', 'SUCCESS',
        'package_id', v_pkg.id,
        'kode_pengiriman', v_pkg.kode_pengiriman,
        'package_status', v_pkg.status,
        'hospital_id', v_pkg.hospital_id,
        'manifest_hash', v_pkg.manifest_hash,
        'claimed_at', v_pkg.claimed_at,
        'claim_expires_at', v_pkg.claim_expires_at,
        'claimed_by_operation_id', v_pkg.claimed_by_operation_id,
        'claimed_by_installation_uid', v_pkg.claimed_by_installation_uid,
        'confirmed_at', v_pkg.confirmed_at,
        'confirmed_by_operation_id', v_pkg.confirmed_by_operation_id,
        'confirmed_by_installation_uid', v_pkg.confirmed_by_installation_uid
    );
END;
$$;

-- -----------------------------------------------------------------------------
-- 15. ROW LEVEL SECURITY (RLS) & MULTI-TENANT ACCESS POLICIES
-- -----------------------------------------------------------------------------
ALTER TABLE public.rumah_sakit ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.hospital_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pengiriman_linen ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detail_pengiriman_linen ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.linen_master ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.linen_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.suppliers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.supplier_hospital_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registered_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.device_nonces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.supplier_ingestion_audit ENABLE ROW LEVEL SECURITY;

-- 15.1 Kebijakan RLS Rumah Sakit & Membership
DROP POLICY IF EXISTS p_rumah_sakit_member_read ON public.rumah_sakit;
CREATE POLICY p_rumah_sakit_member_read ON public.rumah_sakit
    FOR SELECT TO authenticated
    USING (id IN (SELECT hospital_id FROM public.hospital_memberships WHERE user_id = auth.uid() AND is_active = TRUE));

DROP POLICY IF EXISTS p_hospital_memberships_user_read ON public.hospital_memberships;
CREATE POLICY p_hospital_memberships_user_read ON public.hospital_memberships
    FOR SELECT TO authenticated
    USING (user_id = auth.uid());

-- 15.2 Kebijakan RLS Pengiriman Linen (Multi-Tenant Hospital Isolation)
DROP POLICY IF EXISTS p_pengiriman_linen_tenant ON public.pengiriman_linen;
CREATE POLICY p_pengiriman_linen_tenant ON public.pengiriman_linen
    FOR SELECT TO authenticated
    USING (hospital_id IN (SELECT hospital_id FROM public.hospital_memberships WHERE user_id = auth.uid() AND is_active = TRUE));

DROP POLICY IF EXISTS p_detail_pengiriman_linen_tenant ON public.detail_pengiriman_linen;
CREATE POLICY p_detail_pengiriman_linen_tenant ON public.detail_pengiriman_linen
    FOR SELECT TO authenticated
    USING (pengiriman_id IN (
        SELECT id FROM public.pengiriman_linen 
        WHERE hospital_id IN (SELECT hospital_id FROM public.hospital_memberships WHERE user_id = auth.uid() AND is_active = TRUE)
    ));

-- 15.3 Kebijakan RLS Linen Master & Audit (Strict SELECT ONLY untuk Authenticated)
DROP POLICY IF EXISTS p_linen_master_tenant ON public.linen_master;
CREATE POLICY p_linen_master_tenant ON public.linen_master
    FOR SELECT TO authenticated
    USING (hospital_id IN (SELECT hospital_id FROM public.hospital_memberships WHERE user_id = auth.uid() AND is_active = TRUE));

DROP POLICY IF EXISTS p_linen_audit_log_tenant ON public.linen_audit_log;
CREATE POLICY p_linen_audit_log_tenant ON public.linen_audit_log
    FOR SELECT TO authenticated
    USING (epc IN (
        SELECT epc FROM public.linen_master 
        WHERE hospital_id IN (SELECT hospital_id FROM public.hospital_memberships WHERE user_id = auth.uid() AND is_active = TRUE)
    ));

-- -----------------------------------------------------------------------------
-- 16. HAK AKSES PERAN BASIS DATA (LEAST PRIVILEGE ENFORCEMENT & HARDENING)
-- -----------------------------------------------------------------------------
-- 16.1 Pembatasan Skema Public
REVOKE CREATE ON SCHEMA public FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON ROUTINES FROM PUBLIC, anon;

-- 16.2 Pencabutan Hak Akses Tabel, Fungsi, dan Sequence Global
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, PUBLIC, authenticated;

-- 16.3 Hak Akses Role: authenticated (Operator & Admin Rumah Sakit)
GRANT SELECT ON TABLE public.rumah_sakit TO authenticated;
GRANT SELECT ON TABLE public.hospital_memberships TO authenticated;
GRANT SELECT ON TABLE public.pengiriman_linen TO authenticated;
GRANT SELECT ON TABLE public.detail_pengiriman_linen TO authenticated;
GRANT SELECT ON TABLE public.linen_master TO authenticated;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.linen_master FROM authenticated, anon, PUBLIC;
GRANT SELECT ON TABLE public.linen_audit_log TO authenticated;

-- Hak Eksekusi RPC ClientS (Hanya authenticated & service_role)
GRANT EXECUTE ON FUNCTION public.get_package_info(BIGINT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.claim_package(BIGINT, TEXT, TEXT, TEXT, TEXT, BIGINT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.confirm_package(BIGINT, BIGINT, TEXT, TEXT, TEXT, TEXT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.get_package_claim_status(BIGINT, BIGINT) TO authenticated, service_role;

-- Hak Eksekusi Fungsi Utility & Validasi (Mandat REV4 A.1 & A.6)
GRANT EXECUTE ON FUNCTION public.clean_epc(TEXT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.clean_metadata_text(TEXT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.compute_package_manifest_hash(BIGINT) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.compute_manifest_hash_from_items(JSONB) TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.register_hospital_admin_membership(TEXT, BIGINT, TEXT) TO service_role;
GRANT EXECUTE ON FUNCTION public.register_hospital_membership_by_uuid(UUID, BIGINT, TEXT) TO service_role;

-- 16.4 Hak Akses Role: service_role (Edge Function & Backend Gateway)
-- RPC ingest_supplier_package HANYA dapat dipanggil oleh service_role
REVOKE ALL ON FUNCTION public.ingest_supplier_package(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.ingest_supplier_package(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, JSONB) TO service_role;

GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;

-- -----------------------------------------------------------------------------
-- 17. INITIAL STAGING SEED DATA (VERIFIED WORKSTATION CREDENTIALS)
-- -----------------------------------------------------------------------------
-- 1. Rumah Sakit Uji Staging
INSERT INTO public.rumah_sakit (id, kode_rs, nama_rs, alamat, is_active)
VALUES (1, 'RS-SMC-001', 'RS Siloam Medika Cikarang', 'Kawasan Industri Lippo Cikarang', TRUE)
ON CONFLICT (id) DO UPDATE
SET nama_rs = EXCLUDED.nama_rs, is_active = TRUE;

-- Sinkronisasi sequence rumah_sakit_id_seq setelah manual ID
SELECT setval('public.rumah_sakit_id_seq', (SELECT GREATEST(COALESCE(max(id), 1), 1) FROM public.rumah_sakit));

-- 2. Vendor Supplier
INSERT INTO public.suppliers (supplier_id, supplier_code, supplier_name, contact_email, is_active)
VALUES (
    'a0000000-0000-0000-0000-000000000001'::uuid,
    'SUPP-001',
    'PT Linen Prima Nusantara',
    'support@linenprima.co.id',
    TRUE
)
ON CONFLICT (supplier_code) DO UPDATE
SET supplier_name = EXCLUDED.supplier_name, is_active = TRUE;

-- 3. Kontrak Kerjasama Supplier - Rumah Sakit
INSERT INTO public.supplier_hospital_contracts (
    contract_id,
    contract_number,
    supplier_id,
    hospital_id,
    valid_from,
    valid_until,
    is_active
)
VALUES (
    'c0000000-0000-0000-0000-000000000001'::uuid,
    'KTR-2026-LPN-SMC-01',
    'a0000000-0000-0000-0000-000000000001'::uuid,
    1,
    now() - interval '30 days',
    now() + interval '5 years',
    TRUE
)
ON CONFLICT (contract_number) DO UPDATE
SET is_active = TRUE, valid_until = EXCLUDED.valid_until;

-- 4. Registrasi Perangkat RFID Scanner Supplier
INSERT INTO public.registered_devices (
    device_id,
    supplier_id,
    device_name,
    device_type,
    status
)
VALUES (
    'DEV-MAGICRF-STAGING-01',
    'a0000000-0000-0000-0000-000000000001'::uuid,
    'Workstation Scanner UHF MagicRF M100',
    'RFID_SCANNER_GATEWAY',
    'ACTIVE'
)
ON CONFLICT (device_id) DO UPDATE
SET status = 'ACTIVE';

-- 5. Registrasi Kunci Publik Ed25519 ASLI Workstation
-- Kunci ini 100% cocok dengan D:\RFID_Scanner_App\config\device_master.key
INSERT INTO public.device_keys (
    key_id,
    device_id,
    public_key_ed25519,
    key_algorithm,
    status,
    valid_from,
    valid_until
)
VALUES (
    'd0000000-0000-0000-0000-000000000001'::uuid,
    'DEV-MAGICRF-STAGING-01',
    '7f7ed1969b4bbcf02baf063e2630dba1ef59813e7756cd360d50a0bc15972dec',
    'Ed25519',
    'ACTIVE',
    now() - interval '1 day',
    now() + interval '5 years'
)
ON CONFLICT (public_key_ed25519) DO UPDATE
SET status = 'ACTIVE', valid_until = EXCLUDED.valid_until;

-- 6. Pendaftaran Otomatis Admin Staging Pertama (Hanya jika pengguna sudah ada di auth.users Supabase)
-- Mandat REV4 A.6: Mencegah pembuatan UUID fiktif dan menjamin foreign key auth sah
DO $$
DECLARE
    v_first_user_id UUID;
BEGIN
    SELECT id INTO v_first_user_id FROM auth.users ORDER BY created_at ASC LIMIT 1;
    IF v_first_user_id IS NOT NULL THEN
        INSERT INTO public.hospital_memberships (hospital_id, user_id, role, is_active, created_at, updated_at)
        VALUES (1, v_first_user_id, 'admin', TRUE, now(), now())
        ON CONFLICT (user_id, hospital_id) DO UPDATE
        SET role = 'admin', is_active = TRUE, updated_at = now();
    END IF;
END $$;

COMMIT;
