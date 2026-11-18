# Lessons

## Spike: LangGraph agent in OpenShell (issue #13, 2026-11-12)

- Pin `mcp` to a 1.x version. `pip install mcp` now gives 2.3.0, where `FastMCP` is gone. `langchain-mcp-adapters` 0.3.2 needs `mcp<2`.
- `openshell sandbox exec` does not pass the `ENV` of the image. Set non-secret variables with `--env` on `sandbox create`.
- The model writes identifiers with a special hyphen (U+2011) in some runs. Never match a policy number in the answer text. Read it from the tool result or the gateway event. In tests, normalise the hyphens.
- A Python MCP server (FastMCP) refuses a Host header it does not know (HTTP 421). The sandbox calls `host.openshell.internal`: list that name in `allowed_hosts`.
- In a shell script, `set -o pipefail` with `grep -q` gives false failures: `grep -q` closes the pipe early.
- The first `docker pull` of a base image took 4.4 minutes on the slow wifi. Pull the image before you need it, and build the agent image on the machine that runs the gateway.

## LangGraph agent (issue #47, 2026-11-12)

- `ChatOpenAI(max_tokens=...)` sends `max_completion_tokens`. The NVIDIA endpoint accepts it. mypy knows the field only under that name.
- A tool call with arguments that are not JSON does not come in `AIMessage.tool_calls`. It comes in `invalid_tool_calls`. Answer it with a `ToolMessage` error like any other bad call, or the history is broken.
- Keep the tool calls of a turn inside the node. If they go in the graph state, every checkpoint and every later prompt grows with them. The state keeps the conversation only.
- A graph that ends after each turn and starts again from `START` with a router on the saved `step` is enough for a chat. A failure in a code node leaves the step in the checkpoint, so the next message resumes there. No `interrupt()` is needed.
- Do not put the enforcement in a tool that tests and the scripted agent also call directly. Put it in the `call_tool` of the model agent, and make the MCP server call the same function.
- `position: sticky` in a scroll container with padding sticks at the content edge, not at the scrollport edge. Use a negative `top` equal to the padding, and add the same padding inside the sticky box, or the content scrolls in the gap above it.
- The shell wins over `.env` for the live tests. The `.env` of this machine can hold an older `AGENT_MODEL`: pass the model on the command line.

## Sandbox mode (issue #14, 2026-11-12)

- A `protocol: mcp` endpoint in the policy governs the whole host and port, not only its `path`. A request with no `MCP-Protocol-Version` header gets 403 `mcp_protocol_version_not_allowed` on any path, `/health` included. To prove that another path is denied, send the header: then the answer is 403 `policy_denied` with the rule `GET /health`. A probe that forgets the header proves nothing.
- The schema of a tool is what the model sees. The code nodes of the graph send arguments the model must not see (`agent_state` to `update_claim`). The MCP SDK checks the arguments against the schema by default (`call_tool(validate_input=True)`), so the code nodes would fail. Switch it off and let the tool function refuse unknown arguments.
- The MCP client of the SDK asks `tools/list` again after a `tools/call` (it reads the output schema). The policy needs `tools/list` for that, and the log then shows `tools/list` after the call.
- A placeholder in the comment of a template is replaced too. A header comment that named `@TOOL_RULES@` made the rendered YAML invalid. Name the places in words in a comment.
- uvicorn writes the path of every request in its access log. A token in the path is then in the log: filter the record (`RedactToken`). The OpenShell log of the sandbox holds the whole path as well.
- Do not send a key into a sandbox to look for it. A real NVIDIA key has the form `nvapi-...`: look for that form in the environment and the files.
- A test that calls the API of a backend in `sandbox` mode starts a sandbox on each greeting and each upload, and the new sandbox gets a new token. A test that made a token by hand must store its documents and its policy without a turn of the agent.
- `ruff format` does not wrap a long string, so `E501` stays. The `timeout` command does not exist on macOS, and BSD `sed -i` needs an argument: use Python for an edit in a script.

## Sandbox panel (issue #15, 2026-11-12)

