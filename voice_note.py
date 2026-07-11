from __future__ import annotations

import asyncio
import os
import re
import tempfile
import time
from functools import cache
from pathlib import Path
from typing import Awaitable, Callable

from groq import Groq

from observability import metrics
from observability.tracing import trace_span

TranscriptSender = Callable[[dict], Awaitable[None]]
TRANSCRIPTION_MODEL = "whisper-large-v3"


class VoiceNote:
    def __init__(
        self,
        session_id: str,
        audio_suffix: str = ".webm",
    ) -> None:
        self.session_id = session_id
        safe_session_id = self._safe_filename(session_id)
        self.current_transcript = ""
        self.transcription_failed = False

        audio_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=audio_suffix,
            prefix=f"{safe_session_id}-",
        )
        self.audio_path = Path(audio_file.name)
        audio_file.close()

    async def process_chunk(
        self,
        chunk: bytes,
        sequence: int,
        send_update: TranscriptSender,
    ) -> None:
        #append chunk to audio file
        await asyncio.to_thread(self.append_audio_chunk, chunk) 
        await send_update(
            {
                "type": "audio.chunk.received",
                "session_id": self.session_id,
                "sequence": sequence,
                "size": len(chunk),
            }
        )

        try:
            #transcribe the audio 
            transcript = await asyncio.to_thread(self.transcribe, self.audio_path)
        except Exception as exc:
            self.transcription_failed = True
            await send_update(
                {
                    "type": "transcript.error",
                    "session_id": self.session_id,
                    "sequence": sequence,
                    "message": str(exc),
                }
            )
            return

        if transcript != self.current_transcript:
            self.current_transcript = transcript
            await send_update(
                {
                    "type": "transcript.update",
                    "session_id": self.session_id,
                    "sequence": sequence,
                    "text": transcript,
                }
            )

    def append_audio_chunk(self, chunk: bytes) -> None:
        with self.audio_path.open("ab") as audio_file:
            audio_file.write(chunk)

    async def finish(self) -> str:
        await asyncio.to_thread(self._remove_temp_audio)
        return self.current_transcript

    @staticmethod
    def transcribe(filename: str | os.PathLike[str]) -> str:
        path = Path(filename)
        start = time.perf_counter()
        status = "success"
        with trace_span(
            "transcribe_audio",
            run_type="llm",
            inputs={"audio_file": path.name},
            metadata={"model": TRANSCRIPTION_MODEL},
        ) as span:
            try:
                with path.open("rb") as audio_file:
                    audio_bytes = audio_file.read()
                    transcription = get_groq_client().audio.transcriptions.create(
                        file=(path.name, audio_bytes),
                        model=TRANSCRIPTION_MODEL,
                        temperature=0,
                        response_format="verbose_json",
                    )
            except Exception as exc:
                status = "error"
                span.set_error(exc)
                raise
            finally:
                metrics.TRANSCRIPTION_CALLS.labels(
                    model=TRANSCRIPTION_MODEL, status=status
                ).inc()
                metrics.TRANSCRIPTION_LATENCY.labels(
                    model=TRANSCRIPTION_MODEL, status=status
                ).observe(time.perf_counter() - start)

            text = transcription.text or ""
            span.set_outputs(
                {
                    "status": status,
                    "audio_bytes": len(audio_bytes),
                    "transcript_char_count": len(text),
                }
            )
            return text

    @staticmethod
    def _safe_filename(value: str) -> str:
        safe_value = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
        return safe_value or "voice-note"

    def _remove_temp_audio(self) -> None:
        self.audio_path.unlink(missing_ok=True)


@cache
def get_groq_client() -> Groq:
    return Groq()
