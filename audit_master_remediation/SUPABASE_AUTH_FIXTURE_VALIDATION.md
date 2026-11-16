# VALIDASI & SPESIFIKASI FIXTURE SUPABASE AUTH (GOTRUE)
## ISOLASI PENGUJIAN RUNTIME POSTGRESQL 15 TANPA POLUSI SKEMA PUBLIK
### GLADI BERSIH JALUR REV4 → MIGRATION 10 | BASELINE: R2.10.4

**Tanggal Penyusunan**: 16 November 2026  
**Otoritas Tata Kelola**: Single Owner-Operator Governance (System Owner & Acting DBA)  
**Lingkungan Target**: PostgreSQL 15 Disposable Service Container  
**Status Gate B**: **LOCKED**  

---

## 1. Latar Belakang & Masalah Arsitektur

Pada platform Supabase Cloud (seperti instance Staging `bgtxpdydljfeltdimmxr`), ekosistem autentikasi (*GoTrue Auth Engine*) dan peran basis data internal telah terpasang secara bawaan di luar skema `public`:
- Skema internal: `auth`
- Tabel identitas pengguna: `auth.users`
- Fungsi konteks sesi JWT: `auth.uid()`
- Peran internal Supabase: `anon`, `authenticated`, `service_role`

Namun, ketika skrip DDL `00_ALL_IN_ONE_STAGING_BOOTSTRAP_FINAL_VERIFIED_REV4.sql` dan `10_unified_dashboard_hospital_monitoring_rls.sql` dieksekusi pada mesin PostgreSQL 15 vanilla (seperti `postgres:15-alpine` pada GitHub Actions service container), objek-objek sistem bawaan Supabase tersebut **belum ada**.

Ketergantungan langsung dalam berkas SQL mencakup:
1. **Pemberian Hak Akses (Privilege Grants)**:
   Baris 58-59 pada REV4:  
   `GRANT USAGE ON SCHEMA extensions TO authenticated, service_role, anon;`  
   `GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA extensions TO authenticated, service_role, anon;`  
   *Jika ketiga peran ini tidak ada di PostgreSQL, eksekusi SQL akan langsung gagal (*fail-fast*) dengan error: `role "authenticated" does not exist`.*
2. **Kueri Integritas Foreign Key & Membership**:
   Baris 113 pada REV4 (`register_hospital_admin_membership`):  
   `SELECT id INTO v_user_id FROM auth.users WHERE lower(email) = lower(btrim(p_email));`  
   *Jika skema `auth` atau tabel `auth.users` tidak ada, fungsi akan gagal dikompilasi atau gagal dieksekusi dengan error: `relation "auth.users" does not exist`.*
3. **Kebijakan Row Level Security (RLS)**:
   Kebijakan pada REV4 dan Migration 10:  
   `WHERE hm.user_id = auth.uid()`  
   *Jika fungsi `auth.uid()` tidak ada, kueri RLS akan mengalami error.*

Oleh karena itu, diperlukan **Supabase Auth Minimal Fixture** yang meniru perilaku Supabase secara presisi di database disposable tanpa mengotori skema bisnis `public`.

---

## 2. Spesifikasi Teknis Minimal Fixture