- `openshell logs` is about 0.3 s late: a read right after the request does not hold its lines yet. Read again until the lines you expect are there, with a limit.
- Do not place the events of a process by counting back from its end. The process takes about 0.2 s to exit and `openshell` to return. Use the clock of the process and keep the result inside the time you waited.
- A function that cuts a secret and adds a mark (`/mcp/ab12cd…`) must also match the mark, or a second run adds a second one. Test it twice.
- The test of non-negotiable 1 reads all string literals of the backend. A new feature that needs `denied` for another meaning needs a named exception in that test.
- One refused connection gives two OpenShell lines: the DNS refusal (`NET:REFUSE`), then the connection (`NET:OPEN`). A non-DENIED line (`WARN`, `ALLOWED`) can talk about the same host: keep only the OCSF lines with `DENIED`.
- A JSON line from a sandbox is data. Judge a probe in the backend (status, error name) and accept a placeholder only if it matches its form: a lying sandbox then cannot show a key.
- The setup script builds the image from the graph files: run it again after a change of `sandbox_turn.py` or the graph, or the sandbox runs the old code.
- A shared pytest fixture (`make` in `tests/rig.py`) is imported once in `conftest.py`. Never name a test argument like an imported fixture in the test file.


## Document kit (issue #23, 2026-11-13)

- C003 (outside travel) compares the claimed amount with the first `total` field of the claim, so a receipt must not call its amount `total`: it uses `amount_paid`. A quote is `quote_number` and `quote_date`, never `invoice_date`, or R005 would read it.
- One message can hold the story, the date and the amount: the scripted agent fills the three slots from it. The kit stories are one line each.
- The expected signals of a story are checked against the real rules, not computed by hand: a threshold story (60 points for `investigation`, 20 for a review) breaks the day somebody tunes the points. The test names the story that moved.
- reportlab keeps a JPEG as it is (DCTDecode) when `drawImage` gets a JPEG file or an `ImageReader` of one: the scan PDF is a plain page with one image, no text layer, and the bytes of the JPEG are the ones Pillow wrote.
- pdfium does not embed the standard fonts of the reportlab PDFs: it draws a substitute that depends on the machine. A pixel test of a scan must be coarse (reduced to 1/8, mean grey level): two different documents differ by about 11 on that scale, a Mac against Linux by far less.
- The titles and stories of `/demo/kit` are read by a visitor in the customer panel (non-negotiable 2): never write "fraud", "tampered" or "edited" there. The seeded signals live in `index.json` only, which no route serves.


## Deploy on the Brev VM (issue #16, 2026-11-13)

- A `umask 077` in the shell that runs `infra/sandbox/setup.sh` gave an image where the agent user could not read `/app/app/agent`: every turn failed in less than a second with the fallback message and no network line in the sandbox log. The script now makes the build context readable (`chmod -R a+rX`). When a turn fails with no denial in the log, run `python -m app.agent.sandbox_turn` by hand with `openshell sandbox exec`.
- The NemoForge recipe works unchanged on Ubuntu 22.04 with the GCP kernel 6.8: write `gateway.toml` before the OpenShell installer starts the gateway.
- `/claims/{id}/events` is a stream: a `curl` on it never ends. Use `/claims/{id}` to read the state in a script.
- `pkill -f <text>` over SSH kills its own shell when the text is in the command line.
- A Brev VM has no public SSH port: `brev port-forward` or `ssh -L` through the Brev host is the only way in. Good for us, the app has no login.


## Public URL for the demo (issue #65, 2026-11-13)

- The Brev CLI cannot open a port: only the Brev console can. Plan a step for a person. Use its "HTTP Ports" (an HTTPS URL on `gobrev.dev`, with a Brev login until you share it as public), not a raw port: no certificate to manage.
- Caddy asks Let's Encrypt for a certificate as soon as it starts. With ports 80 and 443 closed, each try fails and counts against the limit of Let's Encrypt: start Caddy only when the ports are open.
- The pipeline panel uses server-sent events (`EventSource`): check that a tunnel or a proxy supports them before you choose it.
- The Brev HTTP port does not reach `127.0.0.1`: a service bound to the loopback gives a 503 from Brev. Listen on every interface and check that the raw port stays closed from outside.
- `vite preview` refuses a Host name that it does not know (403). The proxy in front rewrites the Host header, so the frontend config does not hold the public name.

## Claim lifecycle (issue #72, 2026-11-16)

