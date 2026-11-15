"""
=============================================================================
FILE: verify_ddl_r2_10_4.py
REVISI: TAHAP 4D.2A.3-R2.10.4
DESKRIPSI:
    Static Sanity Checker & Schema Alignment Verifier untuk DDL Migrasi 
    07_package_communication_rpc_consolidated_r2_10_4.sql (P0-01 & P1-03)
=============================================================================
"""

import os
import re
import sys

def verify_ddl_static(file_path: str):
    print("=" * 80)
    print(f"STATIC SANITY CHECKER DDL: {file_path}")
    print("STATUS: PEMERIKSAAN STATIK STRUKTUR SINTAKS, KLAUSUL & SCHEMA ALIGNMENT (NON-RUNTIME)")
    print("=" * 80)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        print(f"[FAIL] Gagal membaca berkas: {exc}")
        sys.exit(1)

    checks = []

    # C1: Preflight Invariant 11 & Invariant 13
    c1_inv11 = "Invarian 11" in content and "manifest_hash" in content and "^[0-9a-f]{64}$" in content
    c1_inv13 = "Invarian 13" in content and "clean_epc" in content and "^[0-9A-F]{24}$" in content
    checks.append(("C1: Preflight Invariant 11 (Non-pending hash 64-hex) & Invariant 13 (clean_epc 24-hex)", c1_inv11 and c1_inv13))

    # C2: Unique Index with clean_epc schema qualified
    c2 = "uq_detail_pengiriman_linen_epc_clean" in content and "public.clean_epc(epc)" in content
    checks.append(("C2: Unique Functional Index uq_detail_pengiriman_linen_epc_clean schema-qualified", c2))

    # C3: clean_epc handles full Unicode whitespace set identical to Python str.strip()
    c3 = "public.clean_epc" in content and "\\u00A0" in content and "\\u3000" in content and "\\u2003" in content and "\\u0085" in content
    checks.append(("C3: Function public.clean_epc Normalisasi Whitespace Unicode Lengkap (Paritas Python)", c3))

    # C4: Header Trigger BEFORE INSERT OR UPDATE & State Machine Bypass Prevention (P0 Fix)
    c4_timing = "BEFORE INSERT OR UPDATE ON public.pengiriman_linen" in content
    c4_ins_status = "INSERT_INVALID_STATUS" in content and "NEW.status IS DISTINCT FROM 'pending'" in content
    c4_audit_null = "INSERT_AUDIT_NOT_NULL" in content and "NEW.claimed_at IS NOT NULL" in content
    checks.append(("C4: Header Trigger BEFORE INSERT OR UPDATE dengan Proteksi State Machine & Audit NULL", c4_timing and c4_ins_status and c4_audit_null))

    # C5: Detail Trigger NULL-Safe status check & EPC 24-Hex Validation (P0 Fix)
    c5_null_safe = "v_pkg_status IS DISTINCT FROM 'pending'" in content
    c5_epc_val = "DETAIL_INVALID_EPC" in content and "DETAIL_INVALID_EPC_FORMAT" in content and "DETAIL_DUPLICATE_EPC" in content
    checks.append(("C5: Detail Trigger NULL-Safe status check & Validasi Format EPC Kanonikal 24-Hex", c5_null_safe and c5_epc_val))

    # C6: Header Immutability NULL-Safe via IS DISTINCT FROM (P1 Fix)
    c6_hosp = "NEW.hospital_id IS DISTINCT FROM OLD.hospital_id" in content
    c6_hash = "NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash" in content
    c6_claim = "NEW.claimed_at IS DISTINCT FROM OLD.claimed_at" in content
    c6_conf = "NEW.confirmed_at IS DISTINCT FROM OLD.confirmed_at" in content
    checks.append(("C6: Header Immutability Menggunakan Perbandingan NULL-Safe IS DISTINCT FROM", c6_hosp and c6_hash and c6_claim and c6_conf))

    # C7: Confirm RPC Exact 6 Canonical Parameters (No p_permit_id)
    confirm_sig_match = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.confirm_package\s*\((.*?)\)",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if confirm_sig_match:
        params_str = confirm_sig_match.group(1)
        has_permit = "permit" in params_str.lower()
        param_count = len([p for p in params_str.split(",") if p.strip()])
        c7 = (param_count == 6) and not has_permit
    else:
        c7 = False
    checks.append(("C7: Confirm RPC Signature Memiliki Tepat 6 Parameter Kanonikal (Tanpa p_permit_id)", c7))

    # C8: Confirm Overload Cleanup with to_regprocedure & Single Signature Postflight Assertion
    c8 = "OVERLOAD_CLEANUP_BLOCKED" in content and "to_regprocedure" in content and "PILAR 10" in content
    checks.append(("C8: Pembersihan Signature Overload RPC Menggunakan to_regprocedure & Assertion Tunggal", c8))

    # C9: Hardened search_path on all functions & Helper Execution Revocation
    c9_sp = "SET search_path = pg_catalog, pg_temp" in content
    c9_rev = "REVOKE ALL ON FUNCTION public.clean_epc" in content and "REVOKE ALL ON FUNCTION public.compute_package_manifest_hash" in content
    checks.append(("C9: Search_path Hardening (pg_catalog, pg_temp) & Pencabutan Hak Helper Internal", c9_sp and c9_rev))

    # C10: RLS, Table Privileges & Column-Level Security (verification_code masked)
    c10_rls = "ENABLE ROW LEVEL SECURITY" in content
    c10_mask = "verification_code" in content and "REVOKE ALL PRIVILEGES ON TABLE public.pengiriman_linen FROM PUBLIC, anon, authenticated" in content
    c10_catatan = "GRANT UPDATE (catatan) ON TABLE public.pengiriman_linen TO service_role;" in content
    checks.append(("C10: RLS Aktif, Hak Akses Column-Level (Masking verification_code & UPDATE Catatan)", c10_rls and c10_mask and c10_catatan))

    # C11: Comprehensive 12-Pillar Deep Catalog Assertions
    c11_pillars = (
        "POSTFLIGHT ASSERTION 12 PILAR EKSAK" in content
        and "confdeltype = 'r'" in content
        and "indisunique = TRUE" in content
        and "(t.tgtype & 2) <> 0" in content
        and "(t.tgtype & 4) <> 0" in content
    )
    checks.append(("C11: Postflight Assertion 12 Pilar Mendalam (FK Actions, Trigger Bits, Sequences)", c11_pillars))

    # C12: Static Schema Alignment & Timestamp Type Integrity (P0-01 & P1-03)
    c12_align = (
        "claimed_at TIMESTAMPTZ" in content
        and "confirmed_at TIMESTAMPTZ" in content
        and "claimed_by_user_id UUID" in content
        and "confirmed_by_user_id UUID" in content
        and "hospital_id BIGINT" in content
        and "manifest_hash VARCHAR(64)" in content
    )
    checks.append(("C12: Static Schema Alignment & Data Type Parity (TIMESTAMPTZ, UUID, BIGINT)", c12_align))

    # C13: Option A Invariants (Hospital-Bound Mandatory, Immutability & Oracle Protection)
    c13_ins_hosp = "INSERT_INVALID_HOSPITAL: hospital_id wajib diisi" in content
    c13_immut_hosp = "IMMUTABLE_COLUMN: hospital_id bersifat permanen dan dilarang diubah" in content

    claim_fn_start = content.find("FUNCTION public.claim_package(")
    claim_fn_end = content.find("COMMENT ON FUNCTION public.claim_package", claim_fn_start)
    claim_fn_body = content[claim_fn_start:claim_fn_end] if claim_fn_start != -1 else ""

    pos_pnb = claim_fn_body.find("PACKAGE_NOT_BOUND")
    pos_ct = claim_fn_body.find("CROSS_TENANT_ACCESS_DENIED")
    pos_ivc = claim_fn_body.find("INVALID_VERIFICATION_CODE")

    c13_seq = (pos_pnb != -1 and pos_ct != -1 and pos_ivc != -1 and pos_pnb < pos_ivc and pos_ct < pos_ivc)

    gpi_start = content.find("FUNCTION public.get_package_info(")
    gpi_end = content.find("COMMENT ON FUNCTION public.get_package_info", gpi_start)
    gpi_body = content[gpi_start:gpi_end] if gpi_start != -1 else ""
    pos_gpi_ct = gpi_body.find("CROSS_TENANT_ACCESS_DENIED")
    pos_gpi_agg = gpi_body.find("jsonb_agg")
    c13_gpi = (pos_gpi_ct != -1 and pos_gpi_agg != -1 and pos_gpi_ct < pos_gpi_agg)

    c13 = c13_ins_hosp and c13_immut_hosp and c13_seq and c13_gpi
    checks.append(("C13: Option A Invariants (Hospital-Bound Mandatory, Immutability & Oracle Protection)", c13))

    # C14: P0-01 Strict Elimination of Custom GUC Bypass
    c14_no_guc = "allow_unbound_fixture" not in content
    checks.append(("C14: P0-01 Eliminasi Mutlak Custom GUC Bypass (allow_unbound_fixture)", c14_no_guc))

    # C15: P0-02 Lock Order in Claim and Confirm (Plain SELECT & Tenant Check Precedes FOR UPDATE)
    claim_fn_start = content.find("FUNCTION public.claim_package(")
    claim_fn_end = content.find("COMMENT ON FUNCTION public.claim_package", claim_fn_start)
    claim_body = content[claim_fn_start:claim_fn_end] if claim_fn_start != -1 else ""

    confirm_fn_start = content.find("FUNCTION public.confirm_package(")
    confirm_fn_end = content.find("COMMENT ON FUNCTION public.confirm_package", confirm_fn_start)
    confirm_body = content[confirm_fn_start:confirm_fn_end] if confirm_fn_start != -1 else ""

    c15_claim_plain = claim_body.find("SELECT * INTO v_pkg\n    FROM public.pengiriman_linen\n    WHERE id = p_package_id;")
    c15_claim_ct = claim_body.find("CROSS_TENANT_ACCESS_DENIED")
    c15_claim_lock = claim_body.find("WHERE id = p_package_id\n    FOR UPDATE;")
    c15_claim_order = (
        c15_claim_plain != -1 and c15_claim_ct != -1 and c15_claim_lock != -1
        and c15_claim_plain < c15_claim_ct < c15_claim_lock
    )

    c15_conf_plain = confirm_body.find("SELECT * INTO v_pkg\n    FROM public.pengiriman_linen\n    WHERE id = p_package_id;")
    c15_conf_ct = confirm_body.find("UNAUTHORIZED_TENANT_CONFIRMATION")
    c15_conf_lock = confirm_body.find("WHERE id = p_package_id\n    FOR UPDATE;")
    c15_conf_order = (
        c15_conf_plain != -1 and c15_conf_ct != -1 and c15_conf_lock != -1
        and c15_conf_plain < c15_conf_ct < c15_conf_lock
    )
    checks.append(("C15: P0-02 Urutan Lock Aman (Plain SELECT & Validasi Tenant Mendahului SELECT FOR UPDATE)", c15_claim_order and c15_conf_order))

    # C16: P1-01 Partial Unique Index & Consistent Membership Policy (No MAX(hospital_id))
    c16_idx = "uq_hospital_memberships_user_single_active" in content and "WHERE is_active = TRUE" in content
    c16_no_max = "MAX(hospital_id)" not in content and "max(hospital_id)" not in content
    checks.append(("C16: P1-01 Partial Unique Index Keanggotaan Tunggal Aktif & Eliminasi MAX(hospital_id)", c16_idx and c16_no_max))

    all_pass = True
    for desc, passed in checks:
        status_str = "[PASS]" if passed else "[FAIL]"
        print(f"{status_str} {desc}")
        if not passed:
            all_pass = False

    print("=" * 80)
    if all_pass:
        print(f"[SUCCESS] Seluruh {len(checks)} kriteria verifikasi statik DDL R2.10.4 terpenuhi.")
        return 0
    else:
        print("[FAILURE] Terdapat kriteria statik DDL R2.10.4 yang gagal.")
        return 1

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ddl_file = os.path.join(base_dir, "07_package_communication_rpc_consolidated_r2_10_4.sql")
    if not os.path.exists(ddl_file):
        ddl_file = os.path.join(base_dir, "database_migrations", "cloud", "07_package_communication_rpc_consolidated_r2_10_4.sql")
    sys.exit(verify_ddl_static(ddl_file))

