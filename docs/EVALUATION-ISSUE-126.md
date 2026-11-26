# Issue #126: one call per page against one call per document

The short text is in [`EVALUATION.md`](EVALUATION.md), section "One call per page or per document (issue #126)". This file holds the method. The results come later, in new sections below the method. **The method was written and committed before any request of this experiment.**

## Method (written 2026-11-26, before any request)

No answer of the model to a document of the pilot or of MP1 existed when this text was written.

Code: commit `4a2be6f` on `nikola-work` is the last commit that changes a code file of the experiment. A live run needs a freeze file (`python -m app.docintel.experiment freeze`): it records the commit that is checked out, the SHA-256 of each frozen code file, of each file of MP1 and of this file. The runner refuses a live run when one of them differs, or when the checked-out commit is not the commit of the freeze. The freeze file is local (`backend/.dev-data/issue-126/live/freeze.json`).

Words: **page scope** = one model call per page (the reader of #18 to #125, the default, the **control**). **Document scope** = one model call per document with every page image (the **candidate**). **Logical call** = one call of the reader. **Attempt** = one HTTP request; a retry is a new attempt of the same logical call.

The method has five parts that must not be mixed:

- Part A: the official acceptance criteria (issue #20). Only they give the verdict.
- Part B: how four of these criteria map to the set MP1. The mapping is in force as written; Paul's confirmation is pending.
- Part C: supplementary analyses. They are reported and never change the verdict.
- Part D: safety findings that can block a change of the default. They do not change the verdict of the experiment.
- Part E: rules of integrity and reliability. They say if the experiment is valid at all.

### 1. Question and design

Question of issue #126: on multi-page documents, does one call per document read better than one call per page, by the rules written in advance in #20?

- Two arms, the same reader code, the same settings. Only `OmniSettings.scope` differs: `"page"` (config id `b0f2e2ccab2b`) or `"document"` (config id `54b875de9dfa`).
- Three runs per arm, six runs in **one invocation on one day**, in the order **page, document, document, page, page, document** (tags `r1-page`, `r2-document`, `r3-document`, `r4-page`, `r5-page`, `r6-document`).
- Each run reads the 8 documents of MP1 in the order of their file names, with a new reader and a new HTTP client. So each run classifies every document again.
- The model is asked once per call. Every answer is saved at once (`calls.jsonl`). A replay must give the same six reports with no request.
- **The production default does not change in this experiment.** `OmniSettings()` has `scope="page"`. A verdict "the candidate meets every official rule" is an input for a decision of Paul, not a change of the default.

### 2. Fixed settings (the settings of #18)

| Setting | Value |
|---|---|
| Prompt family | `v1` |
| Resolution | 150 DPI |
| Temperature | 0.2 |
| Reasoning | off (`chat_template_kwargs.enable_thinking: false`), no budget sent |
| Maximum output tokens | 2048 (`max_tokens`), per call, in both scopes |
| Timeout | 90 s (the timeout of the HTTP client, for each phase of an attempt) |
| Retries | 2 after a timeout, a 429 or a 5xx; waits 2 s then 8 s, or `Retry-After` up to 30 s. A 4xx other than 429 and a 200 with no content are final. |
| Structured output | `response_format` `json_schema`, `strict: true` |
| Pacing | none (`--pace` is not passed) |
| Fallback | none: no second call after a failed or unusable document call, no higher token limit |
| Model and endpoint | `VLM_MODEL` and `VLM_BASE_URL` of the environment. Expected: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at `https://integrate.api.nvidia.com/v1` |

Fingerprints:

| Item | Value |
|---|---|
| Config id, page scope | `b0f2e2ccab2b` |
| Config id, document scope | `54b875de9dfa` |
| Prompt SHA-256, page scope | `e8096cae4ff9aa0d4a95bc1c00070f88d6b9a38753c384d1ed10abd6814e0d9a` |
| Prompt SHA-256, document scope | `0c6895e9c62f50c0156e8b717afc0bebe78d95d512c1ce1587c4af5b1331ebb2` |

The two extraction requests differ only by what the scope needs. A report must say "the same settings", never "the same request":

1. Several images in one request, with the text part `Page k of N:` before each image, in page order.
2. Another prompt text ("Read every image. They are the N pages of one <type>"; one entry per image; "that page" for "this page"). The rule sentences are those of `v1`, word for word.
3. Another answer schema: one block per page under `page_1` ... `page_n`.
4. One limit of 2048 output tokens and one timeout for the whole document.
5. The unit of failure is the document, not the page.
6. On a scan, the page of a field is a claim of the model.

The classify request is the same in both scopes. A document of one page takes the page request of #18 in both scopes (MP1 has none).

### 3. The set MP1 (frozen)

8 documents, 19 pages (14 with a text layer, 5 image-only), 6 text PDFs and 2 scans, 35 scored fields = 33 present + 2 absent, 12 fields with more than one accepted source page, 39 line items = 35 on text PDFs + 4 on a scan. Mock data only. Folder `data/mock/mp1/`.

SHA-256 of the 17 files:

```
c9807b3b02151650e4006aba1c74468e30c8c663f0319682a49db3c02bb85b84  manifest.json
3c2bf913c5a5fd5bc8653ab86f64635b159bed86e7ddec97fce70651866f5c50  mp1-01-repair-invoice-carried-forward.expected.json
1438b192851134bb0e520162b2c8a59413d8fd665bdc4bbf7119ec1737d00063  mp1-01-repair-invoice-carried-forward.pdf
5379e397d256e4460c09612d911f276cfccc7add82da0b0686c26e853dbed2e1  mp1-02-medical-invoice-continued.expected.json
cc65a004191657639521c3b8a21e687a62b38fc983959976a45c2ade8306d42a  mp1-02-medical-invoice-continued.pdf
31382d4054aea1fba4cda01dc16c0239e1eaaa45debddf1b7617e20d5c1a98c3  mp1-03-cancellation-confirmation-scan.expected.json
b54813db72d02981437064d6d1ad8b883c60da874cb68e7b3bc1f93bd3de6dec  mp1-03-cancellation-confirmation-scan.pdf
41c5e6b81fef11cc816d1b5342c0087d3d55d13bcd32f6e1fd656b1e1f475a7f  mp1-04-repair-quote-terms.expected.json
d1f76082e1804117e3c83770d7b8d11124e3d9377634f209c49df18194301002  mp1-04-repair-quote-terms.pdf
b93a60d2baa83d4c1c63dce8e9ab800e921243219f1f9c615a92db821eef3f7b  mp1-05-repair-invoice-service-rows.expected.json
3521ec73a7fe2d7ede13c39fe13d612555e0f5939a750dd73527580c75406a36  mp1-05-repair-invoice-service-rows.pdf
8ff6118d8aea726874eb49a8d6b8a0b55bddff625a1ef7d366243eaaf63dd968  mp1-06-medical-report-scan.expected.json
752739a243eafcbc6968b86091a484c21ef1ee5473a87bce9013d4462c8c5984  mp1-06-medical-report-scan.pdf
8f7ad32970d3dd7ea863b4f451bd5718b4004d6052946febebed50c3ad96c3cb  mp1-07-booking-confirmation-no-price.expected.json
8709d7f229b61055454d0744b39106a0148eb1954b6fb6bb2ea511c5f6f78e0d  mp1-07-booking-confirmation-no-price.pdf
5956fa6b949d7ac31bd73feef5c152bccb48f912d7adf57e2eebfa11ecdef335  mp1-08-police-report-two-dates.expected.json
5677ef9bcc7b7251ea6055470a82845e75e195bb92e5ea83b7ec1b9549417963  mp1-08-police-report-two-dates.pdf
```

The set does not change. No document of MP1 is sent before the main run. The pilot uses two other documents.

### 4. Requests and caps

| | Page scope, per run | Document scope, per run | Total |
|---|---|---|---|
| Planned logical calls (classify + extraction) | 8 + 19 = 27 | 8 + 8 = 16 | 3 x 27 + 3 x 16 = **129** |

- **Hard cap of the main run: 145 HTTP attempts.** Every attempt is counted before it is sent: classify, page, document, every retry, every failure. Attempt 146 is never sent.
- **Pilot: 4 planned logical calls, hard cap 8 HTTP attempts** (section 5).
- The caps are constants of the runner and part of the freeze. They do not change.

### Part A. Official acceptance criteria (issue #20, unchanged)

Source: [`EVALUATION.md`](EVALUATION.md), "How a variant was judged (the rules written before the runs)". The evaluator (`backend/app/docintel/acceptance.py`) computes each rule as the analysis script of #20 did. Means are exact fractions over the 3 runs of each arm. The words in quotes are the text of #20; the words after a quote say how it is computed.

The candidate replaces the control **only when all of these are true**:

1. "It gives at least 3 more right fields per run." Mean right fields of the candidate minus mean of the control >= **3**. A right field is a right value or a right absence (35 scored fields per run).
2. "Its lowest run is higher than the highest run of the control."
3. "The guardrails hold":
   - a. "no wrong amount, date or identifier": computed as 0 in the 3 runs of the candidate, at any confidence;
   - b. "no more wrong values than the control": the means;
   - c. "the reading group loses 1 right field per run at most";
   - d. "classification loses 1 document per run at most";
   - e. "every line item of the clean PDFs stays right";
   - f. "no reader error, and no call left without an answer";
   - g. "at most 1.5 times the seconds per document of the control, or Paul accepts the cost". The seconds of a document are the time of its classify and extract calls, the waits of retries included; the value of an arm is the mean of its 3 run means. The evaluator reports met or not met. When this rule is the only one not met, the computed decision is still "the control stays"; an acceptance of the cost by Paul is a separate written decision, reported next to the verdict.
4. "The gain stays above 0 when any one scenario is left out."

Also from #20:

- "No verdict when a run is incomplete, a call got no answer after the retries, or a check of integrity fails (code, kit, settings, model)."
- "A call answered after an allowed retry is a success, and the retry is reported."
- "When two variants win, the larger gain wins": not applicable, there is one candidate.
- When the candidate does not meet every rule, the control stays (the result of #20 itself: no variant won, and the settings of #18 stayed).

**The official threshold of rule 1 is 3 fields. It is not a question and it does not change.**

### Part B. Mappings to MP1 (in force; Paul's confirmation is pending)

Four rules of #20 name a count or a term of the 35-document kit, or need a definition for a document answer. The evaluator uses the mapping in the second column. The official verdict is stated under these mappings and is provisional until Paul confirms them. For each one, the one other reading that Paul could choose is written here now, before any answer exists.

| Rule | Mapping in force (what the evaluator computes) | Other reading, written in advance | Pending |
|---|---|---|---|
| 3e | All **35 line items of the 6 text PDFs** are right in **every** run of the candidate (the kit had 53). The 4 lines of the scan are not in the rule and are reported apart. | Relative: the lowest run of the candidate has no fewer right text lines than the highest run of the control. | P1 |
| 4 | MP1 has no scenario: the gain stays above 0 when **any one document** is left out. | none | P1 |
| 3c | The groups of #20, by document type and field: 31 reading fields and 4 business-category fields on MP1 (`medical_invoice.provider`, `medical_report.diagnosis`, `medical_report.provider`, `police_report.offence`); no damage photo. | none (it is the definition of #20) | with P1 |
| 3f | An **unusable answer** is an extraction answer with status 200 that is cut (finish reason `length`, also when its text is whole JSON), empty, not JSON, or not in the asked form. The asked form: a page answer, and each block of a document answer, is an object with the objects `values` and `snippets`; a document answer has the keys `page_1` to `page_N` and no other key. An unusable answer of the **candidate** counts as "a call left without an answer": **the run stays valid**, the numbers are reported, and the candidate cannot pass. An unusable answer of the **control** gives **no verdict** (rule 3f judges the candidate only, and such an answer would lower the score of the control). | The reading of #125: an unusable answer in either arm makes the run invalid, so there is no verdict. | P2 |

Rules for this part:

- P1 and P2 are open for the **main** run. They do not affect the pilot: the pilot gives no verdict.
- Under both readings of 3f an unusable answer never lets the candidate pass. The readings differ only between "the control stays" and "no verdict".
- A block that has the asked form and whose values fail the schema is not an unusable answer. The reader treats it as it treats a page answer of #18: it drops the failing keys, or it counts the page as not read. The fields then count as missing or stay below 0.8.
- The numbers that the other readings need are in `verdict.json` (the right text lines of each run of both arms; the unusable answers of each run). No new code is needed to state them.
- If Paul chooses another reading **before the first live answer of the pilot**, the code, this text and the freeze are made again, and the change is in git.
- **After the first live answer of the pilot, no acceptance criterion changes.** If Paul then chooses the other reading of a row above, the verdict of the mapping in force is still reported as the result of this method, and the other reading is reported next to it with the date of his decision. No reading that is not in this table can be added after a live answer.

### Part C. Supplementary analyses (reported, never the verdict)

None of these enters the decision. In the evaluator, the decision is made from the official rules only. None is a reason to change the default. Items 1 and 2 are in `verdict.json`. Items 3 to 6 are read after the verdict from the saved run reports and the record of the requests, by a script that does not write `verdict.json`.

1. **Thresholds of 1 and 2 fields** for rule 1: for each, whether rule 1 would be met and whether every rule would then be met.
2. **Five proposed safety gates** (from #125, not approved): (1) no candidate run has more wrong values at 0.8 or more than the highest control run; (2) no value at 0.8 or more for an absent field; (3) no value at 0.8 or more on a page that is not accepted; (4) the share of stored values on an accepted page is not below the control, text PDFs and scans apart; (5) no placeholder stored. Also reported: the fields right at 0.8 or more in every control run and not in every candidate run.
3. **Accepted source pages:** `accepted_page_accuracy`, `verified_accepted`, `pass_rate_accepted`, `wrong_page_trusted`, `unaccepted_page_trusted`, `right_untrusted`. The official source-page number stays the key page.
4. **Per document and per kind:** each number for the 6 text PDFs and the 2 scans apart; right fields per document in each run; on scans, the page that each arm gives for each field.
5. **Cut answers:** the right fields per run with and without the documents whose document answer has the finish reason `length`.
6. **Cost:** tokens (prompt, completion, cached, as the endpoint reports them), seconds per document (mean, median, lowest, highest), requests and retries.

A count that needs a confidence and has none is "not available". A token count that the endpoint did not report is missing. Neither is ever 0.

### Part D. Safety findings that can block a change of the default

These are not part of the comparison and do not change its verdict. A verdict that passes is necessary for a new default and not sufficient.

1. **A document with no field.** A document that gives no field looks like a document that prints nothing (D80 (13), reported in the text of pull request #121). When the settlement of the claim does not read an amount from that document (a medical certificate, a medical report, a police report, a luggage report), the claim can be paid with no person. It exists today in the page scope. The document scope makes it more likely and adds paths (a cut answer loses every page). Offline tests pin it: `backend/tests/test_scope_downstream.py`, the tests that end in `_gap`. It must be fixed in the base, or assessed in writing by Paul, before the document scope can be a default. A fix changes files that the freeze holds, so it lands after the main run, or the freeze is made again before the first live answer.
2. **Page claim on scans.** In the document scope the page of a field of a scan is a claim of the model (D80 (1) chose the page scope for this reason). Paul decides if this is acceptable for a default.

The evaluation reads documents only. It builds no claim and calls no routing, settlement or payment function. So these findings do not make the evaluation unsafe.

### Part E. Integrity and reliability rules

**Two separate answers.** Was the procedure valid? Did the candidate meet the rules? An invalid procedure gives "no verdict" and the candidate is not judged.

| Kind | Examples | Procedure | Candidate |
|---|---|---|---|
| Infrastructure | in either arm, a call with no 200 after the allowed retries (timeout, 429, 5xx) or a 4xx | **no verdict** | not judged |
| Integrity | the run stopped (cap, failing endpoint, crash, lock, a secret in a file); a run report is missing; the report and the record count other requests; the settings are not those of the scope; the six runs did not read the same documents and keys; another reader code within the invocation; the record file is cut; more attempts than the cap; an unusable extraction answer of the control | **no verdict** | not judged |
| Caused by the scope | an unusable extraction answer of the candidate (Part B, 3f) | valid | rule 3f not met: **the control stays** |

A classify answer with a 200 and no known type is a classification error (rule 3d). The reader then asks for the type once more before the extraction. The procedure stays valid.

**Cut answers.** The finish reason of every answer is recorded as the endpoint sent it, in `calls.jsonl` and in the counts of the report. An answer with the finish reason `length` is a cut answer **also when its text is whole JSON that the reader can parse**. In the document scope the reader then stores its fields below 0.8 and no line item. These fields are scored like any stored field, so they can be in the right fields of rule 1. The answer is still an unusable answer for rule 3f, so the candidate cannot pass with it. Part C (5) reports the numbers without these documents. In the page scope the reader does not read the finish reason; a cut page answer of the control gives no verdict (Part B, 3f).

**Stops.** The cap is reached. Five logical calls in a row got no 200: the next call is not sent. Any exception. A secret is found in a written file. A lock file exists. After a stop nothing is sent, and **nothing restarts by itself**.

**One invocation, one day.** The six runs are one invocation. Its start and end (`meta.json`, UTC) must have the same date; do not start within one hour before midnight UTC. An invocation that stopped is reported as incomplete. Runs of two invocations are never joined. A new invocation is all six runs, with a new authorization.

**The printed decision is provisional until a person checked these points.** The runner prints a decision and writes `verdict.json` at the end of the run. The result is **no verdict**, whatever `verdict.json` says, when one of these fails: the model and the endpoint in `meta.json` are those of section 2; the dates; `pace_seconds` is 0; `replay` gives the saved reports; the record holds no key. The person writes the check in the report.

**Secrets.** No header, key, image or prompt is written. A secret in an answer is replaced before it is written, and the output folder is scanned for the key at the end. The key enters only through the environment (`--env-file ../.env`); it is never an argument.

**Before a live run starts** (`preflight`; nothing is sent before it passes): the three environment variables of the reader; the authorization text of this mode, followed by a name and a date; the freeze file, with every hash equal (code, MP1, this file), the same commit, no uncommitted frozen file, the same plan, caps and order; a new output folder; no lock.

**Not in the freeze:** the versions of the Python packages. They are in `backend/uv.lock`, which is in the commit. The record holds the SHA-256 of every image sent, so another renderer would show.

### 5. The pilot

**Purpose:** to learn if the hosted endpoint takes the request of the document scope at all. It is technical. It gives no accuracy number and no verdict. It starts nothing: the main run is another command with another authorization, and the main run does not read the pilot.

**Documents:** two mock documents made by `scripts/mockdocs/pilot.py` into the output folder of the run. They are not in MP1, N1 or the kit.

| File | Type | Pages | Kind |
|---|---|---|---|
| `pilot-01-medical-invoice-two-pages.pdf` | medical_invoice | 2 | text PDF |
| `pilot-02-repair-quote-scan-three-pages.pdf` | repair_quote | 3 | scan |

**Requests, in this order** (one reader, `scope="document"`, the settings of section 2):

| # | Call | Images |
|---|---|---|
| 1 | classify document 1 (page 1) | 1 |
| 2 | document call, document 1 | 2, in page order |
| 3 | classify document 2 (page 1) | 1 |
| 4 | document call, document 2 | 3, in page order |

4 logical calls when every call gets a usable answer at the first attempt. **Never more than 8 HTTP attempts.** A retry, or a second classify call after a classify answer with no known type, uses the spare attempts. The cap stops the pilot before attempt 9.

**Technical checks** (per document; all must pass):

1. The classify call got a 200 (the last classify call for this document).
2. Exactly one logical document call holds every page image in page order (a retry is not a second call).
3. It got a 200.
4. The finish reason is `stop`.
5. Prompt and completion tokens are reported.
6. The prompt tokens are at least one token per block of 32 x 32 px of each image sent (2,145 for each A4 page at 150 DPI): no image was dropped. This is a lower bound.
7. The completion tokens are at most 1,536 (75 % of 2048).
8. The answer is JSON with exactly one block for each page.
9. Every block is a valid page answer for the type of the answer key (the validation of a page answer of #18).

**Reported, never a reason to pass or fail:** the type that the model gave and if it is right; the fields stored and how many are on an accepted page; the line items stored; cached prompt tokens; seconds.

**What follows from the result** (written in advance):

| Result | What follows |
|---|---|
| All checks pass | The request form is confirmed. The main run can be authorized, as frozen. |
| The document request is refused (4xx) | The pilot fails. Another request form is a code change: a new commit, a new freeze, a new authorization. |
| The finish reason is `length`, or more than 1,536 completion tokens | The pilot fails. `max_tokens` stays 2048. Nikola decides if the main run is still useful. |
| The prompt tokens are below the image tokens | The pilot fails: an image was dropped or made smaller. No main run until this is understood. |
| Another check fails (1, 5, 8 or 9) | The pilot fails. A person reads the record. No main run until the cause is understood. |
| A stop (cap, failing endpoint) | The pilot is incomplete. A new pilot needs a new authorization. |

**What the pilot never does:** it changes no prompt rule, no answer key, no acceptance criterion, no cap. Its answers are not used to judge quality and not used to tune anything.

### 6. What can change, and when

- After the freeze and before the first live answer: a change needs a new commit, a new freeze, and a note of the reason.
- After the first live answer of the pilot: only a change of the request form that the pilot proves necessary, with a new commit, a new freeze and a new pilot. No acceptance criterion, no MP1 file, no setting of section 2, no cap.
- After the first live answer on MP1: nothing.
- This file is part of the freeze. It is not edited between the freeze and the end of the main run. The results are added after it; the frozen text stays in the git history at the commit of the freeze.

### 7. Commands

All from `backend/`. The two live commands need the exact authorization text, with a name and a date after it. Do not pass `--pace`.

```bash
# offline, no request, no key
uv run python -m app.docintel.experiment dry-run pilot --out .dev-data/issue-126/dry/<name>
uv run python -m app.docintel.experiment dry-run main  --out .dev-data/issue-126/dry/<name>

# once, after the last commit: what a live run is held to
uv run python -m app.docintel.experiment freeze \
    --out .dev-data/issue-126/live/freeze.json --method ../docs/EVALUATION-ISSUE-126.md

# LIVE, the pilot: 4 planned calls, at most 8 attempts
uv run --env-file ../.env python -m app.docintel.experiment pilot \
    --out .dev-data/issue-126/live/pilot-1 \
    --freeze .dev-data/issue-126/live/freeze.json \
    --authorize "issue126-pilot-4-requests-cap-8 <who> <date>"

# LIVE, the main run: 129 planned calls, at most 145 attempts. A separate decision.
uv run --env-file ../.env python -m app.docintel.experiment main \
    --out .dev-data/issue-126/live/main-1 \
    --freeze .dev-data/issue-126/live/freeze.json \
    --authorize "issue126-main-129-requests-cap-145 <who> <date>"

# offline, after a main run
uv run python -m app.docintel.experiment replay  .dev-data/issue-126/live/main-1
uv run python -m app.docintel.experiment verdict .dev-data/issue-126/live/main-1
```

Exit codes: 0 done (for the pilot: every technical check passed; for the main run: the procedure is valid). 2: the preflight refused the run and nothing was sent. 3: the run stopped, the pilot checks did not all pass, or the files give no valid experiment.
