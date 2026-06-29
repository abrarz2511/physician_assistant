# Retrieval Flows

The application uses two independent hybrid retrieval processes. Both combine
local BM25 results with Qdrant vector results using reciprocal rank fusion
(RRF), but they serve different coding tasks.

## ICD-10-CM retrieval

`retrieve_icd.py` accepts the SOAP note's `DiagnosisQueries` and the date of
service. It searches the Alphabetic Index first, follows one-hop cross
references, confirms candidate codes against exact Tabular List leaf codes,
and retrieves relevant coding guidelines.

```mermaid
flowchart TD
    A[SOAP DiagnosisQueries<br/>and service date] --> B{Date within loaded<br/>FY2027 code set?}
    B -- No --> C[Return validation error]
    B -- Yes --> D[Process each diagnosis phrase<br/>and documented qualifiers]

    D --> E1[BM25 search<br/>ICD Alphabetic Index]
    D --> E2[Vector search<br/>ICD Alphabetic Index]
    E1 --> F[Reciprocal rank fusion]
    E2 --> F
    F --> G[Follow one-hop<br/>See / See also references]
    G --> H[Extract intermediate codes]

    D --> I1[BM25 search<br/>ICD Tabular List]
    D --> I2[Vector search<br/>ICD Tabular List]
    I1 --> J[Reciprocal rank fusion]
    I2 --> J

    H --> K[Combine weighted Alphabetic<br/>and Tabular scores]
    J --> K
    K --> L[Reject codes without an exact<br/>Tabular leaf-code match]
    L --> M[Rank confirmed candidates]

    M --> N1[BM25 search<br/>ICD Guidelines]
    M --> N2[Vector search<br/>ICD Guidelines]
    N1 --> O[Reciprocal rank fusion]
    N2 --> O

    M --> P[Candidate ICD codes<br/>with source evidence and warnings]
    O --> P
```

Output is supporting evidence and ranked candidates, not a final coding
decision. Each candidate includes Alphabetic and Tabular evidence, rank,
score, and documentation warnings. Guideline passages are returned separately
for downstream review or model context.

## CMS E/M retrieval

`retrieve_cms.py` accepts an encounter setting and patient type. It constructs
one core query and runs that same query against the MLN E/M Guide and Claims
Processing Manual Chapter 12.

```mermaid
flowchart TD
    A[Encounter setting<br/>and patient type] --> B[Build core CMS query:<br/>setting + patient type + E/M service code level<br/>MDM time documentation medical necessity]

    B --> C1[BM25 search<br/>MLN E/M Guide]
    B --> C2[Vector search<br/>MLN E/M Guide]
    C1 --> D[Reciprocal rank fusion]
    C2 --> D

    B --> E1[BM25 search<br/>Claims Manual Chapter 12]
    B --> E2[Vector search<br/>Claims Manual Chapter 12]
    E1 --> F[Reciprocal rank fusion]
    E2 --> F

    D --> G[Merge and rank evidence]
    F --> G
    G --> H[Top CMS chunks<br/>with both sources represented]
    H --> I[SOAP JSON + CMS evidence<br/>sent to coding model]
    I --> J[Proposed E/M service code,<br/>selection method, modifiers,<br/>add-ons, and missing documentation]
    J --> K[Deterministic validation]

    K --> L1[Setting and patient-type<br/>service-family check]
    K --> L2[Time or MDM<br/>documentation check]
    K --> L3[Modifier 25<br/>same-day separate E/M check]
    K --> L4[G2211 base-code check]
    K --> L5[Prolonged-service<br/>time-threshold check]

    L1 --> M[Validation result<br/>with rule-specific issues]
    L2 --> M
    L3 --> M
    L4 --> M
    L5 --> M
```

The retrieval module supplies the evidence and deterministic validation. The
coding-model call shown between those stages is the downstream integration
boundary and is intentionally not tied to a specific LLM provider inside the
retriever.

## Shared retrieval pattern

```mermaid
flowchart LR
    A[Structured clinical input] --> B[Purpose-built query]
    B --> C[BM25 lexical search]
    B --> D[Qdrant semantic search]
    C --> E[RRF rank fusion]
    D --> E
    E --> F[Source-grounded evidence]
    F --> G[Downstream coding decision support]
```

BM25 recovers exact codes and terminology, while vector search recovers
semantically related language. RRF combines their ranks without comparing the
channels' incompatible raw scores.
