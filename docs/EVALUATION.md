# Evaluation: which model drives the claim agent

Sections 1 to 3 measure the hand-written loop of issue #12 (removed by issue #47). The section "Again with the LangGraph agent" measures the graph. The script is the same.

Date: 2026-11-12. Issue #12, decision D17. Endpoint: `https://integrate.api.nvidia.com/v1` with the free developer key.

**Default: `nvidia/nemotron-3.5-lightning-30b-a3b`.** Both models get all 9 claim files right, but Lightning answers a turn twice as fast, uses 40% fewer tokens, and never needed the fallback message.

## How it was measured

`cd backend && uv run python ../scripts/measure_agent.py` (needs `NVIDIA_KEY` in `.env`).

- The backend runs in `nim` mode, in the process of the script (FastAPI `TestClient`, an empty database), with the fixture reader.
- Each of the three claim files is played 3 times with three different customer wordings (`backend/tests/live_claims.py`): all facts in one message, facts in the other order, and a story with no date and no amount, so the agent must ask. The player answers what the agent asks. It uploads the documents one by one, then says "no, that's everything" in its own words. At most two more "I am done" messages if the agent does not close.
- "Right outcome" means: the expected outcome (`straight_through`, `handler_review`, `investigation`) **and** the customer text word for word. "Right date and amount" means the claim holds the date and the amount of the claim file.
- A turn is one customer message or one upload, as the customer panel sees it (it includes every model call and tool call of the turn). The worst turns (30 to 60 s) are slow model calls; 60 s is the timeout of the HTTP client.
- Tool calls are the calls the model asked for. Tokens are prompt plus completion (reasoning included).
- Default settings of the models (reasoning as the endpoint gives it), `max_tokens` 2000.

## Result after the prompt change (final)

**`nvidia/nemotron-3.5-lightning-30b-a3b`**

| Claim file | Right outcome | Right date and amount | Model calls | Tool calls | Seconds per turn (median / worst) | Tokens | 429 |
|---|---|---|---|---|---|---|---|
| travel | 3/3 | 3/3 | 17.3 | 15.7 | 7.3 / 54.4 | 34,918 | 0 |
| property | 3/3 | 3/3 | 17.0 | 13.7 | 4.2 / 12.4 | 32,831 | 0 |
| health | 3/3 | 3/3 | 16.3 | 12.0 | 4.9 / 32.7 | 31,577 | 0 |
| All | **9/9** | 9/9 | 16.9 | 13.8 | 4.9 / 54.4 | 33,109 | 0 |

**`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`**

| Claim file | Right outcome | Right date and amount | Model calls | Tool calls | Seconds per turn (median / worst) | Tokens | 429 |
|---|---|---|---|---|---|---|---|
| travel | 3/3 | 3/3 | 23.0 | 14.3 | 9.9 / 33.2 | 57,654 | 0 |
| property | 3/3 | 3/3 | 28.3 | 20.3 | 17.2 / 55.7 | 73,394 | 0 |
| health | 3/3 | 3/3 | 16.3 | 11.7 | 8.8 / 42.2 | 36,799 | 0 |
| All | **9/9** | 9/9 | 22.6 | 15.4 | 10.5 / 55.7 | 55,949 | 0 |

Fallback messages ("Sorry, I could not answer just now"): Lightning 0 in 9 claims, Omni 5 in 9 claims. The claim still ended right every time, because the player writes again after a fallback. A real customer would see the fallback and have to write again.

## Before the prompt change (same player, same 9 claim files)

| Model | Right outcome | Model calls per claim | Seconds per turn (median / worst) | Tokens per claim | 429 | Fallback messages |
|---|---|---|---|---|---|---|
| Lightning | 9/9 | 21.1 | 5.7 / 60.0 | 41,520 | 2 | 1 (a 60 s timeout) |
| Omni | 9/9 | 24.0 | 8.5 / 62.7 | 53,929 | 0 | 3 |

### What went wrong, and what was changed (once)

