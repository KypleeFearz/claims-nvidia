# Architecture

Date: 2026-11-12, checked against the code on 2026-11-13. Status: **decided for version 1** (grill session). M0 to M3 are built; the document reading pipeline (M4) is not. The decisions and the open points are in `docs/DECISIONS.md`.

The rule of the model, taken from NemoForge: **a workflow decides, a tool acts, an agent reasons inside a sandbox.**

## 1. Components

```
 CUSTOMER PANEL (chat, upload)            PIPELINE PANEL (events, data, fraud report)
        │ messages, files                          ▲ event stream
        ▼                                          │
 Backend (FastAPI)
   claims API · customer view · handler view · event stream · mock policy DB · mock claims system
        │
        ├─ Agent runner ───────► OpenShell sandbox (deny by default)
        │                          claim agent (LangGraph graph, section 1a)
        │                          ├─ model ──► provider ──► agent LLM (NIM)
        │                          └─ tools ──► L7 proxy ──► Tool gateway (MCP)
        │
        └─ Tool gateway (trusted code, outside the sandbox)
             ├─ get_customer_policies      mock policy DB
             ├─ get_claim_summary          the claim as the agent may see it
             ├─ update_claim               save the story, the date, the amount
             ├─ required_documents         documents the workflow asks for, with what is present and what is missing
             ├─ classify_document          fixture reader (the VLM is M4)
             ├─ extract_document           fixture reader → JSON with sources (the VLM is M4)
             ├─ run_checks                 rule engine (deterministic)
             ├─ check_tampering            file forensics (deterministic)
             ├─ check_consistency          deterministic (a VLM check, C004, is M5)
             ├─ assess_and_route           score → outcome (deterministic)
             └─ write_fraud_report         template, from the signals only (an LLM is M5)
```

- The **agent** is a LangGraph graph (decision D36). The model talks to the customer and, in two nodes, calls the tools of that node. The graph holds the order of the steps. The agent holds no rule and no score. It cannot change an outcome.
- The **tool gateway** is the only door out of the sandbox, apart from the model. It writes one event per call. The pipeline panel is a view of these events.
- **Scores, rules and routing are deterministic code**, not model output. A model finds facts (fields, descriptions, mismatches). Code decides. This makes the result repeatable and testable.

### 1a. The agent graph

The claim flow is a graph of five nodes (`backend/app/agent/graph.py`). The order of the steps is code, not a prompt.

```
 intake ──► documents ──► checks ──► route ──► answer ──► end
 (model)    (model)       (code)     (code)    (code)
```

| Node | Does | Tools |
|---|---|---|
| `intake` | Model. Asks for the story, the date and the amount, and saves them. | `get_claim_summary`, `update_claim` |
| `documents` | Model. Asks for the required documents and reads each upload. | `required_documents`, `classify_document`, `extract_document`, `get_claim_summary`, `update_claim`, `save_answer`, `customer_is_done` |
| `checks` | Code. Reads any document the model left unread, then `run_checks`, `check_tampering`, `check_consistency`. | none for the model |
| `route` | Code. `assess_and_route`. For `investigation` it writes the fraud report. | none for the model |
| `answer` | Code. Sends the `customer_message` word for word, closes the claim. | none for the model |

