# Base roadmap: what we build while Nikola builds the reader

Written on 2026-11-16. Owner: Paul and the coordinating agent ("the base").

This document lists the work that is ours. None of it touches a pull request of Nikola. The aim: the demo must handle a claim as an insurer does, from the first call to the payment, with a person at each decision. Today it stops at a routing label.

The insurance practice in this document is a first reading. Paul confirms or corrects it (section 8), as for `docs/WORKFLOWS.md` (O12).

## 1. The boundary

| | Nikola (fork, pull requests) | The base (this document) |
|---|---|---|
| Question | What is written in the documents? Is a document suspect in itself? | What does the insurer do with these facts? |
| Folders | `backend/app/docintel/`, the evaluation scripts, the rails | `backend/app/workflows/`, `checks/rules.py`, `fraud/`, `agent/`, `store/`, the handler pages of `frontend/`, `data/mock/policies.json` |
| Issues | M4: #17 to #22, #62. M5: #24 to #30 | The work packages of section 4 (no issue yet) |
| Contract | The `Reader` contract and the `Signal` model | We read them. We do not change them without an issue that Nikola sees. |

Three rules keep the two tracks apart:

1. We do not edit `backend/app/docintel/`. A need for a new field or a new document type is an issue for Nikola.
2. A change of a shared model (`ExtractedField`, `Signal`, `Document`) is one small pull request, alone, merged first, and announced in the issue of Nikola that it touches.
3. Every package below works in `local` mode with the fixture reader. So it does not wait for the Omni reader. When the Omni reader is ready, the same package runs on real reading.

## 2. Where we are, against the practice

A real claim has a life of days or weeks. Ours has a life of one conversation.

| Step of a real claim | What an insurer does | Today in the demo | Gap |
|---|---|---|---|
| First notice of loss | Takes the facts with a question list per cause | The agent asks the story, the date, the amount | No question list per cause |
| Coverage check | Is the policy in force? Is the cause covered? Which exclusion, which waiting period? | Policy period and limit only (R002, R004) | No covered causes, no exclusions |
| Triage | Simple or complex, which team, which priority | Three outcomes from a score and a limit | No queue, no priority, no authority level |
| Document request | Asks for what is missing, waits, reminds | The agent asks once, then R001 | The claim cannot wait for a document |
| Assessment | Reads the file, values the loss, may send an expert | Fields and signals | No valuation, no expert |
| Reserve | Books the expected cost on day 1, updates it | Nothing | Missing |
| Decision | A handler accepts, accepts in part or refuses, with a reason | Nothing after the routing | The handler has no screen and no action |
| Settlement | Amount = covered loss − deductible, within the sub-limits | Nothing | Missing |
| Payment | Checks the payee, pays | Nothing (step 10 of `docs/PRODUCT.md` is not built) | Missing |
| Recovery | Asks a liable third party to pay back | Nothing | Missing |
| Fraud investigation | A special unit opens a case, makes a plan, collects evidence, concludes | A report, then nothing | No case, no history, no links between claims |
| Closing and audit | Who did what, when, and why | Pipeline events of the agent only | No actions of persons |

## 3. Principles that stay

- A model finds facts. Code computes. A person decides (non-negotiables 1 to 5).
- The system never refuses a claim. A handler can refuse, with a written reason, and only on his screen.
- The customer never sees a score, a flag or the word "fraud". A question to the customer that comes from a signal is neutral ("Can you send the original invoice?").
- Every number that the handler sees points to a document page or to a policy clause.
- `local` mode gives the same result as the other modes, with no key.

## 4. Work packages

Order = the order to build. Size: S = one pull request, M = two or three, L = a PRD and its tickets.

### B1. Claim lifecycle (M) — first, the others need it

**Practice.** A claim has a status that a person can read: opened, waiting for the customer, in assessment, with a handler, in investigation, decided, paid, closed.

**Build.**
- A status model with the allowed moves, in code. `Claim.status` is a free string today.
- A claim that waits. The customer comes back later, in the same claim, and uploads the missing document. The checks and the routing run again. Today a missing document gives R001 and the flow ends.
- One event per status move, with the actor (`agent`, `handler`, `system`).
- `GET /claims`: the list of claims. It does not exist.

**Done when.** Kit story `kit-lukas-household` (no photo) waits, the customer adds the photo in a second visit, and the claim gets a new outcome.

### B2. Policy terms and settlement proposal (L)

**Practice.** The handler does not pay the claimed amount. He pays the covered loss.