- The chat step lives in two places in `nim` mode: on the claim (`agent_state`) and in the checkpoint of the graph (`step`). When trusted code changes the first, the second is stale. The claim is the truth: the backend corrects the step in the input of the turn.
- A new event with the actor `workflow` breaks every test and view that reads the node events. A new kind of event gets its own actor (`system`).
- A frontend test that checks "the button is enabled" passes before the data is loaded. Wait for the load first, and check that the test fails without the feature.


## Restart of the VM (2026-11-16)

- Test a restart of the VM before you say that a deploy is done. Ours left the backend down for one hour, and the page still loaded: the frontend and the password door hide a dead API.
- Read the unit of a service before you order your unit on it. The OpenShell gateway unit says `After=default.target`: an `After=` on it from a unit that `default.target` wants is a cycle, and systemd breaks a cycle by deleting one start job with only a line in the journal.
- For "start after the other service is ready", prefer a restart loop (`Restart=on-failure`, `RestartSec=5`, `StartLimitIntervalSec=0`) to an ordering: an ordering waits for the start of the process, not for the service to answer.
- A network can block the SSH port of Brev (a high port on the Brev relay) while `brev login` and `brev ls` still work, because they use port 443. Test the port with `nc` before you look for another cause. A way around it: a machine of your own on another network as an SSH jump (`ssh -J user@machine claims-nvidia`), for example through Tailscale. The key stays on your computer.

## A model that must compare two lists (issue #78, 2026-11-16)

- When the model must compare two lists (the required documents and the uploaded documents), it can get it wrong, and it uses many reasoning tokens. Do the comparison in the tool and give the result (`missing`). A model finds facts, code computes: this holds for small things too.
- A fault of this kind hides while the conversation is in memory: the model repeats what it said before. It shows when the agent starts with no conversation (a new sandbox). Test a turn with an empty memory.

## A prompt change is a change of behaviour: measure it (issue #98 and its fix, 2026-11-17)

- Two lines about tone in the prompt that both model nodes share made the intake node fail 3 times in 19: the model wrote to the customer before it saved the facts. Before the change: 20 of 20. The three live tests passed, and so did every test with the fake model: they do not see a failure that comes 1 time in 6.
- After a change of a prompt, play the same input 10 to 15 times with the real model, before and after, and compare the counts. One good run proves nothing.
- Put a new instruction in the prompt of the one node that needs it, not in the shared part.
- A rule added to repair the failure ("a date in the story is the date of the incident") did not help: 9 of 10. Find the line that causes the failure before you add a line.
- Keep the code before the change to measure it: a `git worktree` of the old commit gives the baseline with the same script.

## Several visitors at once (issue #115, 2026-11-17)

- The first test with three claims at the same time found a 500 in one minute. Every test before it played one claim at a time. Test with several users before a demo with several users.
- A store with one SQLite connection needs the lock on every read, and `fetchall()` inside the lock. A read that returns the cursor and walks it later, outside the lock, mixes the rows of two requests. Two reads had no lock: the two oldest ones.
- Without the lock the fault is not always an error: under load the requests can also block. A test for a race needs a time limit around it.
- After the store, the next limit was the key of the model (issue #117): at five visitors, 21 answers 429. A retry must be as long as the window of the limit: the limit is per minute and the client retried for 1.5 seconds. Measure before and after: the correction gave 5 of 5 claims in place of 3 of 5, and it made the run two times slower.

## Evaluation harness (issue #19, 2026-11-18)

- Compare a value by the type of its field, not by the form of the value. The first rule (`compare` of the bench) scored `"1,840.00"` against `1840.0` as wrong, and `"001840"` against `"1840"` as right: an identifier read as a number loses its zeros.
- The same total in three runs does not mean the same reading. The three Omni runs gave 112 of 132 fields each time, but 2 fields changed between the runs and cancelled out. Compare field by field before you say that two runs are equal.
- Record what makes a run comparable from the first run: the settings that the requests really held, and a fingerprint of the reader code. The first Omni report was written before the fingerprint existed, and nothing in the file can prove its prompt later.
- `test_no_auto_rejection.py` reads every backend file with the encoding of the platform. A non-ASCII character in a backend file (a typographic dash in a string) fails it on Windows only. Write such characters as `\u` escapes.
- A reader that never raises hides an outage: with every request refused, its fields are only "missing". Count the HTTP answers next to the fields (`http` in the report), or an outage looks like a weak model.
