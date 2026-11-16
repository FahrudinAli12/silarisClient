from __future__ import annotations

import hashlib
from typing import Any, Sequence
import unicodedata

from app.services.cloud_sync_service import clean_epc


def compute_canonical_manifest_hash(items: Sequence[dict[str, Any]]) -> str:
    """
    Algoritma kanonik perhitungan manifest hash Tahap 4:
    1. Normalisasi EPC dengan clean_epc().
    2. Deteksi duplikasi setelah normalisasi.
    3. Normalisasi Unicode metadata (kategori, nama_linen) menggunakan form 'NFC'.
    4. Pengurutan leksikografis deterministik berdasarkan normalized EPC.
    5. Serialisasi length-prefixed UTF-8: {len_epc}:{epc}:{len_kat}:{kat}:{len_nama}:{nama};
    6. Hashing SHA-256 heksadesimal lowercase.
    
    Catatan: Kontrak hash kanonik cloud belum difinalisasi secara resmi.
    Implementasi ini berlaku sebagai kontrak kanonik lokal hingga cloud menyetujui spesifikasi yang sama.
    """
    if not items:
        raise ValueError("EMPTY_MANIFEST: Manifest tidak boleh kosong.")

    normalized_items: list[dict[str, str]] = []
    seen_epcs: set[str] = set()

    for item in items:
        raw_epc = item.get("epc")
        if not raw_epc or not str(raw_epc).strip():
            raise ValueError("MALFORMED_ITEM: EPC tidak boleh kosong.")

        norm_epc = clean_epc(str(raw_epc))
        if norm_epc in seen_epcs:
            raise ValueError(f"DUPLICATE_MANIFEST_EPC: Tag EPC '{norm_epc}' duplikat setelah normalisasi.")
        seen_epcs.add(norm_epc)

        kategori_raw = str(item.get("kategori") or "").strip()
        nama_linen_raw = str(item.get("nama_linen") or "").strip()

        kategori = unicodedata.normalize("NFC", kategori_raw)
        nama_linen = unicodedata.normalize("NFC", nama_linen_raw)

        normalized_items.append({
            "epc": norm_epc,
            "kategori": kategori,
            "nama_linen": nama_linen,
        })

    # Pengurutan deterministik berdasarkan normalized epc (urutan byte UTF-8)
    normalized_items.sort(key=lambda x: x["epc"].encode("utf-8"))

    # Serialisasi length-prefixed berbasis panjang byte UTF-8
    serialized_parts: list[str] = []
    for it in normalized_items:
        epc_bytes = it["epc"].encode("utf-8")
        kat_bytes = it["kategori"].encode("utf-8")
        nama_bytes = it["nama_linen"].encode("utf-8")

        part = f"{len(epc_bytes)}:{it['epc']}:{len(kat_bytes)}:{it['kategori']}:{len(nama_bytes)}:{it['nama_linen']};"
        serialized_parts.append(part)

    canonical_string = "".join(serialized_parts)
    return hashlib.sha256(canonical_string.encode("utf-8")).hexdigest().lower()


def validate_scan_completeness(
    manifest_items: Sequence[dict[str, Any]],
    scanned_tags: Sequence[str]
) -> dict[str, Any]:
    """
    Membandingkan himpunan EPC unik hasil scan fisik dengan manifest cloud:
    - Normalisasi kedua himpunan dengan clean_epc().
    - Menghitung missing_epcs, unexpected_epcs, dan status kelengkapan 100%.
    """
    manifest_epcs = set(clean_epc(it["epc"]) for it in manifest_items if it.get("epc"))
    scanned_epcs = set(clean_epc(t) for t in scanned_tags if clean_epc(t))

    missing = manifest_epcs - scanned_epcs
    unexpected = scanned_epcs - manifest_epcs

    is_complete_100 = (len(missing) == 0 and len(unexpected) == 0 and len(manifest_epcs) > 0)

    return {
        "is_complete_100": is_complete_100,
        "missing_epcs": sorted(list(missing)),
        "unexpected_epcs": sorted(list(unexpected)),
        "manifest_count": len(manifest_epcs),
        "scanned_unique_count": len(scanned_epcs),
    }

