import asyncio
import time

from app.services.rfid_service import RFIDService


class DummyWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)



def test_rfid_service_broadcasts_quickly() -> None:
    async def run_test() -> None:
        service = RFIDService(demo_mode=True)
        websocket = DummyWebSocket()
        service.register_client(websocket)
        service.start()
        await asyncio.sleep(0.8)
        service.stop()
        await asyncio.sleep(0.1)
        assert websocket.messages, "RFID service should broadcast EPC quickly in demo mode"

    asyncio.run(run_test())


def test_rfid_service_parses_frame() -> None:
    async def run_test() -> None:
        service = RFIDService()
        websocket = DummyWebSocket()
        service.register_client(websocket)
        service._loop = asyncio.get_running_loop()
        
        # Simulated Notification Frame (Type 0x02, CMD 0x22, PL 0x0011)
        # Parameter: RSSI(C9) + PC(34 00) + EPC(30 75 1F EB 70 5C 59 04 E3 D5 0D 70) + CRC(3A 76)
        frame = bytes.fromhex("BB 02 22 00 11 C9 34 00 30 75 1F EB 70 5C 59 04 E3 D5 0D 70 3A 76 EF 7E")
        service._parse_buffer(bytearray(frame))
        await asyncio.sleep(0.1)
        
        assert len(websocket.messages) == 1
        assert websocket.messages[0]["epc"] == "30751FEB705C5904E3D50D70"

    asyncio.run(run_test())

