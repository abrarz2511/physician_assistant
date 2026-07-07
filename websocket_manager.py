from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect


@dataclass
class AudioChunk:
    session_id: str
    data: bytes
    sequence: int


class WebSocketManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self._chunk_sequences: dict[str, int] = {}

    async def connect(
        self, session_id: str, websocket: WebSocket, encounter_id: str
    ) -> None:
        await websocket.accept()
        self.active_connections[encounter_id] = websocket
        self._chunk_sequences[encounter_id] = 0
        await self.send_json(
            encounter_id,
            {
                "type": "connection.accepted",
                "session_id": session_id,
                "encounter_id": encounter_id,
            },
        )

    def disconnect(self, session_id: str) -> None:
        self.active_connections.pop(session_id, None)
        self._chunk_sequences.pop(session_id, None)

    async def send_text(self, session_id: str, message: str) -> None:
        websocket = self.active_connections.get(session_id)
        if websocket is not None:
            await websocket.send_text(message)

    async def send_json(self, session_id: str, message: dict) -> None:
        websocket = self.active_connections.get(session_id)
        if websocket is not None:
            await websocket.send_json(message)

    async def stream_audio_chunks(
        self,
        session_id: str,
        websocket: WebSocket,
    ) -> AsyncIterator[AudioChunk]:
        try:
            while True:
                message = await websocket.receive()
                message_type = message.get("type")

                if message_type == "websocket.disconnect":
                    raise WebSocketDisconnect

                chunk = message.get("bytes")
                if chunk is None:
                    text = message.get("text")
                    if text == "stop":
                        await self.send_json(session_id, {"type": "stream.stopped"})
                        break

                    await self.send_json(
                        session_id,
                        {
                            "type": "stream.error",
                            "message": "Expected binary audio chunk or stop message.",
                        },
                    )
                    continue

                sequence = self._chunk_sequences.get(session_id, 0) + 1
                self._chunk_sequences[session_id] = sequence
                yield AudioChunk(session_id=session_id, data=chunk, sequence=sequence)
        except WebSocketDisconnect:
            raise


websocket_manager = WebSocketManager()
