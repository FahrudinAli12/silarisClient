from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from fastapi import WebSocket

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

logger = logging.getLogger("rfid_service")

# Start & Stop inventory command frames for EL-UHF-RMT01
CMD_START_INVENTORY = bytes.fromhex("BB 00 27 00 03 22 27 10 83 7E")
CMD_STOP_INVENTORY = bytes.fromhex("BB 00 28 00 00 28 7E")
INVENTORY_REFRESH_SECONDS = 240


def list_available_ports() -> list[str]:
    """Scan dan daftar port serial COM yang tersedia pada sistem."""
    if serial is None or not hasattr(serial, "tools") or not hasattr(serial.tools, "list_ports"):
        return []
    try:
        ports = serial.tools.list_ports.comports()
        return [p.device for p in ports]
    except Exception:
        return []


class SingleRFIDReader:
    """Modul listener independen untuk 1 unit Reader RFID via Serial Port (UART)."""

    def __init__(
        self,
        name: str,
        port: str,
        transaction_type: str,
        baudrate: int = 115200,
        demo_mode: bool = False,
        on_epc_scanned: Optional[Callable[[str, SingleRFIDReader], None]] = None,
        on_status_changed: Optional[Callable[[bool, SingleRFIDReader], None]] = None,
    ) -> None:
        self.name = name
        self.port = port
        self.transaction_type = transaction_type
        self.baudrate = baudrate
        self.demo_mode = demo_mode or (os.getenv("RFID_DEMO_MODE", "0") == "1")

        self.on_epc_scanned = on_epc_scanned
        self.on_status_changed = on_status_changed

        self._running = False
        self._connected = False
        self._thread: threading.Thread | None = None
        self._demo_counter = 0
        self._last_connection_log_at = 0.0
        self._serial_connection = None
        self._serial_lock = threading.Lock()
        self._inventory_active = False  # Start in STANDBY mode until explicitly activated

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, args=(loop,))
        self._thread.start()
        logger.info(
            "%s: RFID service started (Port: %s, Baud: %d, Type: %s, Demo Mode: %s)",
            self.name,
            self.port,
            self.baudrate,
            self.transaction_type,
            self.demo_mode,
        )

    def stop(self) -> None:
        self._running = False
        self._connected = False
        logger.info("%s: RFID service stopped", self.name)

    def change_port(self, new_port: str) -> None:
        """Ganti port serial reader RFID secara dinamis."""
        if self.port != new_port:
            logger.info("%s: Mengubah RFID port dari %s ke %s", self.name, self.port, new_port)
            self.port = new_port
            with self._serial_lock:
                if self._serial_connection is not None:
                    try:
                        self._serial_connection.close()
                    except Exception:
                        pass

    def start_inventory(self) -> None:
        """Mulai inventory pada reader (Active Scanning Mode)."""
        with self._serial_lock:
            self._inventory_active = True
            if self._serial_connection is not None and getattr(self._serial_connection, "is_open", False):
                try:
                    if hasattr(self._serial_connection, "reset_input_buffer"):
                        self._serial_connection.reset_input_buffer()
                    self._serial_connection.write(CMD_START_INVENTORY)
                    self._serial_connection.flush()
                except Exception as e:
                    logger.warning("%s: Gagal mengirim START INVENTORY: %s", self.name, e)
            logger.info("START INVENTORY %s | %s STATUS ACTIVE", self.port, self.port)

    def stop_inventory(self) -> None:
        """Hentikan inventory pada reader (STANDBY Mode & Buffer Clear)."""
        with self._serial_lock:
            self._inventory_active = False
            if self._serial_connection is not None and getattr(self._serial_connection, "is_open", False):
                try:
                    self._serial_connection.write(CMD_STOP_INVENTORY)
                    self._serial_connection.flush()
                    if hasattr(self._serial_connection, "reset_input_buffer"):
                        self._serial_connection.reset_input_buffer()
                except Exception as e:
                    logger.warning("%s: Gagal mengirim STOP INVENTORY: %s", self.name, e)
            logger.info("STOP INVENTORY %s | %s STATUS STANDBY | BUFFER %s CLEARED", self.port, self.port, self.port)

    def is_inventory_active(self) -> bool:
        return self._inventory_active

    def get_status(self) -> dict[str, object]:
        """Status reader saat ini."""
        is_conn = self.demo_mode or self._connected or (
            self._serial_connection is not None and getattr(self._serial_connection, "is_open", False)
        )
        return {
            "name": self.name,
            "port": self.port,
            "baudrate": self.baudrate,
            "transaction_type": self.transaction_type,
            "status": "connected" if is_conn else "idle",
            "inventory_active": self._inventory_active,
            "demo_mode": self.demo_mode,
        }

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        logger.info("%s: Reader background loop started on %s", self.name, self.port)

        if self.demo_mode:
            self._run_demo_loop(loop)
            return

        buffer = bytearray()

        while self._running:
            if serial is None:
                logger.warning("%s: Package 'pyserial' belum terinstall.", self.name)
                time.sleep(5)
                continue

            try:
                logger.debug("%s: Mencoba membuka port serial %s...", self.name, self.port)
                with serial.Serial(self.port, self.baudrate, timeout=0.1) as ser:
                    with self._serial_lock:
                        self._serial_connection = ser
                    self._connected = True
                    logger.info("%s: Reader connected %s", self.name, self.port)

                    if self.on_status_changed:
                        self.on_status_changed(True, self)

                    with self._serial_lock:
                        if self._inventory_active:
                            ser.write(CMD_START_INVENTORY)
                            ser.flush()

                    last_inventory_refresh = time.monotonic()

                    while self._running:
                        with self._serial_lock:
                            active = self._inventory_active

                        if active:
                            if time.monotonic() - last_inventory_refresh >= INVENTORY_REFRESH_SECONDS:
                                with self._serial_lock:
                                    ser.write(CMD_START_INVENTORY)
                                    ser.flush()
                                last_inventory_refresh = time.monotonic()

                            data = ser.read(1024)
                            if data and self._inventory_active:
                                buffer.extend(data)
                                buffer = self._parse_buffer(buffer, loop)
                            else:
                                time.sleep(0.01)
                        else:
                            # Standby mode: Immediately purge buffer and flush serial input
                            buffer.clear()
                            try:
                                if ser.in_waiting:
                                    ser.read(ser.in_waiting)
                            except Exception:
                                pass
                            time.sleep(0.05)

                    try:
                        ser.write(CMD_STOP_INVENTORY)
                    except Exception:
                        pass
            except Exception as exc:
                was_connected = self._connected
                if self._connected:
                    self._connected = False
                    logger.error("%s: Koneksi terputus pada port %s: %s", self.name, self.port, exc)
                    if self.on_status_changed:
                        self.on_status_changed(False, self)

                if self._running:
                    now = time.monotonic()
                    if not was_connected and now - self._last_connection_log_at >= 30:
                        logger.warning(
                            "%s: Port RFID %s belum tersedia atau sedang dipakai (%s). Retry 3 detik.",
                            self.name,
                            self.port,
                            exc,
                        )
                        self._last_connection_log_at = now
                    time.sleep(3)
            finally:
                with self._serial_lock:
                    self._serial_connection = None

    def _run_demo_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        logger.info("%s: Reader demo mode aktif", self.name)
        self._connected = True
        if self.on_status_changed:
            self.on_status_changed(True, self)

        try:
            while self._running:
                try:
                    with self._serial_lock:
                        active = self._inventory_active
                    if active:
                        self._demo_counter += 1
                        epc_prefix = "E280MASUK" if self.transaction_type == "LINEN_MASUK" else "E280KELUAR"
                        demo_epc = f"{epc_prefix}{self._demo_counter:04d}"
                        if self.on_epc_scanned:
                            self.on_epc_scanned(demo_epc, self)
                    time.sleep(0.5)
                except Exception as exc:
                    logger.warning("%s: Demo error: %s", self.name, exc)
                    time.sleep(0.5)
        finally:
            self._connected = False
            if self.on_status_changed:
                self.on_status_changed(False, self)

    def _parse_buffer(self, buffer: bytearray, loop: Optional[asyncio.AbstractEventLoop] = None) -> bytearray:
        if not self._inventory_active:
            buffer.clear()
            return bytearray()

        while len(buffer) >= 7 and self._inventory_active:
            try:
                start_idx = buffer.index(0xBB)
            except ValueError:
                buffer.clear()
                break

            if start_idx > 0:
                del buffer[:start_idx]

            if len(buffer) < 5:
                break

            frame_type = buffer[1]
            cmd_code = buffer[2]
            param_len = (buffer[3] << 8) | buffer[4]

            if param_len > 256:
                del buffer[:1]
                continue

            total_frame_len = 5 + param_len + 1 + 1
            if len(buffer) < total_frame_len:
                break

            frame = bytes(buffer[:total_frame_len])
            del buffer[:total_frame_len]

            if frame[-1] == 0x7E:
                self._handle_frame(frame, frame_type, cmd_code, param_len, loop)
            else:
                logger.warning("%s: Frame RFID tidak valid: %s", self.name, frame.hex())

        return buffer

    def _handle_frame(
        self, frame: bytes, frame_type: int, cmd_code: int, param_len: int, loop: Optional[asyncio.AbstractEventLoop]
    ) -> None:
        if not self._inventory_active:
            return

        if frame_type == 0x02:
            param = frame[5 : 5 + param_len]
            if len(param) >= 15:
                epc_bytes = param[3:15]
                epc = epc_bytes.hex().upper()
                logger.info("%s: EPC detected: %s (Port: %s)", self.name, epc, self.port)
                if self.on_epc_scanned:
                    self.on_epc_scanned(epc, self)


