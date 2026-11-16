-- =============================================================================
-- FILE: 10_unified_dashboard_hospital_monitoring_rls.sql
-- DESKRIPSI: Migrasi inkremental RLS untuk modul Monitoring Supplier, Kontrak,
--            dan Perangkat pada Unified Dashboard ClientS (Tahap 1 Gate B/C).
-- TARGET SUPABASE: supplier-clients-staging (bgtxpdydljfeltdimmxr)
-- ATURAN KESELAMATAN:
-- 1. Berkas ini TIDAK dieksekusi secara otomatis ke remote Supabase.
-- 2. Jangan menonaktifkan RLS.
-- 3. Hak SELECT HANYA diberikan kepada role authenticated.
-- 4. Supplier hanya dapat dibaca jika memiliki kontrak aktif dengan RS pengguna.
-- 5. Kontrak hanya dapat dibaca jika hospital_id milik RS pengguna.
-- 6. Perangkat hanya dapat dibaca jika supplier perangkat memiliki kontrak aktif dengan RS pengguna.
-- 7. Dilarang memberikan hak INSERT, UPDATE, DELETE kepada authenticated/anon.
-- =============================================================================

BEGIN;

-- 1. Pastikan RLS aktif pada ketiga tabel (Idempotent)
ALTER TABLE public.suppliers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.supplier_hospital_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.registered_devices ENABLE ROW LEVEL SECURITY;

-- 2. Kebijakan RLS untuk public.supplier_hospital_contracts
DROP POLICY IF EXISTS p_supplier_hospital_contracts_member_read ON public.supplier_hospital_contracts;
CREATE POLICY p_supplier_hospital_contracts_member_read ON public.supplier_hospital_contracts
    FOR SELECT TO authenticated
    USING (
        hospital_id IN (
            SELECT hm.hospital_id 
            FROM public.hospital_memberships hm 
            WHERE hm.user_id = auth.uid() 
              AND hm.is_active = TRUE
        )
    );

-- 3. Kebijakan RLS untuk public.suppliers (Hanya supplier dengan kontrak aktif ke RS pengguna)
DROP POLICY IF EXISTS p_suppliers_member_read ON public.suppliers;
CREATE POLICY p_suppliers_member_read ON public.suppliers
    FOR SELECT TO authenticated
    USING (
        supplier_id IN (
            SELECT shc.supplier_id 
            FROM public.supplier_hospital_contracts shc
            WHERE shc.hospital_id IN (
                SELECT hm.hospital_id 
                FROM public.hospital_memberships hm 
                WHERE hm.user_id = auth.uid() 
                  AND hm.is_active = TRUE
            )
            AND shc.is_active = TRUE
            AND now() BETWEEN shc.valid_from AND shc.valid_until
        )
    );

-- 4. Kebijakan RLS untuk public.registered_devices (Hanya device dari supplier berkontrak aktif)
DROP POLICY IF EXISTS p_registered_devices_member_read ON public.registered_devices;
CREATE POLICY p_registered_devices_member_read ON public.registered_devices
    FOR SELECT TO authenticated
    USING (
        supplier_id IN (
            SELECT shc.supplier_id 
            FROM public.supplier_hospital_contracts shc
            WHERE shc.hospital_id IN (
                SELECT hm.hospital_id 
                FROM public.hospital_memberships hm 
                WHERE hm.user_id = auth.uid() 
                  AND hm.is_active = TRUE
            )
            AND shc.is_active = TRUE
            AND now() BETWEEN shc.valid_from AND shc.valid_until
        )
    );

-- 5. Penegakan Least Privilege (Hak Akses Minimum)
-- HANYA SELECT untuk authenticated. Dilarang INSERT, UPDATE, DELETE.
GRANT SELECT ON TABLE public.suppliers TO authenticated;
GRANT SELECT ON TABLE public.supplier_hospital_contracts TO authenticated;
GRANT SELECT ON TABLE public.registered_devices TO authenticated;

REVOKE INSERT, UPDATE, DELETE ON TABLE public.suppliers FROM authenticated, anon, PUBLIC;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.supplier_hospital_contracts FROM authenticated, anon, PUBLIC;
REVOKE INSERT, UPDATE, DELETE ON TABLE public.registered_devices FROM authenticated, anon, PUBLIC;

COMMIT;