A first run of the measure gave 6/9 for both models. All three failures per model were a bug of the player, not of the models: when the story had no date, the player sent the story again instead of answering the question about the date. The player was fixed (it now answers what the agent asks), and that run is not in the tables.

In the second run (the "before" table), Omni ended 3 turns with the fallback message. A trace of one of them (property file, the turn after "no, that's everything"):

1. In the turn of the last upload, Omni called `classify_document` but not `extract_document`, then wrote "all documents are received". The document stayed unread.
2. At "done" it ran the three checks and `assess_and_route`. The trusted code refused (rule 1: one document is not read), with an error that names the document.
3. Omni then classified and extracted **all** documents again, one call per reply, then ran the three checks again, one call per reply. It reached the limit of 12 model calls before `assess_and_route`.

The enforcement worked (the model could not route a claim with an unread document). The cost was the number of calls. Changes:

- Prompt: "read it before you answer: call classify_document and extract_document, both"; "you may make several tool calls in one reply"; "ask for the date in the customer's own words and convert it yourself" (the model was asking customers for `YYYY-MM-DD`).
- Enforcement: in a turn with an upload, the runner sends the model's answer back once if it has not read the uploaded document (`UNREAD` in `backend/app/agent/llm.py`, removed with the loop by issue #47).

After the change both models are still 9/9. Lightning went from 21.1 to 16.9 model calls per claim and from 1 fallback to 0. Omni went from 24.0 to 22.6 calls, but still ended 5 turns with the fallback, for other reasons: three times its message named a tool (`run_checks`, `classify_document`) and was refused by the output filter, and twice its message was cut off at `max_tokens` because the reasoning tokens used the room. It would need more `max_tokens` or `AGENT_THINKING=off`: not tested, because Lightning does not need it.

## Again with the LangGraph agent (issue #47)

Date: 2026-11-12. Same script, same player, same 9 claim files, 3 runs per file, `nvidia/nemotron-3.5-lightning-30b-a3b` only. The agent is now the graph of `docs/ARCHITECTURE.md` section 1a (`ChatOpenAI`, SQLite checkpointer). The model has no tool that checks or routes, and "done" is decided in code for the Done button and the usual phrases.

| Claim file | Right outcome | Right date and amount | Model calls | Tool calls | Seconds per turn (median / worst) | Tokens | 429 | Fallback messages |
|---|---|---|---|---|---|---|---|---|
| travel | 3/3 | 3/3 | 17.3 | 11.7 | 7.9 / 21.6 | 26,319 | 0 | 0 |
| property | 3/3 | 3/3 | 17.7 | 12.0 | 5.5 / 54.6 | 26,442 | 0 | 0 |
| health | 3/3 | 3/3 | 14.0 | 9.3 | 5.2 / 23.1 | 19,446 | 0 | 0 |
| All | **9/9** | 9/9 | 16.3 | 11.0 | 5.5 / 54.6 | 24,069 | 0 | 0 |

Against the hand-written loop of #12 (Lightning, same player):

| | #12 (loop) | #47 (graph) |
|---|---|---|
| Right outcomes | 9/9 | 9/9 |
| Model calls per claim | 16.9 | 16.3 |
| Tool calls per claim (asked by the model) | 13.8 | 11.0 |
| Seconds per turn (median / worst) | 4.9 / 54.4 | 5.5 / 54.6 |
| Tokens per claim | 33,109 | 24,069 (-27%) |
| Fallback messages | 0 in 9 | 0 in 9 |
| Refused tool calls | not counted | 0 in 9 |

What the numbers say:

