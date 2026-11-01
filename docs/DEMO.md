# Demo script

A rehearsal order and a prompt list for the MARC meeting. Every answer shown is real: the model named in `.env` answers, the gate checks, refusals stay refusals. The only preparation is a seeded database, built before the meeting through the same pipeline, so that history can be shown in seconds and one or two questions asked live.

## The story, in one breath

The saved storytelling notes give the order: stakes, struggle, turn, detail, lesson. Use it.

- **Stakes.** A figure copied by hand has no link back to its page, and errors travel into a rating note. That is what MARC sells.
- **Struggle.** The small model refuses half of what it could answer, and every comparison. Say it before they find it.
- **Turn.** The gate caught a model reading a growth figure off a chart's axis: the quote was real, the number was in it, and the rule still said no. That is the moment the design proved itself.
- **Detail.** 0 of 285. Three models, nineteen runs, no false figure stated as fact.
- **Lesson, turned outward.** For MARC: every answer carries its page, access cannot leak, and in ten weeks you get a number on your own reports, not ours.

The deck keeps the FatherPeng identity (teal and cream, coral rule, yellow meme captions, stamps, the orange cat drawn as SVG in `deck.html`'s `cat()` helper) and drops the emoji and the colour-for-decoration of the first revamp; `docs/DESIGN.md` has the rules and the saved notes they come from. `deck.html?reveal=all#N` shows every row of a reveal slide at once, for screenshots and printing. Slides 6, 9, 10 and 11 carry screenshots of the live demo (`deck-assets/`, retaken with headless Chrome after a visible change; `scripts/deploy_deck.sh` ships them). Slides 8, 10, 14, 16 and 22 reveal one row at a time with the right arrow. Short lines on the slide; the longer sentence is yours to say.

## Before the meeting

```
# `python -m ra serve` reads .env itself: HERMES_API_KEY, HERMES_MODEL=qwen/qwen3.8-27b. See env.example.
uv run --python 3.14 python scripts/seed_demo.py data/demo.db     # sixteen model calls, a few minutes; safe to rerun, it resumes
uv run --python 3.14 python -m ra --db data/demo.db serve --port 8765
```

To show it from another machine or phone, a quick tunnel needs no account or domain and gives a public HTTPS URL that lives while the laptop and the command do:

```
cloudflared tunnel --url http://127.0.0.1:8765     # the URL is printed in the log; it changes every start
```

Set `ADMIN_TOKEN` in `.env` to something random before the URL leaves the room: anyone with the URL can use the chat and spend model tokens, and `demo` is not a secret.

Open three tabs: the research desk at `/?user=alice`, alice's activity at `/dashboard?user=alice`, the admin board at `/dashboard#token=demo`.

Rules for the live part. The model is whatever `.env` names; check it before the call. On 27 October it was Claude Sonnet 5.5, and through the public tunnel all six questions in the safe list answered in 2 to 12 seconds (the October 2023 and social assistance ones needed a retry), and the Singapore wage question was refused. With Qwen 3.8 27B the same set answered about half and every comparison was refused, so a question typed on the spot was a coin flip. Either way, ask live only from the safe list, and keep the stored answers as backup: if a live question is refused, that is the product working, so say so and open the stored answer to the same question from the board. The organisers require the demo to be live, not pre-recorded: stored answers are support, never the show.

## Optional: the helper

Run `uv run --python 3.14 python helper/serve.py --app http://127.0.0.1:8765 --port 8770` and open :8770 (or tunnel it) to show the Help panel and the tour: a newcomer's route in. It is a separate layer in front of the app; see `helper/README.md`. Show it before section 0 when the audience has not seen the product.

## 0. Find, no model (chat, alice)

Type words you know are in a report and press **Find**: `grew by 6.3 percent year-on-year`. Pages with the words marked, in a few milliseconds, no model call; click a page to see it, or PDF to open it there. Then `"real gdp grew" -2024` to show a phrase and an exclusion. The point: finding is plain full-text search over the library, free and instant. The model is only for a claim with a checked quote. `/?user=alice&q=...&find=1` opens straight into it.

## 1. The answer (chat, alice)

**Safe live** (Qwen answered these in the saved run and again when the demo database was seeded; still ask one, not five):

- What growth did the October 2025 monitor give for Malaysia in 2024?
- How much did Malaysia's economy grow in the first half of 2025?
- What was Malaysia's real GDP growth in the fourth quarter of 2025?
- What forecast did the October 2023 monitor give for Malaysia's growth in 2024?
- What share of social assistance transfers goes to the B40 households?

Not safe live, although it is in the question set: "What GDP growth is projected for Malaysia in 2025 in the October 2025 monitor?" Qwen refused it when the demo database was seeded and answered it the day before. It is in history as a refusal, with alice's thumbs-down and review request on it, which is the better story anyway.

What to show on one of them: the live steps as the question runs, the search, the draft and the check, with the retry and its reason if there was one; the answer appears and a line links it to the highlighted quote on its page, then fades (hover either end to bring it back); hover the citation to preview the quote; "Ask across all reports" for the table; thumbs down with a reason; "Ask someone to review".

**From history** (open from alice's board, "open" on the row). Be straight about what is there: of alice's twelve seeded questions, five are answered. Qwen refused every open "what does the monitor say about X" question (what drove growth in 2025, the downside risks, inflation, the ringgit), the comparison, and the two it should refuse.

- Open one refused analysis question and open "Checked in…" above the answer: the right pages were found, the model drafted claims, the gate removed them for quotes that were not exact, and the retry gave up. That is the small model's known weakness, not the design's: the larger models answer these. Say that the model is one line in `.env` and the choice is residency and cost, not quality of the gate.
- The comparison: how the view of 2025 growth changed between October 2025 and April 2026. Qwen refuses it. The two larger models answer it with one true side; both-sides-or-refuse is the week 4 to 5 item.
- The internal memo: "What did the board approve, according to the internal memo?" is answered for alice from the memo. Keep it for section 3.

## 2. Refusals (chat, alice)

Safe live, any of these; each returns "not found" with the closest pages and no invented figure:

- What will Singapore's minimum wage be in 2029?
- Who is the chief executive officer of PETRONAS?
- What growth does the World Bank forecast for Malaysia in 2031?

The point to make: three models, nineteen runs, 285 answerable attempts, no false figure stated as fact. The gate removed up to six in ten of the larger models' drafts, one of them a number read off a chart's axis.

## 3. Access (chat, switch the user to bob)

- What did the board approve, according to the internal memo?

bob holds no grant on the internal library, so the memo is never retrieved for him and the answer is "not found", with closest pages only from the World Bank series (in history as bob's). Switch to alice and open the same question from her history: she has the grant and gets the memo's sentence, cited. Say that the memo is a synthetic one-page document, marked as such. Access is enforced inside retrieval, before any model call; the trace never names a library the user cannot see.

## 4. Integration (terminal, beside the admin board)

Create a key on the admin board's Tools tab for user alice, named "MARC Online portal", then:

```
curl -s -X POST http://127.0.0.1:8765/api/ask \
  -H "content-type: application/json" -H "X-Api-Key: ra_..." \
  -d '{"user":"ignored","question":"What growth did the October 2025 monitor give for Malaysia in 2024?"}'
```

The key decides the user; the body's user is ignored. Revoke the key on the board and run the same call: 401. Then a stored answer, no model call (2 is alice's answered question about 2024 growth):

```
curl -s "http://127.0.0.1:8765/api/answer/2?user=alice"
```

What to say: this is the whole surface MARC Online would need, a key per integration that inherits one user's grants and nothing more, every answer stored with its audit row. Nothing of ours touches their portal.

## 5. Admin board (`/dashboard#token=demo`)

In order, top to bottom: the brief (what happened since this device last looked), the key row (wrong-and-confident in the latest run, share answered with checked citations, pages searchable, to-do count), To do with the reason each item is there, the reports-by-edition timeline, coverage with pages flagged for OCR, recent questions across users, saved runs, users, people.

Click a To do row: the review requests open under it with an **open** link and a **done** action; close one and the brief, the key row and the people panel update. Click "wrong-and-confident in the latest run" to show the one comparison answer with its three cited sentences, and say why the scorer counted it. `/dashboard#token=demo&todo=wrong` opens it directly.

Then the Tools tab:

- Add a user: a name and the libraries they may search. Open `/?user=NAME` and ask as them.
- Try a question as any user and see what the check kept and removed.
- API keys: create, list, revoke.

Expand a panel (Runs or Recent) to show the layout document at work: more columns, saved per owner, reset to default underneath.

## 6. The user's own workspace (`/dashboard?user=alice`)

The user flow, in the order MARC described it: see the dashboard, find a document, ask, check the source, open the PDF, run an analysis, write it up.

- **Your sources**: say what this is based on, in one breath. Five editions, with the phrase most particular to each: nobody wrote those, they are counted from the pages.
- **What to do next**: each has its reason and its button. Press **Read the pages** on a subject nobody has asked about: the passages open with the words marked.
- **What you've found**: checked facts with their pages. Press **How we got this** on one: it plays the steps that really happened, the reports searched, what the check kept and removed. Select a step for its evidence, and on the last one press **Open page** to see the proof beside the flow. The original PDFs must be in `data/pdfs` for the PDF view. Then show a refusal, where it ends on the reason.
- **Explore**: type **tariff** into "Add a topic": it barely appears until 2025. That is a finding, in about a second, with no model call.
- **Assistant**: ask "Build a dashboard about tariff", then "Draft a report from what I've found". Then ask it what GDP growth was: it refuses and offers the desk, because nothing unchecked is said here.
- **Reports**: only checked facts with their pages, plus notes marked as yours. **Print or save as PDF**. Say that this is the "history", "dashboard" and "analysis" from the brief.

What to say about tables, honestly: it is the same pipeline many times, not a summary. Cells are blank where the model refused, and on the small model that is often. The point is that nothing in the table is unverified.

## Questions MARC may ask, and the honest answer

- **How fast is it?** Eight to eleven seconds on this model for a draft, double when a check fails and the model retries. Retrieval and the gate are milliseconds. A faster model is one line in `.env`.
- **Why did it refuse that?** Either the libraries do not contain it, or the model could not quote it exactly, or the question asked for a figure and no claim stated one. "Checked in…" above the answer opens the steps, which show which.
- **Can it be wrong?** It can answer beside the point with a true figure; it has not stated a false figure in 285 attempts. Off-target answers are the review request's job. The admin board's key row shows "1 wrong and confident, latest run" because the latest saved run is Claude's, and that one is a comparison answered with one true side. Say so before they ask.
- **What if the model goes down mid-demo?** The call is retried once; if it still fails, the answer is a refusal and the steps view says the model did not answer. Open the stored answer instead.
- **Where does our text go?** To the model provider named in `.env`, and nowhere else. Provider, region and retention are decided with you in week 1, and nothing is sent before that.
- **Does it touch MARC Online?** No. Our store, our front end, your API licence and samples.