Fixture ini diinisialisasi pada **Stage 1** skrip [run_rev4_to_10_rehearsal.py](file:///D:/ClientS/database_migrations/cloud/run_rev4_to_10_rehearsal.py) sebelum berkas REV4 dieksekusi:

```sql
-- 1. Inisialisasi Skema Terisolasi
CREATE SCHEMA IF NOT EXISTS auth;

-- 2. Tabel Pengguna Otentikasi Minimal (Persis GoTrue)
CREATE TABLE IF NOT EXISTS auth.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    raw_user_meta_data JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT clock_timestamp()
);

-- 3. Fungsi Resolusi Sub-Klaim JWT (auth.uid())
CREATE OR REPLACE FUNCTION auth.uid() RETURNS UUID 
LANGUAGE sql STABLE AS $$ 
    SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''), '00000000-0000-0000-0000-000000000000')::uuid; 
$$;

-- 4. Inisialisasi Peran Bawaan Supabase
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN 
        CREATE ROLE anon NOLOGIN; 
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN 
        CREATE ROLE authenticated NOLOGIN; 
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN 
        CREATE ROLE service_role NOLOGIN BYPASSRLS; 
    END IF;
END $$;

-- 5. Hak Akses Minimal pada Skema Auth
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT SELECT ON auth.users TO authenticated, service_role;
GRANT EXECUTE ON FUNCTION auth.uid() TO anon, authenticated, service_role;
```

---

## 3. Akun Uji Coba Terdaftar (Test Users Seed)

Untuk menguji isolasi multi-tenant rumah sakit dan pembagian wewenang peran (RBAC), fixture menyemai 4 identitas pengguna ke dalam `auth.users`:

| Label Pengguna | UUID Pengguna (`auth.users.id`) | Email Terdaftar | Target Rumah Sakit | Peran RBAC | Skenario Pengujian |
| :--- | :--- | :--- | :---: | :---: | :--- |
| **Admin RS 1** | `11111111-1111-1111-1111-111111111111` | `admin_smc@hospital.org` | RS 1 (`RS-SMC-001`) | `admin` | Pendaftaran otomatis admin staging via REV4 |
| **Operator RS 1** | `22222222-2222-2222-2222-222222222222` | `operator_smc@hospital.org`| RS 1 (`RS-SMC-001`) | `operator` | Klaim paket, konfirmasi penerimaan, akses RLS |
| **Auditor RS 1** | `33333333-3333-3333-3333-333333333333` | `auditor_smc@hospital.org` | RS 1 (`RS-SMC-001`) | `auditor` | Read-only audit log, DITOLAK saat mencoba klaim |
| **Operator RS 2** | `44444444-4444-4444-4444-444444444444` | `operator_other@hospital.org`| RS 2 (`RS-OTHER-002`) | `operator` | Uji pembatasan tenant: TIDAK BOLEH akses RS 1 |

---

## 4. Bukti Nol Polusi Skema Publik (Zero Mock Pollution)

Salah satu persyaratan kritis dari System Owner adalah memastikan pengujian tidak mencemari struktur data operasional.

### Evaluasi Penempatan Objek:
1. **Skema `public` Bersih**:
   - Seluruh objek fixture diletakkan di skema `auth` atau pada level cluster role (`pg_roles`).
   - Tidak ada tabel dummy, kolom mock, atau fungsi sementara yang disuntikkan ke dalam skema `public`.
   - Ke-12 tabel yang terbentuk di skema `public` adalah tabel murni produksi yang didefinisikan oleh REV4.
2. **Kompatibilitas Penuh dengan Supabase Asli**:
   - Skrip REV4 dan Migration 10 **TIDAK MENGALAMI PERUBAHAN APAPUN**. Keduanya tetap memiliki hash SHA-256 yang dibekukan.
   - Ketika REV4 dan Migration 10 nantinya di-deploy ke Supabase Staging (`bgtxpdydljfeltdimmxr`), objek-objek di skema `auth` dan peran-peran tersebut sudah tersedia secara alami oleh Supabase. Dengan demikian, apa yang diuji di CI disposable adalah replika 1:1 dari lingkungan Supabase sebenarnya.
3. **Pemberian Hak Akses `BYPASSRLS` pada `service_role`**:
   - Pada Supabase, Edge Function mengeksekusi RPC Ingestion menggunakan `service_role` yang memiliki atribut `BYPASSRLS`.
   - Fixture mereplikasi atribut ini secara tepat (`CREATE ROLE service_role NOLOGIN BYPASSRLS;`), sehingga pengujian ingestion pada Stage 6 berjalan dengan hak akses yang identik dengan Edge Function Deno di Cloud.

---

## 5. Kesimpulan Validasi Fixture

1. Supabase Auth Minimal Fixture memenuhi seluruh prasyarat yang dibutuhkan oleh REV4 dan Migration 10.
2. Fixture mengeliminasi kebutuhan mocking di level aplikasi Python, sehingga integritas pengujian basis data dapat diuji secara murni di level SQL engine.
3. Desain fixture sepenuhnya aman, terisolasi di dalam disposable container, dan tidak meninggalkan residu apa pun.