class RFIDServiceManager:
    """Manager pengelola Dual Reader RFID dengan Active Scan Control Mode & Dynamic Detection."""

    def __init__(self, port: str | None = None, baudrate: int = 115200, demo_mode: bool = False) -> None:
        available = list_available_ports()
        default_masuk = available[0] if len(available) >= 1 else "COM3"
        default_keluar = available[1] if len(available) >= 2 else "COM4"

        port_masuk = os.getenv("RFID_MASUK_PORT", port or os.getenv("RFID_PORT", default_masuk))
        port_keluar = os.getenv("RFID_KELUAR_PORT", default_keluar)

        self.incoming_reader = SingleRFIDReader(
            name="RFID_LINEN_MASUK",
            port=port_masuk,
            transaction_type="LINEN_MASUK",
            baudrate=baudrate,
            demo_mode=demo_mode,
            on_epc_scanned=self._handle_reader_epc,
            on_status_changed=self._handle_reader_status,
        )

        self.outgoing_reader = SingleRFIDReader(
            name="RFID_LINEN_KELUAR",
            port=port_keluar,
            transaction_type="LINEN_KELUAR",
            baudrate=baudrate,
            demo_mode=demo_mode,
            on_epc_scanned=self._handle_reader_epc,
            on_status_changed=self._handle_reader_status,
        )

        self._running = False
        self._clients: list[WebSocket] = []
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

        # Mode transaksi aktif saat ini ("LINEN_MASUK" atau "LINEN_KELUAR")
        self.active_transaction_mode: str = "LINEN_MASUK"
        self.incoming_reader._inventory_active = True

        # Backward compatibility properties
        self.port = port_masuk
        self.baudrate = baudrate
        self.demo_mode = demo_mode

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self.incoming_reader.start(self._loop)
        self.outgoing_reader.start(self._loop)

        # Inisialisasi active scan control: aktifkan LINEN_MASUK secara default
        self.start_scan("LINEN_MASUK")
        logger.info("RFID Service Manager started (Active Scan Control Mode)")

    def stop(self) -> None:
        self._running = False
        self.incoming_reader.stop()
        self.outgoing_reader.stop()
        logger.info("RFID Service Manager stopped")

    def detect_active_rfid_readers(self) -> dict[str, object]:
        """Deteksi jumlah reader RFID terhubung dan tentukan mode aktif secara dinamis."""
        available = list_available_ports()

        # Dynamic auto-pair outgoing reader if 2 or more COM ports exist on system
        if len(available) >= 2 and not self.outgoing_reader.demo_mode:
            out_stat_chk = self.outgoing_reader.get_status()
            if out_stat_chk["status"] != "connected" or self.outgoing_reader.port not in available:
                other_ports = [p for p in available if p != self.incoming_reader.port]
                if other_ports:
                    self.outgoing_reader.change_port(other_ports[0])

        inc_stat = self.incoming_reader.get_status()
        out_stat = self.outgoing_reader.get_status()

        inc_conn = inc_stat["status"] == "connected"
        out_conn = out_stat["status"] == "connected"

        connected_count = (1 if inc_conn else 0) + (1 if out_conn else 0)

        # Mode IS DUAL_READER ONLY when BOTH readers are connected!
        if connected_count >= 2:
            mode_code = "DUAL_READER"
            mode_label = "Dual Reader Mode"
            port_label = f"{self.incoming_reader.port} + {self.outgoing_reader.port}"
        elif connected_count == 1:
            mode_code = "SINGLE_READER"
            mode_label = "Single Reader Mode"
            port_label = self.incoming_reader.port if inc_conn else self.outgoing_reader.port
        else:
            mode_code = "SINGLE_READER"
            mode_label = "Single Reader Mode"
            port_label = self.incoming_reader.port

        return {
            "count": connected_count,
            "mode_code": mode_code,
            "mode_label": mode_label,
            "port_label": port_label,
            "inc_connected": inc_conn,
            "out_connected": out_conn,
        }

    def start_scan(self, mode: str) -> dict[str, object]:
        """
        Aktifkan Active Scan Control Mode untuk mode transaksi tertentu ('LINEN_MASUK' / 'LINEN_KELUAR').
        Urutan Wajib:
        1. Set reader tidak digunakan inactive & stop inventory.
        2. Hapus buffer serial reader tidak digunakan.
        3. Nonaktifkan pembacaan EPC reader tidak digunakan.
        4. Aktifkan & start inventory reader tujuan.
        """
        target_mode = "LINEN_KELUAR" if "KELUAR" in mode.upper() else "LINEN_MASUK"
        self.active_transaction_mode = target_mode

        detection = self.detect_active_rfid_readers()

        if target_mode == "LINEN_MASUK":
            # STEP 1, 2, 3: STOP INVENTORY COM4 (STANDBY) & CLEAR BUFFER
            self.outgoing_reader.stop_inventory()

            # STEP 4: START INVENTORY COM3 (ACTIVE)
            self.incoming_reader.start_inventory()

            active_port = self.incoming_reader.port
            active_label = f"{active_port} - Linen Masuk"
        else:
            # STEP 1, 2, 3: STOP INVENTORY COM3 (STANDBY) & CLEAR BUFFER
            self.incoming_reader.stop_inventory()

            # STEP 4: START INVENTORY COM4 (ACTIVE)
            self.outgoing_reader.start_inventory()

            active_port = self.outgoing_reader.port
            active_label = f"{active_port} - Linen Keluar"

        ws_payload = {
            "type": "rfid_status",
            "mode": detection["mode_code"],
            "mode_label": detection["mode_label"],
            "port": detection["port_label"],
            "active_reader": active_port,
            "active_scanner_label": active_label,
            "transaction_type": target_mode,
            "status": "ACTIVE",
        }

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._broadcast_json(ws_payload)))

        return {
            "status": "ok",
            "active_reader": active_port,
            "active_scanner_label": active_label,
            "transaction_type": target_mode,
            "mode": detection["mode_label"],
            "port": detection["port_label"],
        }

    def change_port(self, new_port: str, target: str = "masuk") -> None:
        """Ganti port serial secara dinamis (target: 'masuk' / 'keluar' / 'all')."""
        available = list_available_ports()
        if target == "keluar":
            self.outgoing_reader.change_port(new_port)
        else:
            self.incoming_reader.change_port(new_port)
            self.port = new_port
            if len(available) >= 2 and new_port in available:
                other_ports = [p for p in available if p != new_port]
                if other_ports:
                    self.outgoing_reader.change_port(other_ports[0])

    def start_inventory(self, target: str = "all") -> None:
        """Backward-compatible start inventory delegator."""
        if target in ("masuk", "all"):
            self.start_scan("LINEN_MASUK")
        if target == "keluar":
            self.start_scan("LINEN_KELUAR")

    def stop_inventory(self, target: str = "all") -> None:
        """Hentikan inventory pada seluruh/target reader."""
        if target in ("masuk", "all"):
            try:
                self.incoming_reader.stop_inventory()
            except Exception:
                pass
        if target in ("keluar", "all"):
            try:
                self.outgoing_reader.stop_inventory()
            except Exception:
                pass

    def get_status(self) -> dict[str, object]:
        """Status RFID reader dan Active Scan Control Mode."""
        detection = self.detect_active_rfid_readers()
        connected_count = detection["count"]
        inc_conn = detection["inc_connected"]
        out_conn = detection["out_connected"]

        overall_connected = connected_count > 0

        is_masuk_active = self.active_transaction_mode == "LINEN_MASUK"

        if is_masuk_active:
            active_port = self.incoming_reader.port
            active_scanner_label = f"{active_port} - Linen Masuk"
        else:
            active_port = self.outgoing_reader.port
            active_scanner_label = f"{active_port} - Linen Keluar"

        last_sync_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        readers_status_list = [
            {
                "port": self.incoming_reader.port,
                "function": "LINEN_MASUK",
                "status": "ACTIVE" if (is_masuk_active and inc_conn and self.incoming_reader.is_inventory_active()) else "STANDBY",
            },
            {
                "port": self.outgoing_reader.port,
                "function": "LINEN_KELUAR",
                "status": "ACTIVE" if (not is_masuk_active and out_conn and self.outgoing_reader.is_inventory_active()) else "STANDBY",
            },
        ]

        return {
            "status": "connected" if overall_connected else "disconnected",
            "mode": detection["mode_label"],
            "mode_code": detection["mode_code"],
            "port": detection["port_label"],
            "active_reader": active_port,
            "transaction_type": self.active_transaction_mode,
            "active_scanner_label": active_scanner_label,
            "last_sync": last_sync_str,
            "reader_count": connected_count,
            "reader": readers_status_list,
            "baudrate": self.baudrate,
            "demo_mode": self.demo_mode,
            "available_ports": list_available_ports(),
            "incoming": self.incoming_reader.get_status(),
            "outgoing": self.outgoing_reader.get_status(),
        }

    def register_client(self, websocket: WebSocket) -> None:
        with self._lock:
            self._clients.append(websocket)

    def unregister_client(self, websocket: WebSocket) -> None:
        with self._lock:
            if websocket in self._clients:
                self._clients.remove(websocket)

    def _handle_reader_epc(self, epc: str, reader: SingleRFIDReader) -> None:
        # Strict Active Reader Filtering: MUST be in ACTIVE status AND match active_transaction_mode!
        if not reader.is_inventory_active() or reader.transaction_type != self.active_transaction_mode:
            logger.debug(
                "IGNORED EPC %s from standby reader %s (%s). Active mode is %s",
                epc,
                reader.name,
                reader.port,
                self.active_transaction_mode,
            )
            return

        timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(
            "Transaction: %s | EPC: %s | Active Reader: %s | Port: %s",
            reader.transaction_type,
            epc,
            reader.name,
            reader.port,
        )

        payload = {
            "type": "epc",
            "epc": epc,
            "reader_name": reader.name,
            "port": reader.port,
            "transaction_type": reader.transaction_type,
            "timestamp": timestamp_str,
        }

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._broadcast_json(payload)))

    def _handle_reader_status(self, connected: bool, reader: SingleRFIDReader) -> None:
        detection = self.detect_active_rfid_readers()

        is_masuk_active = self.active_transaction_mode == "LINEN_MASUK"
        if is_masuk_active:
            active_port = self.incoming_reader.port
            active_label = f"{active_port} - Linen Masuk"
        else:
            active_port = self.outgoing_reader.port
            active_label = f"{active_port} - Linen Keluar"

        payload = {
            "type": "rfid_status",
            "reader_name": reader.name,
            "port": detection["port_label"],
            "mode": detection["mode_code"],
            "mode_label": detection["mode_label"],
            "active_reader": active_port,
            "active_scanner_label": active_label,
            "transaction_type": self.active_transaction_mode,
            "status": "connected" if connected else "disconnected",
            "incoming": self.incoming_reader.get_status(),
            "outgoing": self.outgoing_reader.get_status(),
        }
        logger.info(
            "Reader status event: %s port %s %s -> Real-time Mode: %s (%s)",
            reader.name,
            reader.port,
            "connected" if connected else "disconnected (UNPLUGGED)",
            detection["mode_label"],
            detection["port_label"],
        )
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._broadcast_json(payload)))

    async def _broadcast_json(self, payload: dict[str, object]) -> None:
        with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception as exc:
                logger.warning("WebSocket send failed: %s", exc)
                self.unregister_client(ws)

    def _parse_buffer(self, buffer: bytearray, loop: Optional[asyncio.AbstractEventLoop] = None) -> bytearray:
        """Backward-compatible helper untuk parse buffer pada reader utama (COM3)."""
        target_loop = loop or self._loop
        if target_loop is None:
            try:
                target_loop = asyncio.get_running_loop()
            except RuntimeError:
                target_loop = None
        return self.incoming_reader._parse_buffer(buffer, target_loop)


# Alias untuk 100% backward compatibility
RFIDService = RFIDServiceManager