**Build.**
- Policy terms in `data/mock/policies.json`: covered causes, exclusions, deductible, sub-limits per category, waiting period. Today a policy has a period and one limit.
- A settlement calculation in code, per workflow:
  - Property: line by line, covered or not, depreciation by age of the item, deductible, sub-limit (for example valuables).
  - Health: covered treatments, a share for the customer, the yearly limit less what was paid before.
  - Travel: trip price − refund − non-refundable parts that the policy excludes.
- The result is a **proposal**: each line has an amount, a reason, a policy clause and a source page. A person approves it (B3).
- A mock policy wording per line of business (a short PDF of general conditions) so a clause has a page to point to.

**Uses from Nikola.** Line items with their source page (#17, #18). The accuracy on amounts (#19) says how much we can trust a proposal without review.

**Done when.** For each of the 10 kit stories the proposal is right to the cent against a hand calculation stored next to `index.json`.

### B3. Handler workbench (L)

**Practice.** The handler works from a queue. He opens a case, reads a summary, checks, decides.

**Build.**
- A third page: the queue. Columns: age, line of business, amount, outcome, priority, status.
- The case page: summary in five lines, the fields with their source page, the signals, the settlement proposal, the history of actions.
- Actions: approve the proposal, change an amount (with a reason), ask the customer for a document or an answer (it becomes a neutral agent message, and the claim waits, B1), send to investigation, refuse (reason required, a person only).
- Authority levels: a handler approves up to a limit; above it a second person approves (four eyes). Two mock handler accounts are enough.
- The audit trail: every action is an event with the person and the reason.

**Boundary with #28.** Issue #28 (Nikola, M5) is "confirm or correct each field next to its source page". That is one part of the case page. We build the queue, the page and the actions, and leave a place for his part. To decide: section 8, question 3.

**Done when.** A second person plays the handler on claim file 2: opens the queue, approves the proposal, and the customer panel shows the result.

### B4. Fraud practice: history, links, investigation case (L)

**Practice.** Most fraud is not visible in one document. It is visible in the history of the customer, in the links between claims, and in the story. And a flag is a start, not an end: an investigator opens a case and concludes.

**Build.**
- **History.** Earlier claims for the mock customers and providers. New rule signals: many claims in 12 months, a claim soon after a rise of the cover, the same loss claimed on two policies, a provider with many flagged claims.
- **Links.** The same bank account, phone, address or provider in claims of different customers. A small table of links is enough. A graph view on the case page.
- **Signals per line of business.** Today the 12 signals are the same for the three workflows. Add the usual indicators per line: for example a theft with no sign of entry and only new valuables, a treatment abroad with a round total, a trip booked after the illness began.
- **Story against documents.** When the story and a document do not fit, the agent asks one neutral question and the answer goes in the file. It never says what it found.
- **Investigation case.** For an `investigation`: a plan made from the signals (call the provider, ask for the original, send an expert), tasks to tick, an evidence log, and a conclusion by the investigator: confirmed, not confirmed, cleared.
- **Feedback.** The conclusions tune the points. `docs/FRAUD.md` says "the points are a first guess". Measure the false alarms on the clean stories and the misses on the seeded ones.

**Uses from Nikola.** Tampering level 2 (#29) and duplicates (#30) arrive as signals: we give them points and a place in the report, we do not build them. The LLM report (#26) writes from our signals. The consistency prompts (#27) give the false alarm rate that the tuning needs. The rail on documents (#62) gives one more signal: "this document held text written for the model".

**Done when.** A new kit story with two customers and one shared bank account goes to `investigation` because of the link, with no tampered document. An investigator closes the case as "cleared" and the claim goes back to the handler queue.

### B5. First notice of loss: a better interview (M)

**Practice.** A call centre uses a question list per cause. The quality of the first notice decides the rest.

**Build.**
- A question list per workflow and cause, as data next to the workflow. The agent asks what is missing, in a natural order, and does not ask twice.
- The agent says which documents it needs and why, in plain words, and what happens next and when.
- A short summary that the customer confirms before the checks run.
- Care for a customer in distress (a burglary, an injury): tone rules in the prompt, and a way to a person at any time.

**Uses from Nikola.** The input rail (#62) runs before this interview. We do not write a second filter.

**Done when.** The measure script of `docs/EVALUATION.md` plays the 10 stories with facts given in a random order, and the saved claim is right in every run.

### B6. Customer follow-up (S)

**Build.** A status line in the customer panel in customer words ("We wait for your photo", "A specialist reviews your case", "Paid on …"). The list of open requests. The final letter: the amount, and the reason of each deduction in plain words. Non-negotiable 2 stays: the same words for `handler_review` and `investigation`.

**Done when.** `test_customer_view.py` covers every new status and still finds no forbidden word.

### B7. Payment, reserve, export (M)

**Build.** A reserve set at the first notice and updated at each step. A payee check before payment (the account belongs to the insured, or to the repair company on the invoice). A mock payment. The export of step 10 of `docs/PRODUCT.md`: one JSON file per closed claim for the mock claims system.

**Done when.** Claim file 1 ends as "paid" with an export file, with no person in the loop, and the file validates against a schema.

### B8. Experts and third parties (M, later)

**Build.** An expert request for a large property claim (a mock report comes back as one more document, read by the reader of Nikola). A medical adviser for health. Recovery: when a third party is liable (the neighbour's pipe), a recovery task opens after the payment.

### B9. Business numbers page (S, after #19)

**Build.** Nikola measures the reading (#19). We measure the business: share of straight-through claims, time from first notice to decision, handler minutes per claim (estimated), false alarms, misses, amount proposed against amount claimed. One page next to his metrics page, same style.

### B10. Demo and operations (S each)

- Dry run on the demo instance (#35). Needs the date of the event (O14).
- Several visitors at once: sandboxes in parallel and the rate limit of the key (O15, measured by #18). Not tested.
- Stop and start of the VM. Not tested.
- Confirm `docs/WORKFLOWS.md` (O12). B2 and B4 depend on it.
- Delete or keep `docs/FIRST-PROMPT.md`.

## 5. How we use the components of Nikola

| Component (issue) | What it gives | Where the base uses it |
|---|---|---|
| Schemas per document type (#17) | Typed fields and line items | B2 calculation, B3 case page |
| Omni reader (#18) | Real reading in place of the fixture | Everything, with no code change (the contract) |
| Evaluation (#19) | Accuracy per field | The level of confidence under which a field goes to a handler (B3); the model for B9 |
| Tuning (#20), classification (#21), hard cases (#22) | Better reading, known limits | The routing rule "a field has low confidence" becomes a real number |
| Rails (#62) | Blocks, with a reason | A block sends the claim to the handler queue (B3) and adds a signal (B4) |
| Specialised chain (#24) | Tables read right | B2 line by line |
| Damage photo and C004 (#25) | Photo against story | B4 story against documents, B8 expert request |
| LLM fraud report (#26) | A better text | B4 case page, with the plan of the investigator |
| Consistency prompts (#27) | False alarm rate | B4 tuning |
| Field confirm and correct (#28) | A corrected field | B3; a correction starts B2 again |
| Tampering level 2 (#29), duplicates (#30) | New signals | B4 points, links and report |

What we must give Nikola in return, early: the list of new fields and document types that B2, B4 and B8 need (bank account on an invoice, purchase date of an item, expert report). Put it in his issues #17 and #22 before he closes them.

## 6. Order and timing

| Phase | Packages | Can start | Waits for |
|---|---|---|---|
| 1 | B1, then B3 (queue and case page, approve only) | Now | Nothing |
| 2 | B2, B6 | After B1 | O12 confirmed |
| 3 | B4 (history, links, case), B5 | After B3 | New mock stories; #62 for the rail signal |
| 4 | B7, B9 | After B2 | #19 for B9 |
| 5 | B8, B10 dry run | Before the event | O14 |

Each package starts with `/grill-me` and `/write-prd`, then tickets with the label `afk` or `human`. No ticket carries the label `intern`.

## 7. New mock data that we must write

- Policy terms and one policy wording PDF per line of business (B2).
- Claim history for the three customers, and two or three more customers for the links (B4).
- Bank accounts and provider records (B4, B7). Invented, with a format that cannot be a real account.
- Four to six new kit stories: a partial payment, a claim that waits for a document, a link between two customers, a refusal by a handler, an expert case, a recovery case.
- The hand calculation of each story (B2).

The generator is `scripts/mockdocs/kit.py`. Non-negotiable 7 stays: mock data only.

## 8. Decisions for Paul

1. **Is the practice in sections 2 and 4 right?** In particular the settlement rules of B2 and the indicators of B4. This is O12, made larger.
2. **A fourth line of business: motor?** It is the best-known claim for a public (accident report, garage quote, photos, a liable third party). It costs one workflow, new document types for Nikola and new mock documents. Proposal: after phase 2, only if time is left.
3. **Who builds the case page of #28?** Proposal: the base builds the workbench (B3), and #28 stays with Nikola as the field correction inside it.
4. **How far does the demo go: to the decision, or to the payment?** Proposal: to the payment for the clean story, to the decision for the two others.
5. **Language of the customer panel.** English only today. French or German changes the prompts, the mock documents and the work of Nikola.
6. **The date of the event (O14).** It sets how many phases fit.

## 9. What we do not build

A real payment, a real claims system, real accounts, a pricing or underwriting step, a model that decides an amount or a refusal, a second document reader, a workflow engine other than plain Python (D20).
