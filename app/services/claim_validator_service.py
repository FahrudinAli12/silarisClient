from __future__ import annotations

from typing import Any


class MalformedResponseError(RuntimeError):
    """Exception untuk respons cloud yang tidak lengkap, tidak valid, atau malformed."""
    pass


def evaluate_claim_response(
    claim_res: Any,
    expected_paket_id: int,
    expected_op_id: str,
    expected_inst_uid: str,
    expected_hash: str
) -> str:
    """
    Evaluasi respons RPC Claim 5-tahap secara berurutan:
    1. Kelengkapan dan tipe field wajib.
    2. Validasi paket_id target.
    3. Validasi status paket == 'claimed'.
    4. Evaluasi kepemilikan (conflict vs owner).
    5. Evaluasi manifest_hash (manifest_mismatch vs verified).
    """
    if not isinstance(claim_res, dict):
        raise MalformedResponseError("Respons RPC Claim bukan objek JSON/dict valid.")

    ret_paket_id = claim_res.get("paket_id")
    ret_status = claim_res.get("status_paket")
    ret_op_id = claim_res.get("operation_id")
    ret_inst_uid = claim_res.get("installation_uid")
    ret_hash = claim_res.get("manifest_hash")

    # Tahap 1: Validasi keberadaan field wajib (tidak boleh None atau string kosong)
    if any(v is None for v in (ret_paket_id, ret_status, ret_op_id, ret_inst_uid, ret_hash)):
        raise MalformedResponseError("Field wajib respons RPC Claim tidak lengkap. Tetap claim_uncertain.")

    if not str(ret_op_id).strip() or not str(ret_inst_uid).strip() or not str(ret_hash).strip():
        raise MalformedResponseError("Field identitas respons RPC Claim kosong string. Tetap claim_uncertain.")

    # Tahap 2: Validasi paket_id
    if ret_paket_id != expected_paket_id:
        raise MalformedResponseError(
            f"ID paket respons ({ret_paket_id}) tidak cocok dengan paket target ({expected_paket_id})."
        )

    # Tahap 3: Validasi status klaim di cloud (Wajib 'claimed')
    if ret_status != "claimed":
        # Jika bukan 'claimed' (misal 'pending' atau 'cancelled'), perbedaan op_id BUKAN bukti konflik kepemilikan
        raise MalformedResponseError(
            f"Status paket di cloud bukan 'claimed' (diterima: '{ret_status}'). Tetap claim_uncertain."
        )

    # Tahap 4: Verifikasi kepemilikan (Konflik hanya jika status claimed tapi dimiliki instalasi/operasi lain)
    if ret_op_id != expected_op_id or ret_inst_uid != expected_inst_uid:
        return "conflict"

    # Tahap 5: Verifikasi manifest hash
    if ret_hash != expected_hash:
        return "manifest_mismatch"

    return "verified"

