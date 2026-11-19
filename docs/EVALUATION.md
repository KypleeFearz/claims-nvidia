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

### Result: Omni reader, 3 runs

Date: 2026-11-18. Three runs on the 35 documents of the kit, one call at a time, with the free developer key, from a Windows laptop on a home network. Mock data only.

Commands (in `backend/`, with `NVIDIA_KEY`, `VLM_BASE_URL` and `VLM_MODEL` in `.env` at the root):

1. Run 1, 15:44 to 15:47 UTC: `uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 1 --output .dev-data/evaluation/omni-run-1.json`
2. Runs 2 and 3, 16:12 to 16:18 UTC: `uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 2 --output .dev-data/evaluation/omni-runs-2-3.json`
3. The three runs in one report, with no model call: `uv run python -m app.docintel.evaluate --combine .dev-data/evaluation/omni-run-1.json .dev-data/evaluation/omni-runs-2-3.json --output .dev-data/evaluation/omni-3-runs.json`

Settings kept fixed. They are the settings of `OmniReader` of #18, unchanged, and the report records them:

- model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, at `https://integrate.api.nvidia.com/v1`
- `response_format` `json_schema` (strict), thinking off, temperature 0.2, `max_tokens` 2048
- 150 DPI, one call to classify (page 1), then one call per page
- at most 10 pages and 16 MP per image, timeout 90 s, 2 retries
- prompt version 1 (`EXTRACT` and `CLASSIFY` in `omni.py`)

The request bodies of all 198 calls held the same model, temperature, `max_tokens`, thinking and response format (`settings.sent`). `omni.py` did not change between the runs: git shows no change since #121, and runs 2 and 3 record its fingerprint `d84bcd22…bef4`. Run 1 was written before the harness recorded a fingerprint; `--combine` notes it in `combine_notes`.

**The four measures** (each run gives one value; the counts are added over the 3 runs):

| Measure | Mean | Lowest | Highest | Counts over the 3 runs |
|---|---|---|---|---|
| Field accuracy (right values / expected fields) | 84.9% | 84.9% | 84.9% | 336 right, **24 wrong**, **36 missing** of 396 |
| Strict document pass rate (every field right and on its page) | 48.6% | 48.6% | 48.6% | 51 of 105 |
| Classification (type right) | 88.6% | 88.6% | 88.6% | 93 of 105 |
| Line items (lines right / lines expected) | 72.6% | 72.6% | 72.6% | 159 of 219 |
| Source page right (of the values read) | 100% | 100% | 100% | 360 of 360 |
| Seconds per document | 5.45 | 5.35 | 5.50 | median 5.09, lowest 0.87, highest 12.05 |

Per run: 112 right, 8 wrong, 12 missing of 132 fields; 17 of 35 documents all right; 31 of 35 types right; 53 of 73 lines right. One run took 187 to 193 s.

**By document kind** (counts over the 3 runs; each kind gave the same accuracy in each run):

| Kind | Documents per run | Right | Wrong | Missing | Accuracy | All right | Lines right | Seconds per document |
|---|---|---|---|---|---|---|---|---|
| Clean | 26 | 231 | 18 | 33 | 81.9% | 33 of 78 | 159 of 159 | 4.99 |
| Scan | 6 | 69 | 6 | 0 | 92.0% | 12 of 18 | 0 of 42 | 7.82 |
| Phone photo | 3 | 36 | 0 | 3 | 92.3% | 6 of 9 | 0 of 18 | 4.69 |

**By document type** (counts over the 3 runs; the lowest and the highest run when they differ):