- **Edges.** `intake` goes to `documents` when the three facts are saved, in the same turn. `documents` goes to `checks` when the customer is done. After that, no model runs. A node cannot be skipped.
- **Done.** Code decides it for the Done button and the usual phrases. For free text the model calls `customer_is_done`. This tool is not a gateway tool. It sets a flag in the graph state and changes nothing else.
- **State.** The graph state holds the conversation (customer and agent messages only), the step that is due, and the ids of the documents that were read. The tool calls of a turn stay inside the node. The state is saved after each node in a SQLite checkpoint (`CLAIMS_CHECKPOINTS`). The thread id is the claim id.
- **Turns.** One customer message or one upload is one `invoke`. A turn ends after one message for the customer. If a code node fails, the customer gets the fallback message. The step is in the checkpoint, so the next message resumes at that node.
- **Independence.** `build_graph(model, call_tool, checkpointer, specs, emit)` imports nothing from the store, the gateway or FastAPI (a test checks it). `call_tool(name, inputs)` is the only way to the tools. In the backend it calls the gateway for one claim. In the sandbox it calls the MCP tools. The same file runs in both places.
- **Events.** A node writes one `workflow` event when it starts (stage = node name). The pipeline panel draws the graph strip from these events. The scripted agent of `local` mode writes the same events. Each model call writes one `llm` event.
- **Second line.** The trusted code keeps the rules of issue #12 (decision D40). `app/tools/guard.py` refuses `assess_and_route` until the story is saved, every document is read and the three checks ran; the customer gets the routing text word for word; every message of the model passes the output filter.

## 2. Document intelligence

| Step | Model | Notes |
|---|---|---|
| OCR | Nemotron OCR v2 | Reads scans and phone photos. Check which OCR version the NIM serves (see reading list). |
| Layout | Page-elements model | Finds titles, paragraphs, tables, charts. |
| Tables | Table-structure model | Rebuilds tables row by row. Needed for invoices and quotes. |
| Complex pages | Nemotron Parse | Whole page in reading order. Standalone NIM with a document API. |
| Understanding | Small VLM: Nemotron Nano 12B v2 VL or Nemotron 3 Nano Omni | Looks at the page image and the extracted text together. Fills the claim schema in JSON. Describes damage photos. |
| Benchmark | Nemotron 3.5 Super VL | Tested against the small VLM on hard documents. |

