# Tuning of the Omni reader (issue #20): method and details

The result, the table of every variant, the rules and the limits are in [`EVALUATION.md`](EVALUATION.md), section "Tuning of the Omni reader (issue #20)". This file keeps what a reviewer needs to check or repeat a stage: the method, the tables of each stage, the causes of the missing fields and the checks. The full text written during the work, with every amendment and review note, is in the history of the branch `nikola-work` (commit `07767f4`).

**Measured**, **interpretation** and **open** mark what the saved files show, what is probable, and what these data cannot answer.

## 1. Settings and commands

`OmniSettings` in `backend/app/docintel/omni.py` holds the settings; `ISSUE_18 = OmniSettings()` is the configuration of #18 (`config_id` `b0f2e2ccab2b`). With these defaults the requests are those of #18 byte for byte: `backend/tests/test_omni_settings.py` pins the SHA-256 of both request bodies, taken from the code of #18.

`--omni KEY=VALUE` (one or more) changes one setting; every other setting stays that of #18:

| Key | Values | What it changes |
|---|---|---|
| `prompt` | `v1` (#18), `schema-only`, `described`, `example` | The text of the page call. The classify call does not change. |
| `dpi` | 50 to 300 (#18: 150) | The render of a PDF page, for both calls. A photo is sent as it is. |
| `temperature` | 0 to 2 (#18: 0.2) | Both calls. |
| `thinking` | `on`, `off` (#18: off) | `chat_template_kwargs.enable_thinking`, both calls. |
| `reasoning_budget` | below `max_tokens`, only with thinking on (#18: not sent) | The top-level key `reasoning_budget` of the NIM API. |
| `max_tokens` | 1 to 65,536 (#18: 2048) | With thinking on, the thinking counts in it (measured in S5). |
| `timeout` | seconds, more than 0 (#18: 90) | Per call. |

Each report records the settings, `prompt_sha256`, `config_id`, the tokens (`prompt`, `completion`, `reasoning`, and `no_usage` / `no_reasoning` for answers that report no count) and the finish reasons. `--combine` refuses to mix two configurations.

## 2. The prompts (S2)

| Prompt | Text of the page call |
|---|---|
| `v1` | The prompt of #18: the rules of the values (null when not on the page, never guess, forms of dates and amounts, every line), the rule of the snippets, the JSON schema. |
| `schema-only` | Only the form of the answer, the rule of the snippets of `v1` word for word, and the JSON schema. |
| `described` | `v1`, with a description of each field in the schema (`DESCRIPTIONS`), sent in the prompt and in `response_format`. |
| `example` | `described`, with one worked example: a medical invoice that is not in the kit. |

- Every prompt keeps the strict `json_schema` and the snippets; the checks of the source do not change.
- Tests check that the example shares no name, no number of 4 characters or more and no run of 3 words with any text of the kit, and that it passes the checks of the reader.
- The descriptions were written after the errors of the control were seen, so a gain from them is the most likely to be overfitting.

## 3. How the stages ran

| Stage | Variants | Control | Requests (sent) | Limit |
|---|---|---|---|---|
| S2 | `schema-only`, `described`, `example` | #19 runs (2026-11-18) | 8 smoke + 594 | — |
| N1 | `v1`, `schema-only` on 9 new documents | — | 108 | 150 |
| S3 | 72, 100, 200 DPI | 150 DPI, 3 new runs | 12 smoke + 798 | 16, 870 |
| S4 | temperature 0, 0.3, 0.5 | the S3 control | 595 | 650 |
| S5 | thinking, budgets 512 and 2048 | the S3 control | 8 smoke + 396 | 12, 440 |
| S7 | the settings of #18 | #19 runs | 199 | 220 |
| **Total** | | | **2,718** | |

The runners of N1 and S3 to S7 (local, `backend/.dev-data/issue-20/`, not in git) share these rules:
- each HTTP request is counted before it is sent; none is sent over the limit;
- after 5 calls in a row with no 200, nothing more is sent; a crash marks the run `error`; nothing restarts by itself;
- a live run needs an approval reference, and a lock allows one live run at a time;
- every answer of the model is written at once (`calls.jsonl`), with the status, the seconds, the usage, the finish reason and the hash of the request. Never a key, a header or an image; error bodies are redacted;
- after a run, the files are searched for the key;
- a replay serves the saved answers again to the current reader, with no API call, and must give identical reports;
- offline tests with a stub endpoint cover the limit, a failing endpoint, a crash, retries, redaction, the lock and the replay.

**Integrity checks before a verdict:** the run is complete; every call got a 200 within the retries; no reader error; the code and the kit are those of the run and of the control; one reader fingerprint; the settings and `config_id` of each run are as planned; the requests of the reports equal those of the runner.

## 4. S2, the prompt

Run on 2026-11-19 against the #19 runs (2026-11-18). 594 requests, all 200 and `stop`.

| Per run (mean; runs) | `v1` (#19) | `schema-only` | `described` | `example` |
|---|---|---|---|---|
| Right of 132 | 112 | 120 (120, 120, 120) | 115.33 (114, 115, 117) | 119.67 (122, 116, 121) |
| Wrong / missing | 8 / 12 | 8 / 4 | 4 / 12.67 | 3.67 / 8.67 |
| Reading group, of 117 | 109 | 117 | 110 | 115 |
| Business category, of 11 | 3 | 3 | 5.33 | 4.67 |
| Strict pass, of 35 | 17 | 25 | 19.33 | 24.67 |
| Fields that change between runs | 2 | 0 | 7 | 8 |
| Seconds per document | 5.45 | 4.40 | 4.66 | 5.43 |

| Comparison | Gain per run | Lowest run > highest control run | Guardrails | Gain, one scenario left out (lowest) | Wins |
|---|---|---|---|---|---|
| `schema-only` against `v1` | +8.00 | 120 > 112 | hold | +5.00 | yes |
| `described` against `v1` | +3.33 | 114 > 112 | hold | +0.33 | yes, narrowly |
| `example` against `v1` | +7.67 | 116 > 112 | hold | +5.33 | yes |
| `example` against `described` | +4.33 | 116 > 117: no | hold | +3.33 | no |

- **Measured:** `schema-only` reads right the 9 reading fields that the control misses (4 booking references, 2 receipt amounts, 1 invoice number, 1 quote total, 1 airline of a photo) and loses none. `described` loses a `total` in 4 documents in some runs; `example` lost all 4 fields of one booking confirmation in one run.
- **The control was of another day.** S7 later measured +2.67 fields per run for the same settings between 2026-11-18 and 2026-11-20; 3 of the fields of the +8 are the fields that move with time. **Estimate:** about +5 to +6 for `schema-only` at the same time; the win of `described` (+3.33) is not robust.
- **The first explanation of S2 was not right.** It said that `v1` "most probably gave null" for the booking references. The answers of S3 to S7 (same settings, answers kept) show the model reading them right and the reader refusing the snippet (section 6). For the S2 runs themselves (no answers kept), the cause cannot be checked.
- **Decision (Nikola, 2026-11-20):** no S2 prompt becomes a default; S3 to S5 use `v1` (N1).

## 5. N1, a safety check with absent values

**Why:** no answer key of the kit expects an empty value, so the kit cannot see an invented value. `schema-only` drops "never guess". The check was designed and reviewed before any document existed; nothing was tuned on it.

**The documents:** 9 new mock documents of one page, made locally with the generator of the kit (`scripts/mockdocs`). Their names and numbers were checked against every text of the kit. Per run: 16 absent fields and 23 present fields.

| Id | Type | Form | Absent fields (kind) |
|---|---|---|---|
| N1 | repair invoice | PDF with text | `invoice_number`, `invoice_date` (plain) |
| N2 | medical invoice | PDF with text | `invoice_number` (lure: a patient number) |
| N3 | repair quote | PDF with text | `total` (plain), `quote_date` (lure: "valid until") |
| N4 | booking confirmation | PDF with text | `booking_reference` (lure: "sent separately by e-mail"), `trip_price` (plain) |
| N5 | cancellation confirmation | PDF with text | `refund_received` (lure: "under review") |
| N6 | medical report | PDF with text | `provider` (lure: only a doctor's name), `treatment_date` (plain) |
| N7 | booking confirmation | scan | `booking_reference`, `trip_price`, `departure_date` (plain) |
| N8 | repair invoice | phone photo | `invoice_number` (lure: a job number), `customer_name` (plain) |
| N9 | receipt | scan | `amount_paid` (plain) |

**The classes:** each absent field, per run, is a right null; an invention dropped by the schema; refused (no snippet, or a snippet that does not hold the value); stopped by the page (the snippet is not on the text layer); or **accepted** (stored). An accepted value is **wrong field** (true text of the page in another field) or **fabricated** (not printed).

**The rules (written in advance):**
1. 0 incorrect values accepted on the 48 absent observations (16 fields × 3 runs).
2. No page lost because of the model, no wrong type.
3. No wrong amount, date or identifier in a present field.
4. No line read on the documents that print no table (N4, N7, N9).

**Results** (2026-11-19, 108 requests, all 200, complete; an independent reviewer recomputed all 234 classes and found 0 disagreements):

| Absent fields, 3 runs | `v1` text pages | `v1` scans/photo | `schema-only` text pages | `schema-only` scans/photo |
|---|---|---|---|---|
| Right null | 6 | 6 | 6 | 2 |
| Refused: no snippet | 0 | 6 | 1 | 3 |
| Refused: the snippet does not hold the value | 5 | 0 | 1 | 1 |
| Stopped: the snippet is not on the text layer | 7 | — | 10 | — |
| **Accepted** | **12** | **6** | **12** | **12** |

| Accepted | Wrong field | Fabricated | Total of 48 | Distinct fields of 16 | Rule 4 |
|---|---|---|---|---|---|
| `v1` | 16 (12 at 0.9 on text pages, 4 at 0.5) | 2 (0.5) | **18** | 6 | pass |
| `schema-only` | 15 | 9 | **24** | 10 | fail (invented lines) |

Accepted by both prompts in all 3 runs:

| Field | Value accepted | Confidence |
|---|---|---|
| N2 `invoice_number` | `P-60417` (the patient number) | 0.9 |
| N3 `quote_date` | `2026-12-15` (the "valid until" date) | 0.9 |
| N4 `booking_reference` | "sent separately by e-mail" | 0.9 |
| N6 `provider` | "Dr. med. Corina Tschudi" (a doctor, not a clinic) | 0.9 |
| N8 `invoice_number` | `J-88213` (the job number) | 0.5 |
| N8 `customer_name` | "Not answerable", or the sender's company (`v1` run 3) | 0.5 |

Only `schema-only`, all on scans at 0.5: N7 `booking_reference` ("Not answerable"), `departure_date`, `trip_price`, and N9 `amount_paid` 0.

- **Measured:** the check of the source stopped every value that a text page does not print (0 of 30 plain fields accepted), even though `v1` gave a right null in only 3 of 15 such cases. Present fields: `v1` 67 of 69 right, `schema-only` 65 of 69 (3 wrong merchants).
- **Interpretation:** the main gap is in the reader, not in the prompt: the check proves that a text is on the page, not that it belongs to the field. "Not answerable" is the model's own null given as a text; on a scan it passes, because its snippet holds it.
- **Limits:** the lures are traps by design, so these are not natural rates; 9 documents and 3 runs that are not independent samples. N6 and N8 `invoice_number` could be argued; without both, both prompts still fail.
- **Next:** issue #125 (the safeguards, and these 9 documents with their generator in git).

## 6. S3, the resolution

**Method:** `v1` at 72, 100, 150 and 200 DPI, 3 runs each, in the rotated order 150, 72, 100, 200 / 72, 100, 200, 150 / 100, 200, 150, 72, on 2026-11-20. The 3 new runs at 150 DPI are the control of S3 to S5. DPI changes the 28 PDFs (the 6 scans are images at 150 DPI); the 7 JPGs do not change.

**The 200 DPI smoke check** (12 requests, all 200, `stop`, JSON):

| DPI | Image sent (px) | Patches of 16 px (computed) | Prompt tokens, classify |
|---|---|---|---|
| 100 | 827 × 1,170 | 3,848 | 1,059 |
| 150 | 1,241 × 1,754 | 8,580 | 2,242 |
| 200 | 1,654 × 2,339 | 15,288 (documented limit 13,312) | 3,361 |

- **Measured:** the prompt tokens of each classify call equal 97 + (width ÷ 32, rounded up) × (height ÷ 32, rounded up) for all 340 other classify calls (252 PDF calls at 72, 100 and 150 DPI, 84 JPG calls, 4 smoke calls). Every one of the 86 PDF classify calls at 200 DPI has exactly 584 tokens fewer (3,264 image tokens instead of 3,848).
- **Interpretation:** the endpoint makes an image above its limit smaller, here to about 1,536 × 2,176 px (about 186 DPI).
- **Open:** which pixels the model sees (a shrink and a crop give the same count).

**Results:** see the table in `EVALUATION.md`. Rules against 150 DPI: 72 DPI −3.33 (fails the reading group and the line items of the clean PDFs), 100 DPI −4.33 (same), 200 DPI −1.00 (guardrails hold; fails the gain). No drift against #19 by the S3 rule (+2.00, the same wrong business values). 798 requests: 5 × 429 and 1 × 502, each solved by one retry.

**Why fields are missing** (each missing or wrong field sent through the checks of the reader, `_json`, `_read` and the steps of `_field`, and judged with `compare_field`; the counts equal the reports):

| Over 3 runs | 72 DPI | 100 DPI | 150 DPI | 200 DPI |
|---|---|---|---|---|
| Outside the scope (damage photos: no page call) | 12 | 12 | 12 | 12 |
| The model returned null | 0 | 0 | 0 | 0 |
| A correct value, refused at the check of the source | 22 | 28 | 18 | 20 |
| An incorrect value, refused | 6 | 4 | 0 | 1 |
| **Missing, total** | 40 | 44 | 30 | 33 |
| Wrong, stored (business-category text) | 24 | 23 | 24 | 24 |
| Line items of the clean PDFs: given / kept | 159 / 130 | 159 / 138 | 159 / 159 | 159 / 159 |

The 10 missing fields per run at 150 DPI:

| Field | Runs | Why the reader refused it |
|---|---|---|
| `damage_description`, 4 damage photos | 3 of 3 | outside the scope (#25) |
| `booking_reference`, 4 booking confirmations | 3 of 3 | the snippet is the title "Booking confirmation" |
| `invoice_number`, `kit-lukas-clean/medical-invoice.pdf` | 3 of 3 | the snippet joins a date and the number (not on the text layer), or is the date only |
| `amount_paid` of the lock set receipt, or the `total` of a quote | 1 per run | the snippet is the label "Total paid", or a LaTeX table |

- At 72 DPI the model misreads characters (`MZ5-2026-3317`, `Z1-2026-0652`, "Miraadouro"); the text layer refuses these values, and one wrong line makes the reader keep no line of its page.
- On a scan or a photo no text layer can refuse a misread value (interpretation); in S3 no incorrect value was stored there beyond the business-category values.

## 7. S4, the temperature

**Method:** 0, 0.3, 0.5, 3 runs each, order 0, 0.3, 0.5 / 0.3, 0.5, 0 / 0.5, 0, 0.3, on 2026-11-20, 2.4 hours after the control. A winner would have been provisional until a confirmation at the same time (396 requests) and N1 at its temperature. 595 requests (1 timeout solved by a retry).

| Comparison | Gain | Lowest run > 114 | Guardrails | Wins |
|---|---|---|---|---|
| 0 | −1.67 | 112: no | fail: more wrong values (8.67 > 8) | no |
| 0.3 | +0.33 | 113: no | "no errors" fails only on the solved timeout; seconds 6.15 against a limit of 6.20 | no |
| 0.5 | −4.00 | 109: no | fail: the reading group | no |

- **Measured:** the model returned null 0 times; the extra wrong value at 0 is a `provider` with its address. The `airline` of a photo was right in 10 of 12 morning S3 runs and refused in 8 of 9 S4 runs, at every temperature.
- **Interpretation:** that field follows the time of day, not the temperature (about 0.9 field per run). Without it no verdict changes. The higher mean seconds come from two slow calls; the medians are about equal.

## 8. S5, reasoning

**Method:** thinking on, `reasoning_budget` 512 and 2048, `max_tokens` = 2048 + budget + 1024 (3,584 and 5,120), so the answer keeps 2048 tokens. Order 512, 2048, 2048, 512, 512, 2048, on 2026-11-20, 3.5 hours after the control. The API reference of the hosted model documents `reasoning_budget` as a top-level integer.

**Smoke check** (8 requests, all passed):
- **Measured:**
  - the thinking text comes in a separate field, `message.reasoning_content`, and `content` holds only the JSON;
  - each page call stopped its thinking at budget − 1 (511, 2047);
  - the completion tokens include the thinking;
  - a page call took 6.6 to 8.9 s at 512 and 18.7 to 22.9 s at 2048.

**Results:** 396 requests, no retry. Rules: 512 −0.33, 2048 −1.00. Both fail the clean line items and the seconds (limit 6.20). 2048 also fails the wrong values (8.67).

| Over 3 runs | Off (control) | 512 | 2048 |
|---|---|---|---|
| Outside the scope | 12 | 12 | 12 |
| The model returned null | 0 | 0 | 0 |
| A correct value refused (of which no snippet) | 18 (0) | 10 (10) | 2 (0) |
| An incorrect value refused | 0 | 5 | 7 |
| A page lost (the answer looped) | 0 | 4 | 10 |
| Wrong, stored | 24 | 24 | 26 |
| Reasoning tokens per run | 0 | 18,013 | 52,993 |

- **Measured:**
  - With thinking, the 4 booking references and 2 other fields that the control refuses are right in every run: the value was already right; the snippet changes.
  - On scans and photos thinking loses fields: values with no snippet, and new wrong values on documents with no text layer ("Receipt" and "Thank you for shopping with us." as an item, "Anna Keller (born 1988-04-12)" as a name).
- **3 answers looped** (`]}  ]}  …` or blank lines) until `max_tokens`, after a thinking that had ended inside its budget. Each page was lost. With thinking off, all 1,980 calls of S2 to S4 ended with `stop`.
- Without the 3 lost pages the gains would be +1.00 and +2.33: still no winner (line items, wrong values, seconds).
- **Open:** whether a prompt could get the snippet gain without thinking; whether the loops depend on `max_tokens` or the temperature (0.2, the model card advises 0.6 with thinking).

## 9. S7, the final run of #18

**Method:** 3 runs of `ISSUE_18` on 2026-11-20, 16:44 UTC. The drift rule of the method was read before the run: no drift when each of the S7 means of right, wrong and missing is within 2 of the #19 totals (112, 8, 12).

| Per run | #19 (2026-11-18) | S3 control (2026-11-20, 11:32) | S7 (2026-11-20, 16:44) |
|---|---|---|---|
| Right / wrong / missing | 112 / 8 / 12 | 114 / 8 / 10 | 114.67 / 8 / 9.33 |
| Strict pass, of 35 | 17 | 19 | 19.67 |
| Fields that change between runs | 2 | 2 | 1 |
| Seconds per document | 5.45 | 4.14 | 4.40 |

- **Measured:**
  - Drift by the S7 rule: right +2.67 and missing −2.67. The S3 rule gives no drift (below 3, the same wrong business values). Against the S3 control the difference is +0.67.
  - Against #19, S7 reads right 3 fields that #19 refused at the check of the source: 2 receipt amounts, 1 invoice number.
  - Against the S3 control, 3 fields move within the same day and cancel out: an invoice number (+3), a lock set amount (+2), the `airline` of a photo (−3).
- 199 requests (1 × 502 solved by a retry); the model returned null 0 times; replay 3 of 3 identical.
- **Interpretation:** the endpoint changes by about 2 to 3 fields per run between periods, on the fields at the edge of the check of the source. The hosted model has no pinned version.

## 10. Checks of the work

- **Methods:** each method (S2, N1, S3, S4 with its confirmation, S5, S7) was committed before the first request of its stage. A rule changed only before results, with the date and the reason.
- **Independent reviews:** each stage had a review that recomputed its numbers from the saved answers; the reviews found no wrong number. They corrected wording: the 200 DPI inference, the S2 null explanation, the `airline` pattern and the drift margin.
- **Replay:** every full live run of S3 to S7 (and the S5 smoke check) replays identically with no API call.
- **No key in a file;** the kit, the answer keys and the earlier results kept the same SHA-256 through every stage.
- **Not in git:** the runners, the analysis scripts and the saved answers (`backend/.dev-data/issue-20/`, with `hashes.txt` per folder). The N1 documents go into git with issue #125, and the multi-page documents with issue #126.