| Type | Documents per run | Accuracy | Right | Wrong | Missing | All right | Lines right | Seconds per document |
|---|---|---|---|---|---|---|---|---|
| cancellation_confirmation | 3 | 100% | 27 | 0 | 0 | 9 of 9 | 27 of 27 | 6.24 |
| repair_invoice | 4 | 100% | 60 | 0 | 0 | 12 of 12 | 24 of 48 | 7.24 |
| repair_quote | 2 | 96.7% (90% to 100%) | 29 | 0 | 1 | 5 of 6 | 24 of 24 | 7.01 |
| medical_invoice | 3 | 93.3% | 42 | 0 | 3 | 6 of 9 | 30 of 45 | 8.44 |
| receipt | 6 | 93.1% (91.7% to 95.8%) | 67 | 0 | 5 | 13 of 18 | 18 of 30 | 4.74 |
| luggage_report | 2 | 87.5% | 21 | 0 | 3 | 3 of 6 | | 3.48 |
| booking_confirmation | 5 | 80.0% | 48 | 0 | 12 | 3 of 15 | 36 of 45 | 7.74 |
| police_report | 2 | 75.0% | 18 | 6 | 0 | 0 of 6 | | 4.36 |
| medical_certificate | 2 | 66.7% | 12 | 6 | 0 | 0 of 6 | | 4.42 |
| medical_report | 2 | 50.0% | 12 | 12 | 0 | 0 of 6 | | 3.82 |
| damage_photo | 4 | 0% | 0 | 0 | 12 | 0 of 12 | | 1.10 |

**The weakest fields** (over the 3 runs):