- Same result, 27% fewer tokens: each node has a short prompt and 2 to 5 tools, and the conversation in the context holds no tool traffic.
- The model calls did not go down much (16.3 against 16.9). The model still needs one call per tool step and one for each message. In #12 the model asked for the checks and the routing in one reply. Now no model call is spent on them. The graph removes the chance to get the order wrong, and the 2.8 fewer tool calls per claim, but few model calls.
- The median turn is 0.6 s slower. The wifi was slow tonight and the endpoint was busy. The worst turn (54.6 s) is one slow model call, with no timeout and no retry. This is not an effect of the graph: the same worst value was in #12 (54.4 s).
- No tool call was refused (`refused` events): with 5 tools per node the model made no call outside its tools. The code paths that the rules of #12 guard (unread document, checks out of date) were not reached with a real model. They are tested with the fake model and by mutation (remove a rule, a test fails).
- "429" counts model calls that failed with a 429 after the retries of the client. The OpenAI client retries a 429 by itself and does not report it, so a retried 429 is not counted here. There was none that ended in a failure.
- The measured code is the commit before the last one of the ticket ("a claim with the three facts goes straight to documents"). That change does not touch these flows.

## Rate limit of the free key (O15, first measure)

The only 429 answers came from Lightning: 2 of them in one run, after 34 model calls in the 60 seconds before. The client retried after 1 s and the second try worked. About 40 requests per minute looks like the limit of the key, but the limit is not documented: measure again before the event. A claim file makes 17 to 23 model calls, so two visitors at the same time can reach it.

## Omni reader on claim file 1 (issue #18)

Date: 2026-11-17. Decision D80. `OmniReader` (`backend/app/docintel/omni.py`), model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at `https://integrate.api.nvidia.com/v1` (`VLM_MODEL`, `VLM_BASE_URL`), free developer key. One call to classify (page 1), then one call per page. Each page is the full page as a PNG at 150 DPI (1241 x 1754 px for A4). `response_format` `json_schema` (strict), the schema also in the prompt. Thinking off (`chat_template_kwargs.enable_thinking: false`), temperature 0.2, `max_tokens` 2048. Mock data only.

Commands (need `NVIDIA_KEY` in `.env` at the repository root):

- Request formats (spike): `cd backend && uv run python ../scripts/probe_omni.py format`
- Fields of claim file 1, and the claim through the API (local mode, `CLAIMS_READER=omni`): `cd backend && uv run pytest -m live tests/test_omni_live.py -k claim_file_1 -s`
- The reader contract on every mock file: `cd backend && uv run pytest -m live tests/test_omni_live.py -k contract`

### Request formats (spike, one call each, page 1 of the booking of claim file 1)

| Variant | HTTP | Seconds | Answer |
|---|---|---|---|
| Schema in the prompt only | 200 | 6.5 | valid JSON, valid values |
| `response_format` `json_schema` | 200 | 4.9 | valid JSON, valid values |
| `response_format` `json_object` | 200 | 5.1 | valid JSON, valid values |
| `nvext.guided_json` | 400 | | field not supported |
| Thinking on (schema in the prompt) | 200 | 41.8 | cut before the JSON: no valid answer |
| A JPEG phone photo (receipt) | 200 | 3.5 | valid JSON |

One call per variant: the seconds show no real difference between the first three. `json_schema` is chosen because the endpoint forces the form of the answer. The snippet that the model gives for `line_items` (the whole table) is not on the page; the reader does not use it (it confirms each line on the page text).

### Claim file 1: 3 runs, measured

| Document | Field | Result (same in the 3 runs) | Source stored |
|---|---|---|---|
| Booking | `booking_reference` | **missing** | none (refused, see below) |
| | `departure_date` | right | page 1, snippet checked on the text layer |
| | `trip_price` | right | page 1, snippet checked |
| | `traveller_name` | right | page 1, snippet checked |
| Cancellation | `booking_reference` | right | page 1, snippet checked |
| | `cancellation_date` | right | page 1, snippet checked |
| | `refund_received` | right | page 1, snippet checked |
| Medical certificate | `patient_name` | right | page 1, snippet checked |
| | `cause` | **wrong** | page 1, snippet checked |
| | `unfit_from` | right | page 1, snippet checked |

