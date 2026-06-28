from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Literal, TypedDict, cast

from voice_note import get_groq_client

DEFAULT_MODEL = "llama-3.3-70b-versatile"
SOAP_FIELDS = ("Subjective", "Objective", "Assessment", "Plan")
DIAGNOSIS_QUERY_FIELDS = {"phrase", "kind", "certainty", "qualifiers"}


class DiagnosisQuery(TypedDict):
    phrase: str
    kind: Literal["diagnosis", "symptom"]
    certainty: Literal["confirmed", "uncertain", "historical"]
    qualifiers: list[str]


class SOAPNote(TypedDict):
    Subjective: str
    Objective: str
    Assessment: str
    Plan: str
    DiagnosisQueries: list[DiagnosisQuery]


SYSTEM_PROMPT = """You are a clinical documentation assistant. Convert the supplied
medical encounter transcript into a concise SOAP note. Use only information stated
in the transcript; do not invent findings, diagnoses, medications, or follow-up.
Return one JSON object with exactly these fields: Subjective, Objective, Assessment,
and Plan as strings, plus DiagnosisQueries as a JSON array. Each DiagnosisQueries
item must contain exactly: phrase (a concise documented condition or symptom), kind
(diagnosis or symptom), certainty (confirmed, uncertain, or historical), and
qualifiers (an array of documented details such as acuity, laterality, anatomical
site, etiology, or encounter type). Preserve uncertainty wording. Include symptoms
that may require coding when no established diagnosis explains them. Do not include
ruled-out conditions, infer diagnoses, or emit ICD codes. Use an empty string for a
SOAP section with no supported content and an empty array when there are no supported
diagnosis queries. Do not include markdown or text outside the JSON object."""


def _valid_diagnosis_query(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != DIAGNOSIS_QUERY_FIELDS:
        return False
    return (
        isinstance(value["phrase"], str)
        and bool(value["phrase"].strip())
        and value["kind"] in {"diagnosis", "symptom"}
        and value["certainty"] in {"confirmed", "uncertain", "historical"}
        and isinstance(value["qualifiers"], list)
        and all(isinstance(item, str) for item in value["qualifiers"])
    )


def create_soap_note(transcript: str, model: str | None = None) -> SOAPNote:
    """Generate and validate a SOAP note from a completed transcript."""
    transcript = transcript.strip()
    if not transcript:
        raise ValueError("The completed transcript cannot be empty.")

    completion = get_groq_client().chat.completions.create(
        model=model or os.getenv("GROQ_SOAP_MODEL", DEFAULT_MODEL),
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

    expected_fields = {*SOAP_FIELDS, "DiagnosisQueries"}
    if set(note) != expected_fields or not all(
        isinstance(note[field], str) for field in SOAP_FIELDS
    ) or not isinstance(note["DiagnosisQueries"], list) or not all(
        _valid_diagnosis_query(query) for query in note["DiagnosisQueries"]
    ):
        raise ValueError(
            "Groq SOAP output must contain the four SOAP strings and a valid "
            "DiagnosisQueries array."
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