| Field | Right | Wrong | Missing | What the report shows |
|---|---|---|---|---|
| `damage_photo.damage_description` | 0 of 12 | 0 | 12 | The reader does not read a damage photo (issue #25). `classify` also gives `unknown` for all 4 photos: these are the 12 type errors. |
| `medical_certificate.cause` | 0 of 6 | 6 | 0 | The diagnosis ("gastroenteritis with fever"), not the category `illness`. |
| `medical_report.diagnosis` | 0 of 6 | 6 | 0 | The right diagnosis with one more sentence ("… No fracture on the X-ray."). The text rule has no fuzzy match. |
| `medical_report.provider` | 0 of 6 | 6 | 0 | The name of the doctor, not the clinic. |
| `police_report.offence` | 0 of 6 | 6 | 0 | "Burglary (forced entry)", not `burglary`. |
| `booking_confirmation.booking_reference` | 3 of 15 | 0 | 12 | Refused on the 4 clean PDFs: the model gives the page title as its snippet (as in #18). Right on the scan, where no text layer can refuse it. |
| `receipt.amount_paid` | 13 of 18 | 0 | 5 | Missing on the trolley receipt in the 3 runs, and on the lock-set receipt in runs 2 and 3. |
| `medical_invoice.invoice_number` | 6 of 9 | 0 | 3 | Missing on the clean medical invoice of Lukas in each run. |
| `luggage_report.airline` | 3 of 6 | 0 | 3 | Missing on the phone photo in each run. |
| `repair_quote.total` | 5 of 6 | 0 | 1 | Missing on the quote of Anna in run 1 only. |

**Line items**: every line of the clean PDFs is right (159 of 159). No line is read on a scan or a photo (0 of 60): the reader keeps the lines only when it can confirm each one on the text layer of the page (D80 (6)), and a scan or a photo has none.

**HTTP**: 198 requests (66 per run: 35 to classify, 31 to read a page; a damage photo is not read), every answer 200. No 429, no other error status, no transport error, no reader error. One call at a time is about 20 requests per minute.

**Variation between runs**: the totals are the same in the 3 runs, but 2 of the 132 field results changed: `amount_paid` of the lock-set receipt (right in run 1, missing in runs 2 and 3) and `total` of the quote of Anna (missing in run 1, right in runs 2 and 3). The two changes cancel out in the totals. So the run-to-run variation is at least 2 fields of 132, and a change of #20 that moves the accuracy by one or two fields is not a gain.

**What the numbers say**:

- The 24 wrong values are all text fields where the answer key holds a short category or the clinic, and the model gives what the page prints. No amount, no date and no identifier was wrong. A wrong amount is the costly error, and there is none.
- Most of the 36 missing values are refused snippets (`booking_reference`) and documents that the reader does not read (damage photos). A missing value goes to a person.
- The strict pass rate (48.6%) is much lower than the field accuracy (84.9%): one weak field fails the whole document.
- Scans and photos have a better field accuracy than the clean PDFs (92% against 81.9%). With no text layer, the reader cannot refuse a snippet, and the source is a box of the whole page (D80 (3)). Their values are stored at confidence 0.5, so a person checks them.

**Limits of this measure**:

- Every document of the kit has one page, so "source page right" is 100% by construction here. The wrong page and an invalid source are tested only offline (`backend/tests/test_evaluate.py`).
- 35 mock documents, generated by our own script, with one layout per type: a real document is harder.
- Three runs in one afternoon, on one network; the endpoint load changes with the hour.
- Four of the ten weakest fields (`cause`, `offence`, `diagnosis`, `provider`) raise one question: which value is right, the text that the page prints or the value of the answer key (a category such as `illness` or `burglary`, a short diagnosis, the clinic and not the doctor). That is a question for #20 (field descriptions) and for Paul, not for the harness.
- The reports are in `backend/.dev-data/evaluation/` on the laptop, not in git. SHA-256: `omni-run-1.json` `4d1535ff…7227`, `omni-runs-2-3.json` `0e01a924…0a45`.

### Business numbers with the Omni reader (one run)

Date: 2026-11-18, 16:37 to 16:39 UTC. The page "Business numbers", button "Play the kit" (`POST /numbers/kit`, issue #96, D71): the 11 stories of the kit through the reader, the checks, the routing and the settlement, with no agent model. Same Omni settings as above, `CLAIMS_MODE=local`, `CLAIMS_READER=omni`. One run only.

How it was run: the backend app in one process (FastAPI `TestClient`, as `scripts/measure_agent.py` does), with the variables of `.env`, then one `POST /numbers/kit`. A script outside the repository put the HTTP counter of the harness on the client of `OmniReader`, and played the fixture reader first through the same function (`play_kit`). With the app running (`CLAIMS_READER=omni` in `.env`, `scripts/dev.sh`), the button "Play the kit" makes the same call.

| Reader | Outcomes right | Signal sets right | Amounts right | Seeded fraud caught | False alarms | Seconds | Model requests |
|---|---|---|---|---|---|---|---|
| Fixture (baseline) | 11 of 11 | 11 of 11 | 11 of 11 | 3 of 3 | 0 | < 1 | none |
| **Omni** | **10 of 11** | **8 of 11** | **10 of 11** | **3 of 3** | **0** | 128.3 | 52, all 200; no 429 |

The differences, story by story (the 7 other stories are the same as with the fixture reader):

| Story | Outcome (expected) | Signals (expected) | Amount (expected) | Cause, in the reading |
|---|---|---|---|---|
| `kit-anna-luggage/787` | `handler_review` (`straight_through`) | none (none) | CHF 398 (CHF 787) | `amount_paid` of the trolley receipt is missing (its snippet is refused, as in the harness). The documents then prove CHF 398 against CHF 787 claimed, so a person decides (D67). |
| `claim-2-property/3200` | `handler_review` (same) | R001 (none) | CHF 3,000 (same) | `classify` gives `unknown` for the damage photos, so the workflow sees no damage photo: R001 "A required document is missing". |
| `kit-anna-theft/2640` | `handler_review` (same) | R001 (none) | CHF 2,440 (same) | The same: the damage photo is `unknown`. |
| `kit-marco-bathroom/4740` | `investigation` (same) | R001, T001, T002, T003 (T001, T002, T003) | CHF 1,540 (same) | The same: the damage photo is `unknown`. |

What it says:

- No payment is wrong to the business: the one wrong amount is a proposal that goes to a person (`handler_review`), not a payment by the system. No amount was overpaid, every seeded fraud was caught, and there is no false alarm.
- The costs come from two errors that the field evaluation also shows: the damage photos (type `unknown`, issue #25 and #21) add R001 to 3 stories, and one missing amount sends a clean luggage claim to a person.
- One run only: the field evaluation shows 2 fields of 132 that change from run to run. One of them is the `total` of the quote of the story `kit-anna-theft`, which the settlement reads: it was read in this run (CHF 2,440 right), but it was missing in run 1 of the field evaluation. Another run can give other business numbers.
- The risk of #121 (a document with no field read is paid as complete) did not happen in this run.

## Tuning of the Omni reader (issue #20): the method

Written on 2026-11-19, before the first run of a variant. The rules below do not change after a result is seen. A change of a rule is written here with its date and its reason.

### The control

- The control is the 3 saved runs of #19 above: `omni-run-1.json` (`4d1535ff…7227`) and `omni-runs-2-3.json` (`0e01a924…0a45`), settings of #18 (`ISSUE_18` in `omni.py`). No run of the control is made again at the start.
- Why the saved runs are still a valid control: `backend/tests/test_omni_settings.py` pins the two request bodies of #18 (SHA-256 of the classify body and of the page body, with no image; the image is compared pixel for pixel with a page rendered at 150 DPI). The hashes were taken from the code of #18 (fingerprint `d84bcd22…bef4`) before any change of #20. With the default settings, the new code sends the same bytes.
- At the end, the settings of #18 run 3 times again (stage S7). If they give the totals of the saved runs (2 field results that change per run at most), the endpoint did not drift, and the runs of each stage stay comparable. If not, the winner runs 3 times again on the same day as the control.

### Commands

`--omni KEY=VALUE` (one or more) changes one setting of `OmniReader`. Every other setting stays the setting of #18. Example: `cd backend && uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 3 --omni dpi=100 --output .dev-data/evaluation/s3-dpi-100.json`.

| Key | Values | What it changes |
|---|---|---|
| `prompt` | `v1` (#18), `schema-only`, `described`, `example` | The text of the page call. The classify call does not change (issue #21). |
| `dpi` | 50 to 300 (#18: 150) | The render of a PDF page, for both calls. A photo (JPG, PNG) is sent as it is. |
| `temperature` | 0 to 2 (#18: 0.2) | Both calls. |
| `thinking` | `on`, `off` (#18: off) | `chat_template_kwargs.enable_thinking`, both calls. |
| `reasoning_budget` | a number less than `max_tokens`, only with thinking on (#18: none, not sent) | The key `reasoning_budget` of the NIM API. |
| `max_tokens` | 1 to 65,536 (#18: 2048) | With thinking on, the budget is part of it. |
| `timeout` | seconds, a finite number more than 0 (#18: 90) | Per call. Thinking takes longer. |

The report records each value in `settings`, with `prompt_sha256` (the texts of the prompt: classify, page template, descriptions, example) and `config_id` (a short name of the whole). `--combine` compares `settings`, so it never mixes two configurations. Each run also records the tokens of the answers (`usage`: prompt, completion, reasoning) and why each answer ended (`finish_reasons`): the cost of a setting and the answers cut by `max_tokens` (`length`).

The endpoint does not always report the reasoning tokens. Two counters show what is not in the sums:

- `no_usage`: the answers with no prompt and completion counts. They add nothing to the sums.
- `no_reasoning`: the answers with no reasoning count. The sum `reasoning` is for the other answers only.

A reported 0 is a count, not a missing value. Before you compare the reasoning tokens of two variants, make sure that `no_reasoning` is 0 for both.

### The prompts

| Prompt | Text of the page call | It measures |
|---|---|---|
| `v1` | The prompt of #18: the rules of the values (null when not on the page, never guess, date and amount forms, every line), the rule of the snippets (the exact text, copied character by character, as short as possible) and the JSON schema. | the control |
| `schema-only` | Only the form of the answer, the rule of the snippets of `v1` (word for word) and the JSON schema. | what the rules of the values of `v1` bring. The rule of the sources does not change. |
| `described` | `v1`, and each field of the schema has a description (`DESCRIPTIONS` in `omni.py`). The same described schema goes in the prompt and in `response_format`. | what the descriptions bring |
| `example` | `described`, and one worked example at the end: the text of a page and its right answer. | what the example brings (against `described`) |

- Every prompt keeps the strict `json_schema` and the snippets. The checks of a source do not change (non-negotiable 3). A test runs the provenance checks for each prompt.
- The descriptions come from the business: `docs/WORKFLOWS.md`, the causes of the workflows (`WORKFLOWS`, for `cause`), and Paul on pull request #122 (`cause` is a category, `diagnosis` is short, `provider` is the clinic). `offence` has a general description: the kind of offence as a short general legal term. No description holds a value of an answer key, apart from the names of the causes (a test checks it).
- The example is a medical invoice that is not in the kit. Every name, number and date of it is invented. Tests compare it with every text of the kit: the answer keys and the other JSON files of `data/mock`, the text layer of each mock PDF, `scripts/mockdocs` and `docs/MOCK-DATA.md`. These must not be in a text of the kit:
  - a name (a word with a capital letter that is not a word of the form, such as `Invoice` or `Total`);
  - a number of 4 characters or more (a postcode, an identifier, a date, an amount);
  - a run of 3 words.

  Another test checks that the example passes the checks of the reader: each snippet is on its page and holds its value. The prompt ends with one line that sends the model back to the image of the request. Limit: the example shows `provider` as the clinic, a field that is wrong in the control.

### Three groups of fields

The groups are fixed now, from the control. Every table gives the 132 fields and the three groups apart.

| Group | Fields | Control, per run (the same in the 3 runs) |
|---|---|---|
| Reading | every field that is not in the two other groups | 109 of 117 right, 0 wrong, 8 missing |
| Business category | `medical_certificate.cause`, `police_report.offence`, `medical_report.diagnosis`, `medical_report.provider`, `medical_invoice.provider` | 3 of 11 right, 8 wrong, 0 missing |
| Not read (issue #25) | `damage_photo.damage_description` | 0 of 4 |

A gain in the business-category group says that the reader follows the meaning of the business. It is not a better reading of the page. It is reported apart and never hides a loss in the reading group.

### The stages

Each variant changes one setting against the control of its stage and runs 3 times on the 35 documents of the kit (66 requests per run, 198 per variant). The control of a stage is the winner of the stage before; its runs are used again, not made again.

| Stage | Variants | Control | New requests |
|---|---|---|---|
| S2 prompt | `schema-only`, `described`, `example` | `v1` (the saved runs) | 594 |
| S3 resolution | 72, 100, 200 DPI | 150 DPI (the S2 winner) | 594 |
| S4 temperature | 0, 0.3, 0.5 | 0.2 (the S3 winner) | 594 |
| S5 reasoning | thinking on, budget 512 and 2048; `max_tokens` = 2048 + budget + 1024, so the answer keeps 2048 tokens | thinking off (the S4 winner) | 396 |
| S7 final | the settings of #18, 3 runs | the saved runs | 198 (+198 if drift) |

Total of the stages: 2,376 requests, 2,574 if S7 shows a drift. The smoke checks come in addition: 8 requests before S2 (24 at most with the retries; done on 2026-11-19: 8 requests, all 200, no retry) and 4 to 10 before S5.

- S4 compares 4 temperatures: 0, 0.2, 0.3 and 0.5. 0.2 is the control: its runs are the runs of the S3 winner, used again. Each new temperature (0, 0.3, 0.5) runs 3 times on the full kit. Every other setting is the setting of the S3 winner and does not change in S4. The same rules of "When a variant wins" apply to each of the 3.
- Changed on 2026-11-19, before any run of a variant, by decision of Nikola. The plan had 0 only (198 requests). The reason: one point below 0.2 shows only one side. With 0.3 and 0.5, the stage also shows whether a temperature above 0.2 helps or hurts, so the choice of 0.2 is measured, not assumed. The cost is 396 more requests.
- Before S2 and before S5, a short check on 1 or 2 documents (4 to 10 requests) shows that the endpoint accepts the new request. It is not a result.
- S5 runs only if its check shows that thinking with the strict schema gives an answer. The model card says the thinking text can come in `content`, before the JSON: the reader would then lose every page.
- One call per page or per document: every document of the kit has one page, so the two give the same request. How to report it is open (Nikola and Paul).
- Choosing stage by stage: the effect of a stage is measured with the winners of the stages before. S5 runs at the temperature of S4; the model card advises 0.6 with thinking. This is a limit, not a second change.

### When a variant wins

These are practical rules for a decision, not tests of significance. With 10 scenarios and about 11 layouts, a p-value would give a false precision.

A variant replaces the control of its stage only when all these are true:

1. It gives at least 3 more right fields per run (the mean of its 3 runs against the mean of the control). The control changes by 2 field results between runs.
2. Its lowest run is higher than the highest run of the control.
3. The guardrails hold:
   - no wrong amount, date or identifier (the control has none);
   - no more wrong values in total than the control;
   - the reading group loses 1 right field per run at most;
   - classification loses 1 document per run at most;
   - every line item of the clean PDFs stays right (53 of 53 per run);
   - no reader error, and no 429 that the retries did not solve;
   - the seconds per document are 1.5 times those of the control at most, or Paul accepts the cost.
4. The gain stays above 0 when any one scenario is left out.

When no variant wins, the control stays. When two variants win, the one with the larger gain wins; at the same gain, the cheaper one (seconds, tokens). `example` replaces `described` only when it wins against `described` by the same rules.

### What each variant reports

Accuracy per field (right, wrong, missing; the 132 fields and each group), strict pass rate, classification, line items, seconds per document (mean and median), requests, statuses, 429, tokens, finish reasons, the lowest and the highest run, and `config_id`. The paired view is made offline from the saved reports: for each document, the mean of its 3 runs against the control, the scenarios better and worse, and the documents whose type changed (a type changes the schema). Three runs of one document are one document, not three.

### Limits, known before the runs

- The kit is small: 35 documents, 10 scenarios, about 11 layouts (one per type), all made by one generator. The descriptions and the example were written after the errors of the control were seen. A gain on the kit does not prove a gain on other documents. A holdout with new layouts needs Paul's approval (a separate issue).
- 200 DPI gives more image tokens than the model takes for one image (13,312 patches of 16 px; an A4 page at 200 DPI has about 15,300): the server probably makes it smaller. The 6 scans are images at 150 DPI, so 200 DPI only enlarges them; the 3 phone photos do not change with the DPI.
- A category value passes the check of its snippet only when the word is on the page (`acute illness` holds `illness`). On a page that does not print it, the field is refused: it goes to a person.
- The line items of scans and photos stay 0: the reader keeps a line only when the text layer confirms it (D80 (6)). No setting of #20 changes that.

## Tuning of the Omni reader (issue #20): S2, the prompt

Run on 2026-11-19 with the method above. The prompts, the answer keys, the comparison and the reader code did not change during the stage. An independent reviewer recomputed every number below from the JSON reports and found no error.

### What ran

- 3 variants × 3 runs × 35 documents: 594 requests. Every answer was 200, with no retry, no 429, no transport error and no reader error. Every answer ended with `stop` (none cut by `max_tokens`). Before the stage, a smoke check made 8 requests (all 200).
- Settings of #18 apart from the prompt: 150 DPI, temperature 0.2, `max_tokens` 2048, thinking off.
- Code fingerprint `49603fe1…` in the 3 reports; git `4cb0a87`.
- The reports are in `backend/.dev-data/issue-20/s2/` (not in git), with the frozen state (`freeze.txt`), the times (`timeline.txt`), the analysis script (`analyze_s2.py`, `compare_pair.py`) and its outputs (`*.analysis.txt`). The saved reports of #19 and the Metrics `latest.json` did not change.

| Variant | `config_id` | Report |
|---|---|---|
| `schema-only` | `3cc3e9264e13` | `s2-schema-only.json` |
| `described` | `2df4dfdab3b8` | `s2-described.json` |
| `example` | `f83b155b3d22` | `s2-example.json` |

### Results per run

The control is the 3 saved runs of #19 (`v1`). It gave the same totals in each run.

| Per run (mean; runs) | `v1` (control) | `schema-only` | `described` | `example` |
|---|---|---|---|---|
| Right of 132 | 112 | **120** (120, 120, 120) | 115.3 (114, 115, 117) | 119.7 (122, 116, 121) |
| Wrong | 8 | 8 | 4 | 3.7 |
| Missing | 12 | 4 | 12.7 | 8.7 |
| Reading group, right of 117 | 109 | **117** | 110 | 115 |
| Business category, right of 11 | 3 | 3 | 5.3 | 4.7 |
| Not read (damage photo), of 4 | 0 | 0 | 0 | 0 |
| Strict pass, of 35 | 17 | 25 | 19.3 | 24.7 |
| Classification, of 35 | 31 | 31 | 31 | 31 |
| Line items, of 73 (clean PDFs 53 of 53) | 53 | 53 | 53 | 53 |
| Wrong amount, date or identifier | 0 | 0 | 0 | 0 |
| Fields whose result changes between runs | 2 | **0** | 7 | 8 |
| Seconds per document (mean) | 5.45 | 4.40 | 4.65 | 5.43 |
| Prompt tokens per run | not recorded | 149,942 | 155,797 | 168,042 |
| Completion tokens per run | not recorded | about 10,200 | about 10,300 | about 10,150 |

Classification is 31 of 35 for every prompt. The 4 damage photos give `unknown`, and the prompt of the page call does not change the classify call.

### The rules of "When a variant wins"

| Comparison | Gain per run | Lowest run > highest control run | Guardrails | Gain with any one scenario left out (lowest) | Wins |
|---|---|---|---|---|---|
| `schema-only` against `v1` | +8.00 | 120 > 112 | hold | +5.00 | yes |
| `described` against `v1` | +3.33 | 114 > 112 | hold | +0.33 | yes, narrowly |
| `example` against `v1` | +7.67 | 116 > 112 | hold | +5.33 | yes |
| `example` against `described` | +4.33 | 116 > 117: **no** | hold | +3.33 | no |

`example` does not win against `described`, so it does not replace `described`. Of the winners, `schema-only` has the largest gain (+8.00 against +3.33). It is also the cheapest prompt and the only one with no change between runs. **The rules select `schema-only`.** Its margin over `example` (+7.67) is 0.33 field per run: that is noise. The selection holds for each reading of the rule on `example`.

### Where the +8 of `schema-only` comes from

The control misses 9 fields of the reading group:
- 7 in all 3 runs: the `booking_reference` of 4 booking confirmations (`claim-1-travel`, `kit-anna-luggage`, `kit-elena-travel`, `kit-marco-travel`), the `amount_paid` of `kit-anna-luggage/receipt-trolley.pdf`, the `invoice_number` of `kit-lukas-clean/medical-invoice.pdf` and the `airline` of `hard/kit-anna-luggage-luggage-report-photo.jpg`.
- 2 in some runs: the `amount_paid` of `kit-anna-theft/receipt-lock-set.pdf` (missing in runs 2 and 3) and the `total` of `kit-anna-theft/repair-quote.pdf` (missing in run 1).

Each control run misses 8 of these 9. `schema-only` reads all 9 right in each run and loses no field that the control reads: 9 documents are better, 0 worse.

All 9 values have their source on the right page. Two kinds:
- 8 are on PDFs with a text layer: the snippet is on the text layer and holds the value (confidence 0.9).
- 1, the `airline` of the luggage report photo, has no text layer. Only the model gives its source (confidence 0.5, so a person checks it). `described` and `example` also read it.

The 4 booking references are on the text layer as `Booking reference:` and the value on the next line, and a snippet with or without the line break passes the check. So the control most probably gave null for them. The reports cannot prove it: they do not keep the answer of the model, and a missing field has the one reason `absent` for a null and for a refused snippet.

### What the stage shows, and what it does not

- **Why `schema-only` reads more:** the most probable cause is that the rules of `v1` make the model give null too often, mostly "Use null for a value that is not on this page: never guess". But `schema-only` drops 4 parts at once:
  - the null rule;
  - the forms of dates and amounts;
  - the rule for the line items;
  - "Read the whole image" and "JSON only".

  The stage cannot say which part does it. `described` keeps the rules of `v1` and on the same day misses the same booking references (11 of 12). So the cause is the prompt, not the day of the control.
- **The kit cannot test the risk of `schema-only`.** No answer key expects an empty value: the 132 expected fields all have a value. So the kit cannot see a value that the model invents when the page has none, and that is exactly what "never guess" prevents. Two checks reduce the risk:
  - On a page with a text layer, an invented value is refused: its snippet must be on the page and hold the value.
  - On a scan or a photo, a value is accepted with confidence 0.5, so the claim goes to a person.

  Neither check finds a true value of the page that goes into the wrong field. In the kit, `schema-only` gives the same 8 wrong values as the control and no new wrong value.
- **The descriptions fix little, and they make the reader less stable.** The business gain of `described` and `example` comes from 2 documents:
  - In `kit-lukas-clean/medical-report.pdf`, `diagnosis` and `provider` become right.
  - In `claim-3-health/medical-report.pdf`, they stay wrong.
  - `offence` stays wrong for every prompt.

  About half of the drop of wrong values is a wrong value that becomes missing: `cause` "gastroenteritis" becomes no value, because "illness" is not on the page. `described` also wins only through one scenario (+0.33 when `kit-lukas-clean` is left out). The descriptions were written after the errors of the control were seen, so a gain from them is the most likely to be overfitting.
- **`described` and `example` lose fields that the control reads.** The guardrails written in advance check wrong values, not lost ones, so they pass.
  - `described` loses a `total` in 4 documents in some runs:
    - `claim-3-health/medical-invoice.pdf` in 1 of 3 runs, as in the smoke check;
    - the repair invoice photo in 2 runs;
    - the `kit-anna-theft` quote in 2 runs;
    - the `kit-lukas-household` quote in 1 run.

    It also loses `item_description` of `kit-anna-theft/receipt-door-frame.pdf` in 3 of 3 runs, and of `kit-anna-luggage/receipt-jacket-shoes.pdf` in 1 run.
  - `example` lost all 4 fields of `kit-anna-luggage/booking-confirmation.pdf` in run 2, while the 3 line items of that page were read right. So the answer was parsed, and every field was a null or a refused snippet. No read value copies the worked example.
  - `schema-only` loses nothing.
- **Time:** the control ran one day earlier. Its 5.45 s per document cannot be compared exactly with 4.40 s. Run 2 of `example` had one call of 66 s (no retry). S7 checks the drift of the endpoint.

### Limits of the result

- 35 documents, about 11 layouts, one generator, no document of the kit kept apart. A gain on the kit does not prove a gain on documents that the model has not seen.
- The kit has no field that is expected empty, so the risk of invented values is not measured (see above).
- 1 of the 8 gained fields comes from a photo, with a source that only the model gives.
- The reports do not keep why a field is missing (a null or a refused snippet).

### Decision and what comes next

- By the rules, `schema-only` is the winner of S2, and it is the control of S3. The defaults of `OmniReader` (`ISSUE_18`, prompt `v1`) do not change in this stage.
- Open, for Nikola and Paul, and not started:
  - a check of invented values on documents where a field is truly absent (this needs new mock documents, so Paul must approve it);
  - a reason per missing field in the report (a null or a refused snippet);
  - `schema-only` with the descriptions. This is a new variant made after the results were seen. If it runs, it must say so.