- **Fields: 8 right, 1 wrong, 1 missing, out of 10**, in each of the 3 runs.
- **Line items: 6 of 6 right** (3 of the booking, 3 of the cancellation; 0 expected and 0 read on the certificate). Seen in run 1 and in a fourth run of the field test; runs 2 and 3 did not print them.
- **Sources:** every stored field has page 1 (right) and a snippet that the reader found on the text layer of the page, holding the value: confidence 0.9 for all 9. No stored source was invented. The three PDFs have a text layer, so no field has a whole-page box.
- **Why `booking_reference` is missing on the booking:** the model read the right value (`WPR-482913`) but gave the title of the page (`Booking confirmation`) as its snippet, in every run. The snippet does not hold the value, so the reader refuses the field (D80 (3)). The reader does not search the page for the value itself: that would replace the evidence of the model with a guess of the reader.
- **Why `cause` is wrong:** the expected value is a category (`illness`, from "acute illness"). The model gave the diagnosis printed on the page (`gastroenteritis with fever`), with a true snippet. The schema has no field descriptions yet (D77 (5)). The snippet check cannot catch it: the text is on the page (the limit "true text, wrong meaning" of D80 (3)).
- **Seconds per page** (one-page documents, classify and read together, 12 samples: 3 documents x 4 runs): median **5.5 s**, range 2.3 to 12.9 s. Booking about 6 s, cancellation about 5.6 s, certificate about 2.6 s. The live contract run: 210 s for the 35 mock files and the junk bytes (about 6 s per file).
- **Outcome through the API** (local mode, scripted agent, `CLAIMS_READER=omni`): **`straight_through`, score 0, no signal, no routing reason, in the 3 runs.** The fixture reader gives the same. It is right for the right reasons: every field that the routing reads was right and checked (`departure_date` and `cancellation_date` for C002; `trip_price` and `refund_received` for C003 and the settlement; the names for the consistency check; `unfit_from` for the incident date). The two errors are in fields that no check reads (the cause of the claim comes from the words of the customer, not from the certificate).
- **Contract:** `check_reader_contract(OmniReader())` passed (35 mock files, about 70 model calls, no refused request).
- **Running backend, through the endpoints of the UI** (uvicorn with `CLAIMS_MODE=local`, `CLAIMS_READER=omni`, one more run): the 3 uploads were read; `GET /claims/{id}` (the pipeline panel) holds the same 9 fields, each with its document id, page 1 and its snippet; the outcome is `straight_through`, status `paid`; the customer reads "Your claim is validated." The Reader bench (`POST /bench/read`) shows `booking_reference` of the booking as "missing" and `cause` as "wrong", and 3 of 3 line items twice. The page in a browser was not looked at in this check.
- **In the browser** (Nikola, by hand, `scripts/dev.sh` with `CLAIMS_READER=omni`): Anna Keller, policy TRV-2026-0417, the 3 PDFs uploaded. The 3 documents were classified right; the claim was validated with CHF 1,840.00 less the refund of CHF 340.00 = CHF 1,500.00; the customer read "Your claim is validated."

### Rate limit of the free key with Omni (O15, second measure)

`cd backend && uv run python ../scripts/probe_omni.py rate --per-minute 60 --max 80`: tiny text requests (4 tokens, thinking off), one at a time at a fixed pace, no retry, stop at the first error. 2026-11-17, 16:38:08 to 16:39:29 UTC: **80 answers 200 in 80.5 s, no 429** (about 60 in each 60 s window). So the limit for this model and key is above 60 requests per minute over 80 s; this run gives no upper bound. Not measured: requests with images (a limit on tokens would not show), a longer window, two visitors at the same time. The first measure (about 40 per minute) came from Lightning: the limit can differ per model.

### Limits of this measure

