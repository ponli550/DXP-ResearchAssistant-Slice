# Demo script

A rehearsal order and a prompt list for the MARC meeting. Every answer shown is real: the model named in `.env` answers, the gate checks, refusals stay refusals. The only preparation is a seeded database, built before the meeting through the same pipeline, so that history can be shown in seconds and one or two questions asked live.

## Before the meeting

```
set -a; . ./.env; set +a                         # HERMES_API_KEY, HERMES_MODEL=qwen/qwen3.8-27b
uv run --python 3.14 python scripts/seed_demo.py data/demo.db     # sixteen model calls, a few minutes; safe to rerun, it resumes
uv run --python 3.14 python -m ra --db data/demo.db serve --port 8765
```

Open three tabs: the chat at `/?user=alice`, alice's board at `/dashboard?user=alice`, the admin board at `/dashboard#token=demo`.

Rules for the live part. Qwen answers about half of the single-report questions in the set and refuses every comparison; a question typed on the spot is a coin flip. Ask live only from the "safe live" list, and show everything else from history, where the answer is already stored and opens without a model call. If a live question is refused, that is the product working: say so, and open the stored answer to the same question from the board.

## 1. The answer (chat, alice)

**Safe live** (Qwen answered these in the saved run and again in rehearsal; still ask one, not five):

- What GDP growth is projected for Malaysia in 2025 in the October 2025 monitor?
- What growth did the October 2025 monitor give for Malaysia in 2024?
- How much did Malaysia's economy grow in the first half of 2025?
- What was Malaysia's real GDP growth in the fourth quarter of 2025?
- What forecast did the October 2023 monitor give for Malaysia's growth in 2024?

What to show on one of them: the canvas builds the answer in steps, sources then checks then the answer; "Show the quote" under the claim; click the citation chip to open the page with the quote highlighted; the Investigation steps tab for the same answer, with the retry and its reasons if there was one; thumbs down with a reason; "Create review request".

**From history** (open from alice's board, "open" on the row):

- The analysis questions: what drove growth in 2025, the downside risks, inflation. Show what the model found and what the gate removed.
- The comparison: how the view of 2025 growth changed between October 2025 and April 2026. Qwen refuses it. Say that the two larger models answer it with one true side, and that both-sides-or-refuse is the week 4 to 5 item.

## 2. Refusals (chat, alice)

Safe live, any of these; each returns "not found" with the closest pages and no invented figure:

- What will Singapore's minimum wage be in 2029?
- Who is the chief executive officer of PETRONAS?
- What growth does the World Bank forecast for Malaysia in 2031?

The point to make: three models, nineteen runs, 285 answerable attempts, no false figure stated as fact. The gate removed up to six in ten of the larger models' drafts, one of them a number read off a chart's axis.

## 3. Access (chat, switch the user to bob)

- What does the internal memo say about the coverage review?

bob holds no grant on the internal library, so the memo is never retrieved for him and the answer is "not found", with closest pages only from the World Bank series. Switch back to alice and open the same question from her history: she has the grant and gets the memo. Access is enforced inside retrieval, before any model call; the trace never names a library the user cannot see.

## 4. Integration (terminal, beside the admin board)

Create a key on the admin board's Tools tab for user alice, named "MARC Online portal", then:

```
curl -s -X POST http://127.0.0.1:8765/api/ask \
  -H "content-type: application/json" -H "X-Api-Key: ra_..." \
  -d '{"user":"ignored","question":"What growth did the October 2025 monitor give for Malaysia in 2024?"}'
```

The key decides the user; the body's user is ignored. Revoke the key on the board and run the same call: 401. Then the stored answer, no model call:

```
curl -s "http://127.0.0.1:8765/api/answer/1?user=alice"
```

What to say: this is the whole surface MARC Online would need, a key per integration that inherits one user's grants and nothing more, every answer stored with its audit row. Nothing of ours touches their portal.

## 5. Admin board (`/dashboard#token=demo`)

In order, top to bottom: the brief (what happened since this device last looked), the key row (wrong-and-confident in the latest run, share answered with checked citations, pages searchable, to-do count), To do with the reason each item is there, the reports-by-edition timeline, coverage with pages flagged for OCR, recent questions across users, saved runs, users, people.

Then the Tools tab:

- Add a user: a name and the libraries they may search. Open `/?user=NAME` and ask as them.
- Try a question as any user and see what the check kept and removed.
- API keys: create, list, revoke.

Expand a panel (Runs or Recent) to show the layout document at work: more columns, saved per owner, reset to default underneath.

## 6. The user's own board (`/dashboard?user=alice`)

Four widgets, nothing about other users: recent answers that open in the chat, usage and the libraries she may search, her ratings, her review requests. Say that this is the "history" and "dashboard" from the brief, that the layout is a JSON document per user, and that the widget catalogue is code.

## Questions MARC may ask, and the honest answer

- **How fast is it?** Eight to eleven seconds on this model for a draft, double when a check fails and the model retries. Retrieval and the gate are milliseconds. A faster model is one line in `.env`.
- **Why did it refuse that?** Either the libraries do not contain it, or the model could not quote it exactly, or the question asked for a figure and no claim stated one. The Investigation steps tab shows which.
- **Can it be wrong?** It can answer beside the point with a true figure; it has not stated a false figure in 285 attempts. Off-target answers are the review request's job.
- **Where does our text go?** To the model provider named in `.env`, and nowhere else. Provider, region and retention are decided with you in week 1, and nothing is sent before that.
- **Does it touch MARC Online?** No. Our store, our front end, your API licence and samples.
