from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect

from voice_note import VoiceNote
from websocket_manager import websocket_manager

app = FastAPI()


@app.post("/note")
async def create_note():
    return {"status": "not_implemented"}


@app.websocket("/ws/audio/{session_id}")
async def stream_audio(session_id: str, websocket: WebSocket):
    await websocket_manager.connect(session_id, websocket)
    voice_note = VoiceNote(session_id=session_id)
    disconnected = False

    try:
        async for chunk in websocket_manager.stream_audio_chunks(session_id, websocket):
            await voice_note.process_chunk(
                chunk.data,
                chunk.sequence,
                lambda message: websocket_manager.send_json(session_id, message),
            )
    except WebSocketDisconnect:
        disconnected = True
    finally:
        transcript_path = await voice_note.finish()
        if not disconnected:
            await websocket_manager.send_json(
                session_id,
                {
                    "type": "transcript.final",
                    "session_id": session_id,
                    "text": voice_note.current_transcript,
                    "path": str(transcript_path),
                },
            )
        websocket_manager.disconnect(session_id)
