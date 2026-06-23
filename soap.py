from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TypedDict, cast

from voice_note import get_groq_client

DEFAULT_MODEL = "llama-3.3-70b-versatile"
SOAP_FIELDS = ("Subjective", "Objective", "Assessment", "Plan")


class SOAPNote(TypedDict):
    Subjective: str
    Objective: str
    Assessment: str
    Plan: str


SYSTEM_PROMPT = """You are a clinical documentation assistant. Convert the supplied
medical encounter transcript into a concise SOAP note. Use only information stated
in the transcript; do not invent findings, diagnoses, medications, or follow-up.
Return one JSON object with exactly these string fields: Subjective, Objective,
Assessment, and Plan. Use an empty string when a section has no supported content.
Do not include markdown or any text outside the JSON object."""


def create_soap_note(transcript: str) -> SOAPNote:
    """Generate and validate a SOAP note from a completed transcript."""
    transcript = transcript.strip()
    if not transcript:
        raise ValueError("The completed transcript cannot be empty.")

    completion = get_groq_client().chat.completions.create(
        model=os.getenv("GROQ_SOAP_MODEL", DEFAULT_MODEL),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    content = completion.choices[0].message.content
    if not content:
        raise ValueError("Groq returned an empty SOAP note.")

    try:
        note = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Groq returned invalid JSON for the SOAP note.") from exc

    if not isinstance(note, dict):
        raise ValueError("Groq returned a SOAP note that is not a JSON object.")

    if set(note) != set(SOAP_FIELDS) or not all(
        isinstance(note[field], str) for field in SOAP_FIELDS
    ):
        raise ValueError(
            "Groq SOAP output must contain exactly Subjective, Objective, "
            "Assessment, and Plan as strings."
        )

    return cast(SOAPNote, note)


def create_soap_note_from_file(
    transcript_path: str | Path,
    model: str | None = None,
) -> SOAPNote:
    """Read a completed transcript file and generate its SOAP note."""
    transcript = Path(transcript_path).read_text(encoding="utf-8")
    return create_soap_note(transcript, model=model)


async def create_soap_note_async(
    transcript: str,
    model: str | None = None,
) -> SOAPNote:
    """Generate a SOAP note without blocking an async request handler."""
    return await asyncio.to_thread(create_soap_note, transcript, model)