**Version 1 uses one model: Nemotron 3 Nano Omni alone** (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`), on the hosted NVIDIA endpoint: page image in, JSON with structured output out. Nemotron Nano 12B v2 VL is out (no hosted endpoint on 2026-11-12). The other rows of the table come later, where the accuracy numbers say they help. Measure each addition.

### The contract of `extract_document`

This contract is the boundary between the base and the intern's pipeline (decision D24).

- In: one stored `Document` (file, pages) and the workflow.
- Out: the document type, a list of `ExtractedField` (each with its source: document, page, snippet or box) and the line items.
- A reader is one class with this contract. The setting `CLAIMS_READER` picks it: `fixture` (the base) or `omni` (the intern).
- The **fixture reader** finds the `expected.json` of the uploaded file by the SHA-256 of its bytes and returns its content. An unknown file gives the type `unknown` and no field. It calls no model.

Checks, score, routing and the UI read only the output of this contract. They do not change when the reader changes.

## 3. Modes

| Mode | Agent | Document reading | Sandbox | Use |
|---|---|---|---|---|
| `local` | Scripted state machine | Fixture reader | None | Development, CI, tests. No GPU, no key. |
| `nim` | The LangGraph graph, in the backend process. Model: `ChatOpenAI` on the hosted NVIDIA endpoint. | Fixture reader (the Omni reader is M4) | None | Work on the agent and on extraction quality. |
| `sandbox` | The same graph, in OpenShell. The tools are MCP tools. | Same as `nim` | Enforced | The demo, on the Brev VM. |

The three modes share the tool gateway, the schemas, the rules and the UI. Only the agent and the model clients change. `sandbox` mode runs the same `build_graph`: the spike of issue #13 proved the graph, the model key swap and the MCP tools in OpenShell. Issue #14 built it (section 7). `local` mode imports no LangChain and no LangGraph: the packages load only in `nim` mode. `local` mode must give the right outcome for the three mock claim files.

## 4. Data model (Pydantic)

- `Customer`, `Policy` (number, line of business, start, end, coverage limit).
- `Claim`: id, customer, policy, type, description, incident date, claimed amount, documents, signals, score, outcome, fraud report, status.
- `ClaimStatus` (issues #72 and #84): `open`, `waiting_customer`, `validated`, `in_review`, `in_investigation`, `approved`, `refused`, `waiting_approval`, `paid`. See "Claim lifecycle" below.
- `Decision`: action, handler, reason, amount (for an approval), time. A claim keeps the list of its decisions.
- `Payment` (issue #94): amount, account, reference, time. A claim also has a `reserve`: the expected cost.
- `ExpertRequest` and `Recovery` (issue #102): see "Experts and third parties" below.
- `Investigation` (issue #92): tasks (text, the signal codes it answers, done, by, note), notes of the evidence log, and the conclusion (confirmed or not confirmed, by, text).
- `Policy` terms (issue #86): deductible, coinsurance (the share the customer pays), covered causes. The mock policies are read from `data/mock/policies.json` at every start of the backend.
- `Proposal`: the name of the general conditions, the lines (label, amount, reason, article, sources), the amount to pay, and why a person must decide. Computed by `backend/app/settlement.py` (pure code) inside `assess_and_route`, saved with the outcome, shown in the pipeline panel and on the case page. A handler approves the proposed amount, or another amount with a written reason; the customer reads the approved amount.
- `Document`: id, file, document type, pages, fields, line items, file metadata.
- `ExtractedField`: name, value, confidence, `source` (document id, page, text snippet or box). The source is required.
- `Signal`: code, category (`rule`, `tampering`, `consistency`), severity, points, title, detail, evidence (list of sources).
- `PipelineEvent`: sequence, time, actor (`agent`, `tool`, `workflow`, `sandbox`), stage, title, inputs, outputs, model, duration.
- One typed claim schema per workflow, in `docs/WORKFLOWS.md`.

## 5. Views

- **Customer follow-up (issue #88).** The customer view also gives a status line in the words of a customer, the open requests (the missing documents and the last request of a handler, while the claim waits) and, once the claim is validated or approved, the settlement letter: the amount and the lines of the proposal with their reason and article. When a handler approved another amount than the proposal, the letter gives the amount and says that the reasons come by post. The reason of a handler is never in the view. The three parts depend on the customer status, so they are the same for a review and for an investigation. The customer panel shows them above the chat.
- **A customer finds his claims again (issue #107).** `GET /customers/{id}/claims` gives the claims of one customer that have a policy, newest first, as customer views. After the choice of the account the customer panel shows the list "Your claims" (with the status line of each) and "New claim"; with no earlier claim a new one opens at once. The page keeps the current claim in `localStorage`, so a reload shows it again; a claim that does not exist any more is forgotten.
- **Customer view** of a claim: messages and status only. Built by one function that drops signals, score and report. It gives the same exact message for `handler_review` and `investigation`. Non-negotiable 2 tests this function.
- **Pipeline / handler view**: everything.

### 5a. Routes

`GET /health`, `GET /customers`, `POST /claims`, `GET /claims` (the list of claims, for a handler: id, customer, policy, type, amount, status, outcome, score, reasons, opening time), `GET /customers/{id}/claims` (the claims of a customer, as customer views), `GET /handlers`, `GET /handlers/authority`, `POST /claims/{id}/decisions` (a decision of a handler), `GET /claims/{id}/export` (the record of a paid claim for the mock claims system), `GET /claims/{id}/history` (for the handler: the contact data of the customer, his earlier claims and the linked persons), `GET /claims/{id}` (the full claim, for the pipeline panel), `GET /claims/{id}/customer` (the customer view), `GET /claims/{id}/policies`, `PUT /claims/{id}/policy`, `GET` and `POST /claims/{id}/messages`, `POST /claims/{id}/documents`, `GET /claims/{id}/documents/{doc}/file`, `GET /claims/{id}/events` (the event stream), `GET /claims/{id}/sandbox`, `POST /claims/{id}/sandbox/probe`, `GET /numbers` and `POST /numbers/kit` (the business numbers, `backend/app/numbers.py`: the claims of the demo, and the kit played through the active reader), `POST /bench/read` (the reader bench: one kit document read by the active reader and compared with its `expected.json`; it calls the `Reader` contract only and stores nothing; `backend/app/bench.py`), `GET /evaluation/latest` (the last report of the evaluation harness, for the page "Metrics": it reads one file and never runs a reader; `backend/app/metrics.py`), and under `/demo`: `GET /kit`, `GET /scenarios`, `GET /kit/{folder}/{file}`, `POST /reset`. The MCP server of the sandbox is mounted at `/mcp/<token>`. The code is `backend/app/main.py` and `backend/app/demo.py`.

### 5b. Claim lifecycle (issue #72)

The status of a claim says where it is in its life. `backend/app/lifecycle.py` holds the allowed moves, and the store is the one place that changes a status (`save_routing`, `set_status`, `reopen`). `update_claim` refuses a status. Each move writes one event: actor `system`, stage `status`, with `from`, `to`, `by` (`system`, `customer`) and why.

```
open ──routing──► validated | in_review | in_investigation
validated ──mock payment──► paid          approved ──mock payment──► paid   (issue #94)
open ──routing, a required document is missing (R001)──► waiting_customer
waiting_customer ──the customer uploads a document──► open

a handler, on a claim that is in_review or waiting_customer (issue #84):
  approve ──► approved          refuse (reason) ──► refused
  request (text for the customer) ──► waiting_customer
  investigate (reason) ──► in_investigation

four eyes (issue #105): an approval above the limit of the handler
  approve ──► waiting_approval (a proposal: nothing is paid)
  a second handler: approve ──► approved ──► paid     return (reason) ──► in_review

four eyes (issue #111): every refusal
  refuse (internal reason + reason for the customer) ──► waiting_approval
  a second handler: refuse ──► refused     return (reason) ──► in_review

an investigator, on a claim that is in_investigation (issue #92):
  clear (not confirmed, text) ──► in_review     confirm (fraud confirmed, text) ──► in_review
  request (text for the customer) ──► waiting_customer
```

- **The claim waits.** When R001 is in the signals, the status after the routing is `waiting_customer`, whatever the outcome. The outcome and the report stay on the claim for the pipeline panel. The customer message names the missing documents and then gives the usual text. The wait depends only on the documents, so it tells nothing about the outcome (non-negotiable 2).
- **The claim opens again.** An upload to a claim that waits calls `reopen`: status `open`, chat step `ask_documents`, no outcome, no score, no report (the event keeps the old outcome). The agent reads the document as in a first visit. When the customer says "done", the three checks and the routing run again. An upload to a claim that does not wait is still a 409.
- **The three modes.** `local`: the scripted agent reads its step from the claim, so it needs no change. `nim`: the checkpoint of the graph still says `closed`, so `graph_agent.py` sets the step to `documents` when the claim is open. `sandbox`: the first sandbox was deleted at the routing; the turn starts a new one, and the checks node reads every document again. The graph file did not change, so the sandbox image is the same.
- **The decisions of a handler (issue #84).** `backend/app/handler.py` is the only way to `approved` and `refused`: a known handler (two mock names, no login), and a written reason to refuse, to investigate and to clear. `POST /claims/{id}/decisions` `{action, by, reason}`; `GET /handlers`. The decision goes on the claim (`decisions`) and is one event with the actor `handler`. The outcome of the routing does not change: it stays what the system said. A test checks that no other module writes these two statuses.
- **What the customer reads after a decision.** Approve and refuse: a fixed text in the chat and in the customer view (`approved`, `decided`); never the reason of the handler. A request: the text of the handler, which must pass the word filter of the agent (a text with "fraud", "score" or "investigation" is a 422). Investigate and clear: nothing changes for the customer. An upload after a request opens the claim again, as for a missing document.
- **The page.** "Handler desk" (`frontend/src/HandlerDesk.tsx`): the queue (investigations first, then reviews, then the claims that wait; it asks again every 5 seconds) and the case page (facts, decision form, decisions, fraud report, outcome, signals, fields with their source, conversation). A closed chat of the customer asks again for its messages every 5 seconds, so the customer sees a decision.
- **Authority and the second approval (issue #105).** `AUTHORITY` in `backend/app/handler.py`: Sofia Brunner approves alone up to CHF 20,000, David Frei up to CHF 2,500 (`GET /handlers/authority`). An approval above the limit of the handler is saved as the decision `propose` and the claim goes to `waiting_approval`: nothing is paid, and the customer still reads a review. A second handler, who is another person and whose limit covers the amount, approves the amount of the proposal as it is, or returns the claim to review with a reason (`return`). No other action on a claim that waits for an approval.
- **A refusal needs two handlers and a reason for the customer (issue #111).** A refusal is saved as the decision `propose_refusal` and the claim goes to `waiting_approval`; the customer sees no change. The handler writes two texts: the internal reason, and `customer_reason`, which must pass the word filter of the agent. A second handler, another person, confirms with `refuse` (the reasons of the proposal are kept) or returns the claim. After the confirmation the customer view has a letter of the kind `refusal`: that the claim cannot be paid, the reason for the customer, and how to ask for a second look. The internal reason is never in the customer view.
- **The investigation case (issue #92).** `backend/app/investigation.py`. A claim that goes to `in_investigation` (by the routing or by a handler) gets a plan: one task per kind of signal (`TASKS`), strongest first; a claim with no signal gets one task with the reason of the handler. `POST /claims/{id}/investigation/tasks/{task}` `{by, done, note}` ticks a task; `POST /claims/{id}/investigation/notes` `{by, text}` adds a note to the evidence log. Each change is one event with the actor `handler` and the stage `investigation`. The conclusion is a decision: `clear` (not confirmed) or `confirm` (fraud confirmed), with a text, refused while a task is open. Both send the claim back to `in_review`: the investigator reports, the handler decides. A claim in investigation cannot be approved or refused. The conclusion is saved on the case in the same write as the status. Nothing of the case reaches the customer.
- **Reserve, payment and export (issue #94).** `backend/app/payment.py`.
  - *Reserve*: the claimed amount when the customer gives it (`update_claim`), the proposal at the routing, the approved amount at the approval, zero after a refusal or a payment. Each change is one event (`system`, `reserve`).
  - *Payee check*: the payout account must be known (`data/mock/history.json`) and must belong to the customer only. When it is not, the routing adds a reason "Payment: ..." and the claim goes to a handler.
  - *Payment*: a claim that is `validated` or `approved` with an amount above zero moves to `paid` in the same call, with a `Payment` record. No real money and no real account. An amount of zero pays nothing and the status stays.
  - *Export*: one JSON file per paid claim in `CLAIMS_EXPORTS` (default `.dev-data/exports`), built from the model `ExportRecord`, which is its schema: the claim, the amounts, who decided, the lines of the proposal and every field with its document and page. `GET /claims/{id}/export` gives the same record. The demo reset deletes the files.
  - A paid claim cannot be routed or decided again.
- **Later.** Authority levels and a second approval, the status `closed`, a payment to a repair company, the conclusions as input to tune the points.

### 5e. Experts and third parties (issue #102)

`backend/app/third_parties.py`. Two objects on a claim, next to its status: they do not change it.

- **Expert request** (`experts`): a handler asks one question to a loss adjuster (property), a medical adviser (health) or an expert (other). `POST /claims/{id}/experts` `{by, question}`. While a request has no report, the claim cannot be approved or refused. The report: `POST /claims/{id}/experts/{request}/report` `{by, expert, finding, amount}`; in the demo a handler types it (a mock). The handler then decides; the page puts the assessed amount in the decision form. The routing sends a claimed loss above `expert_above` of the workflow (property 5,000, health 3,000) to a handler with the reason "needs an expert".
- **Recovery** (`recovery`): another person is liable. `POST /claims/{id}/recovery` `{by, party, reason}`: `planned` until the claim is paid, then `open` for the paid amount (the payment activates it). `POST /claims/{id}/recovery/result` `{by, result, amount, reason}`: `recovered` with an amount, or `abandoned` with a reason. One recovery per claim; none on a refused claim.
- Each change is one event (`expert` or `recovery`). Neither decides anything, and the customer reads nothing of either. The business numbers count the open expert requests and the recoveries.

### 5d. First notice of loss (issue #98)

- **Questions per cause.** Each workflow holds `questions`: for a cause, a list of `Question` (key, text, why the insurer asks). When the cause of a claim is saved, `update_claim` puts the questions of that cause on the claim (`notice`: key, question, why, answer). The claim summary gives `open_questions` (key and text, no reason) and `answers`.
- **The agent asks them one at a time**, after it asked for the documents. `local`: the scripted agent appends the first open question to its message, and takes the next message that is not "done" as the answer. `nim` and `sandbox`: step 7 of the prompt of the documents node, and the tool `save_answer` (the key, the answer). Each answer is one tool event. An answer does not make the checks stale.
- **They do not block.** "done" closes the claim with open questions; the handler sees "not answered" on the case page, section "First notice".
- **Why each document.** A required document has a `why`; `required_documents` returns it and the agent says it.
- **A way to a person.** A message of a customer that asks for a person (`wants_person` in `agent/phrases.py`, a narrow pattern) is answered by the backend, before any agent: the claim goes to `in_review` with no outcome, a fixed text, and in `sandbox` mode the sandbox is deleted. Only while the claim is `open`.
- **Tone.** Step 9 of the prompt of the documents node: one kind sentence first for a customer who is hurt or shocked, and how to ask for a person. Not in the shared prompt: there it made the intake node fail (issue #100, `docs/LESSONS.md`).

### 5c. Business numbers (issue #96)

`backend/app/numbers.py`, page "Business numbers".

- **The claims of the demo** (`GET /numbers`): claims by status, the share with the outcome `straight_through` among the routed claims, the median time from the opening to the end (the payment, or the refusal), the amounts claimed, proposed and paid, the open reserve, the conclusions of the investigations and the decisions of the handlers.
- **The kit** (`POST /numbers/kit`): each story of `data/mock/index.json` is played with no agent model: the facts of the story from the index, each document through the active reader (`classify_document`, `extract_document`), then `run_checks`, `check_tampering`, `check_consistency` and `assess_and_route`, through a gateway built by `build_gateway` on a store in memory. The handler queue does not see these claims and no export file is written. Each story is compared with its expected outcome and signals, and with the amount of `data/mock/settlements.json`. One run at a time (a second one is a 409). A reader that raises gives a row with the error.
- The kit run is right by construction with the fixture reader. With a real reader it shows what a reading error costs the business. The accuracy per field stays the evaluation harness of issue #19.

### 5f. Evaluation of the reader (issue #19)

`backend/app/docintel/evaluate.py`, page "Metrics", decision D81.

- **The command** `cd backend && uv run python -m app.docintel.evaluate --reader <name> [--runs N]` runs a new reader in each run on every document of the kit (the list of `/demo/kit`). It calls `classify` and `extract` through the `Reader` contract and times each call. It touches no claim, no store and no gateway.
- **The comparison** (`backend/app/docintel/compare.py`): the kind of each field comes from its schema of #17: amount, date, identifier or text. Each expected field is right, wrong or missing; the source page is checked apart.
- **The report** is one JSON file, `backend/.dev-data/evaluation/latest.json` (`CLAIMS_EVALUATION`), with a dated copy. It is not in git. `GET /evaluation/latest` serves it as it is. The page "Metrics" shows it: one table per document kind, one per document type, one line per document.
- Not the same as the reader bench (one document, one read, for the eye) and the business numbers (what a reading error costs). The rules and the measured numbers: `docs/EVALUATION.md`.

## 6. Workflows

A workflow is declarative data plus rules: required documents, the claim schema, the rule set, the straight-through limit. One engine runs the three. First version: plain Python, state in SQLite. No Temporal (decision D20).

## 7. Sandbox

Same pattern as NemoForge: OpenShell with a deny-by-default policy. The policy allows the tool gateway and the model provider, and nothing else. No folder of the host except a scratch folder. OpenShell injects the model key outside the sandbox. The pipeline panel shows the policy, the denials and a confinement test (issue #15, see "The pipeline panel" below).

`CLAIMS_MODE=sandbox` (issue #14, decisions D44 to D49) runs the graph of section 1a in one OpenShell sandbox per claim. OpenShell 0.1.5 with Colima. Nothing in the sandbox is trusted: the backend decides what the customer reads.

```
 Backend (127.0.0.1:BACKEND_PORT)                    OpenShell sandbox `claims-<claim id>`
   SandboxAgent ── sandbox exec, JSON on stdin ───►    python -m app.agent.sandbox_turn
     create (1st turn) · turn · delete (close, reset)    the graph (same graph.py as nim mode)
   MCP server  /mcp/<token>  ◄── MCP over HTTP ────     call_tool = MCP client, MCP_URL
     guarded_call → gateway.call → one tool event        model ──► provider claims-nim ──► NIM
```

- **One setup, once**: `infra/sandbox/setup.sh` builds the image `claims-agent:1` (Python slim, non-root, the pinned packages, the graph files of `backend/app`, no `curl`), imports the profile `claims-nim` and creates the provider `claims-nim` with the key from `NVIDIA_KEY` (in the environment of that one command). All names start with `claims-`. The backend stops at startup, naming this script, when `openshell`, the gateway, the provider or the image is missing.
- **MCP server** (`app/tools/mcp_server.py`): the tools of the gateway that the graph uses, on `/mcp/<token>`. The token fixes the claim. Every call goes through `guarded_call` and `gateway.call`, so rule 1 holds and one tool event is written per call. These tool events are the trusted ones.
- **Policy** (`infra/sandbox/policy.yaml`, made for each sandbox by `render_policy`): one network policy, the MCP endpoint of this claim (`host.openshell.internal`, the backend port, the path with the token), methods `initialize`, `notifications/initialized`, `tools/list`, and `tools/call` for the tools of `GRAPH_TOOLS`. The model goes through the provider profile (`POST /v1/chat/completions` on `integrate.api.nvidia.com`). Landlock `hard_requirement`; `/sandbox` writable; the rest read-only. Another host, another path of the backend, the MCP path of another claim, another tool and `GET /v1/models` are denied (`pytest -m sandbox` checks each).
- **A turn**: `openshell sandbox exec` with `{"text", "document_id"}` on stdin. The process runs the graph (SQLite checkpoint in `/sandbox/state`, thread id = claim id) and prints one JSON line: the reply and the node and model events. The backend stores the events as display-only (`"source": "sandbox"`), filters the reply, and, when the claim has an outcome, sends its own `customer_message` word for word.
- **Events**: `sandbox` actor: `created` (seconds, policy file, shortened path, how to read the denials), `turn` (seconds), `deleted`, `failed` (the reason). Time per turn: `docs/EVALUATION.md`.
- **Files and log**: the policy file of each sandbox is in `CLAIMS_SANDBOX_DIR` (default `.dev-data/sandboxes/<name>.policy.yaml`, mode 0600, it holds the token; its path is in the `created` event). The denials are `openshell logs <name> --source sandbox`, the lines with `DENIED` (the `created` event holds the command). Those OpenShell lines show the whole path, so the token: the backend cuts it before it stores or shows a line.

### The pipeline panel (issue #15, decisions D50 to D54)

The pipeline panel has a Sandbox section right under the graph strip. It shows only when the claim has a sandbox. The sidebar shows the mode (`local`, `nim`, `sandbox`) and the agent model. `GET /health` gives both (`mode`, `agent_model`, null in `local`).

- **Policy**: `GET /claims/{id}/sandbox` (pipeline view only, 404 when the claim has no sandbox). It gives the name, the state (`running`, `deleted`), the time `sandbox create` took, and the policy of this claim as data: the model endpoint (from the provider profile), the tool gateway (host, port, MCP path, methods, tools), the read-only and read-write folders, the user id and the Landlock setting. The token is cut to its first 6 characters. The full token is in no answer, no event and no log.
- **Denials**: after each turn, and after the probe, the backend reads the sandbox log (`OpenShell.log_lines`) and stores each new `DENIED` line as one event: actor `sandbox`, stage `denied`, title `Denied: <what>`, the reason of the line in the outputs. The token is cut from the line first. The panel shows them as a table (time, what, reason).
- **Confinement probe**: `POST /claims/{id}/sandbox/probe` (409 when the sandbox is not running, in the per-claim lock) runs a fixed script in the sandbox. The script is a constant of the backend (`PROBE` in `app/agent/sandbox_panel.py`), sent on stdin to `python -`: nothing of the request reaches the command. It tries five things, each with a timeout of 8 s: an HTTPS request to `example.com`; `tools/call` on the MCP path of a made-up other claim; a tool the policy does not allow (`write_fraud_report`) on its own path; reading the model key (the script reports only if it starts like a real key, and the placeholder text); listing `/Users` and `/host`. The backend decides from the answers, not the sandbox: a try is "denied" only when its answer proves it (HTTP 403, the supervisor refusing the connection, no key, no folder). One `probe` event per try (`Probe: other host → denied`), then the denials it caused. A try that is not denied is said plainly, and the endpoint returns `"confined": false`.
- **Order of events**: the node and model events of the sandbox arrive in one batch after the turn. Each one carries `at`, the time it happened by the clock of the sandbox. The backend stores that as the display time, kept inside the turn (`display_time`), and the panel sorts by time. The tool events written by the gateway keep their own time and stay the trusted events.

NemoForge runs OpenShell 0.1.5 with provider profiles (`inference.local` was removed in 0.1.x). This setup stays on 0.1.5. Later, test OpenShell 0.1.8: its gateway refuses to start without authentication, and static credentials go only in the `Authorization` header (table in `docs/DECISIONS.md`, spike section). NeMo Guardrails is issue #62 (M4, decision D60). NemoForge notes: `../nemoforge-plateforme/docs/DECISIONS.md` and `docs/LESSONS.md`.

## 8. Stack

- Backend: Python, FastAPI, uv, Pydantic, SQLite, pytest, mypy, ruff.
- Frontend: React, Vite, TypeScript.
- Run: Docker Compose on one Brev VM. No GPU in version 1: the models are hosted NVIDIA endpoints. Laptop for `local` mode.
- Model calls: OpenAI-compatible chat completions for the VLM and the agent LLM, on `integrate.api.nvidia.com`. Later: dedicated APIs for OCR, page elements, table structure and Parse.
- UI: same look as NemoForge.

## 9. Repository layout

```
backend/app/
  main.py       FastAPI app and the claims routes (section 5a)
  demo.py       demo routes: kit, scenarios, reset
  models.py     the Pydantic models of section 4
  views.py      the customer view
  stream.py     the event stream (server-sent events)
  agent/        graph (LangGraph, no store import), graph_agent (backend runner), scripted agent, handover, phrases (shared words and rules), model, sandbox (runner), sandbox_turn (the turn process in the image), sandbox_image (image build context), sandbox_panel (policy, denials, probe)
  tools/        tool gateway, one module per group of tools, guard (rule 1 of the routing), mcp_server
  docintel/     reader contract, fixture reader (base); schemas, the Omni reader, the evaluation harness (evaluate, compare): the work of the intern
  workflows/    engine, property.py, health.py, travel.py
  checks/       rules, tampering, consistency
  fraud/        routing (score and outcome), report (template)
  store/        SQLite store, seeded from data/mock/policies.json
backend/tests/
frontend/src/   App, Demo, CustomerPanel, Chat, PipelinePanel, PipelineSections, GraphStrip, SandboxSection, Health, ClaimMap and mapModel (the Map page)
data/mock/      policies, claim folders, hard versions, index.json, expected results
infra/sandbox/  Dockerfile, policy template, provider profile, setup.sh
infra/deploy/   install.sh (the Brev VM), public.sh (the public door)
scripts/        mock document generator (mockdocs/), measure_agent.py
docs/
```
