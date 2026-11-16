# Checks, fraud signals, routing

## Principles

- A model finds facts. Code gives the points and decides the outcome.
- Each signal has evidence: the documents and pages it comes from.
- The system never rejects a claim. The strongest outcome is `investigation`.
- The customer never sees a signal, a score or the report. The customer message is the same for `handler_review` and `investigation`.

## Signals of the first version

| Code | Category | Signal | Severity | Points |
|---|---|---|---|---|
| R001 | rule | A required document is missing | medium | 25 |
| R002 | rule | Incident date outside the policy period | high | 40 |
| R003 | rule | Incident within 14 days of the policy start | medium | 20 |
| R004 | rule | Claimed amount at 95% or more of the coverage limit | low | 10 |
| R005 | rule | Invoice dated before the incident | medium | 20 |
| R006 | rule | Three or more earlier claims in the 12 months before the incident | medium | 20 |
| R007 | rule | An earlier claim of this customer was closed as fraud | high | 40 |
| R008 | rule | The payout account is also the account of another person | high | 40 |
| R009 | rule | Phone or address shared with a person who has a claim closed as fraud | medium | 20 |
| R010 | rule | A provider on a document has earlier claims closed as fraud | medium | 20 |
| T001 | tampering | PDF last written by an editing tool | medium | 20 |
| T002 | tampering | PDF modified more than one day after its creation | low | 10 |
| T003 | tampering | Line items do not add up to the total | high | 30 |
| C001 | consistency | Name on a document is not the insured person | medium | 20 |
| C002 | consistency | Dates of the documents do not fit the declared incident | medium | 15 |
| C003 | consistency | Claimed amount differs from the documented amount | medium | 15 |
| C004 | consistency | Photo or report does not fit the declared damage (VLM) | medium | 20 |

The points are a first guess. Tune them on the mock test set.

R006 to R010 (issue #90) do not read a document of the claim: they read the history of the customer and the links between persons (`backend/app/checks/history.py`, `data/mock/history.json`). They run inside `run_checks`. Only R010 has a document as evidence (the page that names the provider); the others name the earlier claim or the other person in their detail. The handler reads the same data on the case page, section "History and links".

## Routing

1. Score = sum of the points.
2. `investigation` if the score is 60 or more.
3. `handler_review` if the score is 20 or more, or a required document is missing, or the claimed amount is above the straight-through limit of the workflow, or a field has low confidence, or the settlement proposal needs a person (`docs/WORKFLOWS.md`, "Settlement"), or the payout account is not known or is shared with another person (issue #94).
4. `straight_through` in all other cases.

When a required document is missing (R001), the claim also waits for the customer (status `waiting_customer`, issue #72). The customer can upload the document later: the checks and the routing run again and give a new score and a new outcome. See `docs/ARCHITECTURE.md`, section 5b.

## Fraud report

Written for every `investigation`. The handler must understand it in one minute.

- Outcome and score.
- One line that says why, in plain words.
- Each signal: what the system saw, where (document and page), and what it expected.
- What the system did not check.
- Suggested next steps for the investigator.

A template writes the text from the signals, in every mode. It adds no fact. Later (issue #26) an LLM can write it from the signals only, and it must add no fact.

## The investigation case (issue #92)

A report is a start, not an end. A claim in investigation has a case on the handler desk:

- **Plan**: one task per kind of signal, in plain words ("Ask the provider for the original document", "Check who owns the payout account, before any payment"). The table is `TASKS` in `backend/app/investigation.py`.
- **Tasks**: the investigator ticks each one, with his name and what he found.
- **Evidence log**: free notes, each with a name and a time.
- **Conclusion**: "fraud confirmed" or "not confirmed", with a text, when every task is done. The claim then goes back to a handler, who decides. A conclusion is never a refusal, and the customer sees nothing of the case.

## Later (not in the first version)

Duplicate invoices and photos across claims, photo forensics (edited or reused photos, EXIF data), font and pixel analysis of scans. For the history and the links: the claims of the demo itself as history, a graph view, signals per line of business. For the investigation: the conclusions as input to tune the points, a confirmed fraud written to the history of the person.
