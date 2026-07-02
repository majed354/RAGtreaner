# Round 39 Collection Closure Gate

- generated_at: `2026-05-15T13:27:22.970094+03:00`
- service_url: `http://127.0.0.1:8000`
- retrieval_profile: `jamia_recall` (`dense=0.70`, `lexical=0.30`)
- readiness: `/health=ok`, `admin=200`, Chroma `saudi_legal_consultations=22612`, metric `cosine`

## Corpus Changes
- Added P0 selected procurement companions: `government-procurement-implementing-regulation` (10 chunks), `procurement-conduct-ethics-regulation` (9 chunks).
- Rebuilt `execution-implementing-regulation` using Arabic OCR for weak PDF extraction: 125 chunks -> 350 chunks.
- Final structured chunks and Chroma count: `22612`.

## Gate Results
| Gate case | core | companion | bundle | missing companions |
|---|---:|---:|---:|---|
| `targeted_procurement_grievance` | 1.000 | 1.000 | 1.000 | - |
| `manual_labor` | 1.000 | 1.000 | 1.000 | - |
| `manual_listed_company` | 1.000 | 1.000 | 1.000 | - |
| `manual_electronic_enforcement` | 1.000 | 1.000 | 1.000 | - |
| `working_vat_einvoicing` | 1.000 | 1.000 | 1.000 | - |
| `working_ecommerce_pdpl` | 1.000 | 1.000 | 1.000 | - |
| `heldout_procurement_bidrigging` | 1.000 | 1.000 | 1.000 | - |

- all_pass: `True`

## Issue Classification
- operational issue: external DNS and sandbox loopback hiccups only; not counted as RAG gaps.
- retrieval/package issue: execution implementing regulation was present but OCR-garbled, causing one pre-patch companion miss; resolved after OCR and reindex.
- answer-level issue: not evaluated here; this round is recall/collection closure.

## Remaining Gap
- No tested P0/gold collection gap remains.
- Residual risk: full clean official extraction can harden selected procurement anchors and OCR-imperfect execution text, but this is a corpus-hardening item, not a current tested collection blocker.