- One claim file, three clean PDFs with a text layer. Scans, photos, other types and the full kit are not measured here (issue #19).
- A right outcome does not prove the reading right: a missing field makes some checks skip in silence (for example, no `refund_received` skips C003).
- The confidences 0.9 and 0.5 are labels, not measured probabilities (D80 (4)).
- The model can give a wrong field with a true snippet (for example a line amount as the trip price); here it did not, on these three files.
- The results of the model can change from run to run; here the fields were the same in the 3 runs.
- The 3 runs above came before the review fixes of 2026-11-17 (D80 (3), (8), (11), (12)). One more live run after the fixes (claim file 1 through the API, 6 model calls): the same result, 8 of 10 fields right (the same missing and wrong field), 6 of 6 line items, every source checked at 0.9, `straight_through`, status `paid`; classify 1.0 to 1.2 s and read 2.4 to 4.8 s per call, 14.9 s for the whole claim. The page and pixel limits do not touch claim file 1 (three one-page A4 PDFs). The whole-number rule for snippets is stricter (it refuses a snippet that cuts a number, for example `CHF 1,840` taken from `CHF 1,840.00`), but it refused no snippet of this run: the old rule and the new rule gave the same answer for each of the 10 snippets that the model gave. Offline, the 90 snippets of the answer keys of the mock PDFs with a text layer all pass too. One run only: the change from run to run is not measured.

## Other things seen

- One run of the first measure ended with 11 model calls failing at once (about 0 s each) after a 74 s call. Its cause is not known: the error text was not kept. The later runs kept the reason of every failure (`fallback` events); the only failure in 36 claims was one 60 s timeout. Not reproduced.
- Reasoning off (`chat_template_kwargs.enable_thinking: false`, from the NVIDIA model card) works on both models (checked with one call each: `reasoning_tokens` is 0, the tool call is the same). The client supports it with `AGENT_THINKING=off`. It was not measured.
- Both models return several `tool_calls` in one answer. The runner runs them in order.

## Sandbox mode against nim mode (issue #14)

Date: 2026-11-12. Same player, same 9 claim files, 2 runs of each of the three claim files per mode (6 claims per mode), `nvidia/nemotron-3.5-lightning-30b-a3b`. Command: `cd backend && uv run python ../scripts/measure_agent.py --mode nim --runs 2` and `--mode sandbox --runs 2`, one after the other (the 40 requests per minute of the key). `nim`: the graph in the backend process. `sandbox`: the same graph in an OpenShell 0.1.5 sandbox on Colima, the tools over MCP, the image already on the Docker engine. Wifi and the model endpoint are not the same from one minute to the next: read the difference between the modes as a size, not as a precise value.

A turn is the time of one HTTP call of the customer panel: a message, an upload, the Done message or the policy choice that starts the chat. In `sandbox` mode the first turn of a claim includes the creation of its sandbox.

| Claim file | Mode | Right outcome | Model calls | Seconds per turn (median / worst) | Tokens | Fallback messages |
|---|---|---|---|---|---|---|
| travel | nim | 2/2 | 17.0 | 5.5 / 19.4 | 25,514 | 0 |
| travel | sandbox | 2/2 | 15.5 | 6.1 / 25.7 | 22,948 | 0 |
| property | nim | 2/2 | 15.5 | 6.3 / 13.7 | 22,808 | 0 |
| property | sandbox | 2/2 | 16.5 | 8.5 / 26.5 | 24,630 | 0 |
| health | nim | 2/2 | 14.0 | 9.6 / 23.2 | 20,654 | 0 |
| health | sandbox | 2/2 | 11.0 | 6.6 / 16.8 | 15,153 | 0 |
| **All** | **nim** | **6/6** | 15.5 | **6.6 / 23.2** | 22,992 | 0 |
| **All** | **sandbox** | **6/6** | 14.3 | **7.4 / 26.5** | 20,910 | 0 |

The sandbox itself, from the `sandbox` events of the same runs:

| What | Seconds |
|---|---|
| Create a sandbox (image already on the engine), 6 claims | median 0.49, worst 0.52 |
| `exec` of a turn (34 turns): median / worst | 7.15 / 26.50 |
| Fixed cost of a turn: `exec` of `true` | 0.05 |
| Fixed cost of a turn: Python start and imports (LangGraph, LangChain, MCP) | 0.9 |
| Delete a sandbox (the `deleted` event of the browser run; a demo reset with one sandbox took 0.18 in all) | 0.12 |

What the numbers say:

- The sandbox costs about 0.5 s once per claim (the creation) and about 1 s per turn (Python and the imports start again in each turn, 0.9 s). The median turn is 0.8 s slower (7.4 against 6.6 s); the model call is the rest of every turn, as in the spike. The per-file differences (property slower, health faster) are the number of model calls and the wifi, not the mode.
- Same result in both modes: 6/6 right outcomes, right date and amount, and 0 fallback messages. The three customer texts were word for word (checked by the test, and by the player).
- The worst turn (26.5 s) is a turn with a slow model call, as in `nim` mode (23.2 s).
- The MCP calls cost milliseconds: the backend and the sandbox are on the same machine.
- Not measured here: a first creation on a host where the image is not yet there (pulling `python:3.12-slim` took 4.4 minutes on the slow wifi, spike notes). Many sandboxes at the same time: see the last section.

## Several visitors at the same time (issues #115 and #117)

Date: 2026-11-17. The Brev VM, `sandbox` mode, `nvidia/nemotron-3.5-lightning-30b-a3b`, the free key. N threads each play one claim file (travel, property, health, then travel again) with the first wording of `backend/tests/live_claims.py`, against the running backend. One run for each line: read the numbers as a size.

| At the same time | Code | Total seconds | Right outcome | Answers 429 | Fallback messages | Worst turn (s) |
|---|---|---|---|---|---|---|
| 1 | before | 60 | 1/1 | 0 | 0 | - |
| 3 | before #115 | - | 2/3 | 0 | 0 | - |
| 3 | after #115 | 40 | 3/3 | 1 | 1 | 12.4 |
| 5 | after #115 | 80 | 3/5 | 21 | 21 | 27.8 |
| 5 | after #117 | 166 | 5/5 | 25 | 0 | 68.7 |

- Before #115, the third claim failed at once with a 500: a read of the store with no lock. It is not a limit of the sandboxes.
- The sandboxes are not the limit: five at the same time started and worked. The limit is the key, about 40 model calls per minute (O15). A claim file makes 13 to 25 model calls.
- After #117 the agent waits 5, 10 and then 20 seconds after a 429 and calls again. No visitor reads the fallback message, but a turn can take one minute, and five claim files take near three minutes.
- For more than three visitors at the same time, the key needs a larger limit. Paul decided for the event: one visitor at a time at the stand (D79).

## Evaluation harness of the reader (issue #19)

Date: 2026-11-18. Decision D81. The harness is `backend/app/docintel/evaluate.py`. The rule for one value is `backend/app/docintel/compare.py`. The page "Metrics" shows the last report (`GET /evaluation/latest`).

### Commands

- Fixture reader, no key, no network: `cd backend && uv run python -m app.docintel.evaluate --reader fixture`
- Omni reader, three runs (needs `NVIDIA_KEY`, `VLM_BASE_URL` and `VLM_MODEL` in `.env` at the root): `cd backend && uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 3`
- `--runs N` makes N runs, with a new reader for each run. `--output FILE` writes a second copy of the report.
- Combine saved runs, with no reader and no model call: `cd backend && uv run python -m app.docintel.evaluate --combine FILE FILE ...`. It writes a new `latest.json` and a dated copy, and never writes over an input. See "Combine saved runs" below.
- The command prints a markdown summary and writes the report to `backend/.dev-data/evaluation/latest.json`, with a copy named `evaluation-<reader>-<UTC time>.json`. `CLAIMS_EVALUATION` changes the folder. The write is atomic: the page never reads half a report.
- Exit code 0: the report is written. Exit code 1: the report is written, but an answer key could not be read (`complete: false`). Exit code 2: no report, because the reader did not start (for example, Omni with no `NVIDIA_KEY`).
- The reports are not in git (`.dev-data/` is in `.gitignore`). The numbers that count go into this file and into the pull request, with the command.

### What is measured

- **The kit.** Every PDF, JPG and PNG in `data/mock/<folder>/`: the same list as `/demo/kit` and the Reader bench. On 2026-11-18 that is 35 documents: 26 of the scenarios and 9 hard versions (6 scans, 3 phone photos). They hold 132 expected fields and 73 expected line items.
- **Kind** (clean, scan, photo) comes from the list `hard` of `data/mock/index.json`. **Workflow** comes from the policy of the scenario; a hard version takes the scenario of its source document.
- **Per document**, the harness calls `classify`, then `extract`, through the `Reader` contract, and times each call with `time.perf_counter`. It touches no claim, no store, no upload and no gateway.

### When a value is right

The kind of a field comes from its schema (#17), not from its value.

| Kind | Fields | Right | Wrong |
|---|---|---|---|
| Amount | every `float` field (`trip_price`, `total`, ...) | The same value to the cent (half up). `1840`, `1840.0`, `"1,840.00"`, `"1'840.00"` and `Decimal("1840.00")` are the same. | Another cent value, another sign. A text that is not a plain number: a currency (`"CHF 1,840.00"`), a decimal comma (`"1.840,00"`), bad groups (`"1,84"`). NaN, infinity, a bool. |
| Date | every `date` field | The same calendar day as `2026-08-10`, `10.08.2026`, `10.8.2026`, `10 August 2026`, `10 Aug 2026`, `10 Sept 2026` or `August 10, 2026`. | A slash date (`08/10/2026` is ambiguous), a two-digit year, `2026-8-10`, `20260810`, a day that does not exist, a date with a time. |
| Identifier | a text named `*_number` or `*_reference` | The same characters. Case, outer spaces, Unicode forms and dash characters (`WPR‑482913` with U+2011) do not count. | A number in place of a text (`482913` loses a leading zero), an inner space, another character. `001840` is not `1840`. |
| Text | any other text | The same text. Case, runs of spaces and dash characters do not count. | Another word, an accent (`Sanitär` is not `Sanitaer`), punctuation. There is no fuzzy match. |

A missing value is a field that is absent, `null` or blank text, or a field of a reader that raised. A missing value is never right (except when the answer key itself is `null`: no field of the kit is).

`compare` in `backend/app/bench.py` (D64) is not used here: it scores `"1,840.00"` against `1840.0` as wrong, the example of the issue, and `"001840"` against `"1840"` as right.

### How the numbers are counted

- **Right, wrong, missing** per expected field. These three add up to the expected fields. A wrong value and a missing value are always shown apart: a wrong amount can pay a claim, a missing one goes to a person.
- **Extra**: a field that the answer key does not expect. It is counted apart and is not in the denominator. A name given twice with the same value is right (the document lists it in `duplicates`); with two values it is wrong.
- **Accuracy** = right values / expected fields.
- **Source page right**: of the values read, how many have a valid source on the expected page. A value read is a right or wrong value; a field that the answer key gives as `null` and that the reader does not give is right, but has no source to check. A valid source names this document, a page that exists, and a snippet or a box (non-negotiable 3). An invalid source is never "page right".
- **Verified** = the value is right and its source is on the expected page. The value and the page are measured apart: a right value on the wrong page counts in the accuracy, but not as verified.
- **Pass rate** = documents with every expected field verified, and with no error of the reader / documents. Extra fields, the type and the line items do not change it.
- **Line items** are compared by position (description as text; quantity, unit price and amount to the cent), and reported apart: lines right / lines expected, and documents with every line right. They are not in the field accuracy and not in the pass rate.
- **Type**: the harness counts how many documents `classify` names right. The confusion table is issue #21.
- **Failures**: a document whose reader raised stays in every denominator, with every field missing and the error in the report. A document whose `expected.json` is absent or broken cannot be scored: the report lists it in `answer_key_errors` and says `complete: false`.
- **Runs**: each run has its own numbers (`run_details`). The page and the tables add the counts of all runs (`pooled`). `summary` gives the mean, the lowest and the highest value of each run, and the spread (highest − lowest).
- **Four measures, kept apart.** Field accuracy (right values / expected fields), the strict document pass rate, classification (the type that `classify` names right / documents) and line items (lines right / lines expected) are four numbers. Only the first two are about the fields. The summary gives each one per run (mean, lowest, highest), and the page "Metrics" shows each in its own tile.

### Combine saved runs

`--combine FILE FILE ...` makes one report from the runs of two or more saved reports, in the order of the files. No reader runs and no model is called. Use it to add runs to a measure without making the first runs again.

- **The numbers are made again from the rows.** For each document of each run, the counts and the pass are made again from its field rows. Then every total (per run, added, by type, by kind, by field) and the mean, lowest and highest are made from them. No percentage of a file is averaged.
- **A file is checked on its own first.** It must be readable JSON of schema version 1, with `runs` equal to its number of runs, numbered 1 to N. The counts it states for each document and each run must match its rows. A file that fails is refused, with the file and the run in the message.
- **The files must match each other.**
  - the same reader and the same `settings` (the configuration, and the model settings that the requests held)
  - the same fingerprint of the reader code, when the files hold one
  - the same `kit`, `complete` and `answer_key_errors`
  - in every run: the same documents, kinds, workflows and pages, and the same answer keys (every expected value and page)
- **Duplicates are refused.** This covers a file given twice, two files with the same content (for example `latest.json` and its dated copy), and a run that is in two files.
- **Fingerprint of the reader code.** Each new report holds `reader_code_sha256`: the SHA-256 of the source file of the reader (for Omni, `omni.py`, with its prompt, its settings and its code; line ends do not count). A report written before this existed has none. It is combined, and the combined report says so in `combine_notes`: its prompt and code are then checked by its settings only.
- **Nothing is half done.** When the files are refused, the command writes nothing: `latest.json` stays as it was. The output must not be one of the input files.
- The combined report holds `combined_from`: the name, the SHA-256, the date and the number of runs of each file. Each run keeps its own `run_id` and `started_at`, and its `source`: the file and the run number in that file.
- **Seconds per document** = the time of `classify` plus `extract`, per document; the mean, median, lowest and highest.
- **HTTP** (Omni only): an event hook of the HTTP client counts every request and every answer by status. A 429 that the reader retried is counted too, so `http_429` is the number of 429 answers. `settings.sent` lists the model settings that the request bodies held (model, temperature, `max_tokens`, thinking, response format). The report keeps no header, no key, no image and no answer of the model.

### Result: fixture reader

`cd backend && uv run python -m app.docintel.evaluate --reader fixture --runs 3`, 2026-11-18, Windows laptop.

| | Documents | Fields right | Wrong | Missing | Source page right | All right | Line items right |
|---|---|---|---|---|---|---|---|
| Clean | 26 | 94 of 94 | 0 | 0 | 94 of 94 | 26 of 26 | |
| Scan | 6 | 25 of 25 | 0 | 0 | 25 of 25 | 6 of 6 | |
| Photo | 3 | 13 of 13 | 0 | 0 | 13 of 13 | 3 of 3 | |
| **All, per run** | **35** | **132 of 132** | **0** | **0** | **132 of 132** | **35 of 35** | **73 of 73** |

The 3 runs gave the same numbers: accuracy, verified accuracy and pass rate 100% in each. Each of the 11 document types is at 100%. Seconds per document: below 0.001 s (the fixture reader reads a hash); one run of the 35 documents took 0.02 to 0.03 s. This result tests the harness, not a model: the fixture reader returns the answer keys. `backend/tests/test_evaluate.py` checks the 100% in CI, and gives the harness readers that are wrong on purpose (a wrong amount, a missing field, a null, a wrong page, a source of another document, a field given twice, a reader that raises, a broken answer key): each one is counted as wrong or missing, never as right.

### Result: Omni reader

Not measured yet. The live run calls the NVIDIA endpoint and needs the approval of Nikola.
