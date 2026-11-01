# Design rules, and where each came from

## Design tokens

Every raw value lives in `ra/static/tokens.css`: colour, type, space, radius, motion. Every page reads those names, and `tests/test_tokens.py` fails on a raw colour or font anywhere else, or on a token that does not exist. Add a token before adding a value. Semantic names (`--panel`, `--text-muted`, `--action`) are what surfaces use; the raw scales above them (`--ink-900`, `--marker-400`) change only in that file. Light and dark are the same names through `light-dark()`.

| Role | Choice | Why |
|---|---|---|
| Interface | Geist | A plain, modern working face, set small and dense |
| Reading | Source Serif 4 | Built for long reading. The answer and the report page are the product's voice |
| Figures | Geist Mono | Only for data: page counts, times |
| Shell and actions | Ink navy, `--ink-900` | The rail stays ink in both themes, so the desk has a shape before any content arrives |
| Proof | Highlighter yellow, `--marker-400` | The mark a person makes on a printed report. Only ever on a quote, an active citation, or the current report |
| Corners | 4, 8, 12 px | Small, as working software is |
| Motion | 120 and 200 ms, ease-out | Reduced motion removes it |

The fonts are self-hosted under the SIL Open Font License, with the licences beside them. The reference points were AlphaSense, Elicit and NotebookLM, whose public sites were measured in November 2026: 4 to 8 px corners and a 12, 14, 17 px scale at the dense end, a reading serif in Elicit, a yellow highlight in AlphaSense. Their brands and licensed faces (Klarheit, Martina Plantijn, Google Sans) were not used.

## The research desk

The page at `/` is built on one idea: a claim and the page that proves it belong on screen together. The borrowed patterns are general ones; the code, wording and look are this project's.

| Pattern | Taken from | Here |
|---|---|---|
| Sources always in view, ticked to choose what is searched | NotebookLM | The Reports rail, drawn as a timeline because these are editions of one series, with long gaps marked |
| The document beside the answer, with a list of hits | AlphaSense | The page panel, and a strip showing where in the report the passages sit |
| A question becomes a table, and a cell opens its quote | Elicit | Ask across all reports |

What is this product's own:

- **The proof line.** Selecting a citation draws a line from the claim to the highlighted words. It says "every claim is checked against the page it cites" without saying it. It is a gesture, not a fixture: a line that stayed got in the way of reading and scrolling, so it fades after about two seconds, scrolling dismisses it at once, and hovering either end brings it back. It bends only in the gap between the panels and ends in the margin, so it never crosses text. The yellow on the claim and the quote is what stays.
- **Blank is a result.** A table cell with no checked claim says "Not in this report". A refusal is information.
- **Real progress, and no unchecked words.** Each line is a step the server reports with its real time. Modern chat tools stream the answer as it is written; this one streams the work and reveals a claim only after it passes, because showing a sentence that may be removed breaks the promise. No percentage bars and no invented "thinking" text. A fast step gets 280 ms on screen so it can be read; the time beside it stays the real one. The shimmer on the active line is the one piece of gradient text in the product: it marks work in progress and nothing else.

Deleting asks first, in a dialog that says exactly what goes and that it cannot be undone. Cancel holds the focus, so a stray Enter never deletes.

Left out on purpose: generated summaries and audio overviews, because nothing can check them against a page.

The start: one question box, in the calm layout people know from Claude, with the controls inside it the way AlphaSense's chat bar holds its upload, source and output choices. Nothing appears until it has a job to do: the box moves to the foot on sending, the answer brings the page panel, and the side panel is a slim strip until opened, remembered if kept open. Choosing sources is optional, so someone can just type and press Enter. Which reports are searched stays visible in the box, because "only your reports" is what sets this apart from a general chatbot. Upload lives in the **+** menu and also accepts a dropped file; its progress and result sit inside the question box, because that is where the person is looking. The words say "only alice can see it", naming the user, because the demo has a picker and not a login, and "only you" would promise more than that.

Layout once working: three full-height panels with hairlines between them and no gaps, resizable by drag or arrow keys, the widths remembered. The empty space on the start screen is the one exception to "no dead areas", and it ends with the first question. Report pages keep their line spacing: dense means no dead areas, not cramped reading. Below 62rem the panels stack and the page opens over them; that layout works but has not been tuned.

## The boards

The board at `/dashboard` follows rules that were already written down, not taste. Two sources: the stylesheets and README of SekerinShotto, a tool one of us built and uses daily, and the `uiux-tips` report that SekerinShotto compiles from saved design notes (fifteen posts as of 17 October 2026). Each rule below names its source. Where a saved tip is a claim from a post, it is treated as a claim: applied because it is consistent with the rest, not because it is proven.

## From SekerinShotto's own pages

| Rule | Where it is used | Source |
|---|---|---|
| One screen, two dense columns, counts with text bars, "what to do" first | the grid, the To do panel, the `█` bars | `panels.py`, the home board |
| Counts, names, reasons and dates; never content text on a board, because a board stays on screen | no passage or answer text anywhere on the board | `panels.py` docstring |
| One sentence of counts on return, silent when nothing happened, with a line saying why | the brief and its `why?` hover | README, "A brief when you come back" |
| One accent, flat rows with hairlines, small-caps section labels, tabular numerals | tokens, panels, `h2`, `.mono` | `inbox-worker/src/core.ts` |
| A token typed once per device, a button that says what it does, "forget the token on this device" | login, `forget token` | `core.ts`, `uploadpage.ts` |
| Every decision carries its `why` | hover text on to-do rows and the brief | README, throughout |

## From the saved UI and UX tips

| Rule | Where it is used | Source note |
|---|---|---|
| Prioritise key metrics; group related data; make it scannable at a glance | the key-figures row under the brief; panels grouped by subject | 6bdf58c3 (7 dashboard rules) |
| A 60-30-10 colour split; colour guides the eye and sets hierarchy | ground, surfaces, and the accent only on attention: to-do counts, the brief rule, the primary button, user segments | 833f7a5d / d8cfb25e |
| A spacing system, not random padding | `--s1` to `--s5` on a 4px base; nothing else | eaf91ecc |
| Limit fonts and styles | one sans, one mono, one serif with one job each | 833f7a5d |
| Serif for voice, sans for function, mono for data | the brief is serif; labels and controls sans; figures and times mono | cc8aec1a (a saved design-system reference) |
| Feedback within 400 ms; design loading, disabled and error states | buttons disable and say "Asking…" or "Creating…"; a skeleton while the board loads; refusals shown in place | 7ad0ce95 / eaf91ecc |
| Do not use colour alone for status; add the word | outcomes are words ("answered", "not found"); counts have labels | eaf91ecc |
| Show the few options that matter, hide the rest behind "More" | tools (try a question, keys) behind a second tab; the explainer behind one link | eaf91ecc |
| Progressive onboarding and contextual hints, not a tour | the explainer paragraph, shown once, dismissed with "got it" | 833f7a5d |
| `text-wrap: balance` on short text blocks | the brief | 7ad0ce95 |
| Motion 150 to 250 ms, ease-out, honour reduced motion | the tab hover transition; `prefers-reduced-motion` disables all transitions | 885fe2f5 / 69501fd8 |
| Accessibility: contrast, tap targets, focus states, cues not by colour alone | `:focus-visible` ring; 44 px targets on coarse pointers; words beside colours | a279122c |
| Mobile-first, `rem` sizes | base styles are the phone layout; panels collapse to one column under 900 px | a92ac57d |

## What was removed, and the rule that removed it

- A vertical stack of identical cards with a paragraph each: "make it scannable", "show the few options that matter".
- Three explainer boxes: "progressive onboarding, contextual hints".
- A `why` paragraph under every section: the same, moved to hover where SekerinShotto keeps its `why` in the log.
- Accent colour on times, bars, numbers, dots and buttons at once: 60-30-10.
- A node graph with crossing lines: replaced by a timeline per library, because the question the board answers is "which edition is being used", not "what connects to what". Issuer lanes come with MARC's tables.
- A serif body everywhere: serif is for voice; the only voice on the board is the brief.

## How to check it

Screenshot in headless Chrome at 1280×760, light and dark, after any change. Count accent-coloured elements on screen; if it is more than the to-do counts, the rule on the brief, the primary button and the user segments, something has drifted.


## The layout is a document

The board's grid is built from a JSON layout per owner (`layouts` table; `WIDGETS`, `default_layout`, `validate_layout` in `ra/web.py`). Rules it follows:

- The catalogue is code. A layout may choose, order, size and later position widgets; it cannot define one. Unknown types are rejected on write, not skipped on read, so a typo fails loudly.
- Expand is the only write in the pilot. It is a focus mode, not customisation: it sets `span` to 12 and saves, pushes the other panels down rather than covering them, and always has a way back (the same control, now reading "shrink", and Escape for all). This is the feedback-and-states rule from the saved UI tips.
- Expanded means more, not wider. A four-row table stretched across twelve columns is wasted width, so an open panel's table keeps its natural width and gains columns: Runs shows the saved date, timid, refused-right and retrieval figures that were a hover title at the small size; Recent shows the whole question with headers. Detail that only lives in a tooltip is detail that gets clipped at the viewport edge, which the screenshot of the first version showed.
- The grid flows dense, so a full-width panel does not strand the panel before it beside empty space; later small panels fill the gap. Controls carry an `aria-label`, not a `title`: the control's own text says what it does, and a tooltip at the right edge is clipped.
- Width is a CSS variable (`--span`) on the panel, so the phone breakpoint still collapses everything to one column; a layout cannot break the small screen.
- `x`, `y`, `h` and `settings` are validated and stored but not yet interpreted. They exist so a builder is a front end over the same document, not a schema change.
- The layout note under the grid says where the arrangement came from (catalogue default, or saved and when) and offers reset. Status in words, not colour alone.
- Key figures first, literally: the head (brief, key row, sources) is one small request and paints before any panel has data. Each panel shows a skeleton until its own slice arrives, fetched when it is within 200px of the viewport. Slices are kept, so a layout change re-fills instantly. The server computes only the parts a request asks for; the graph behind the timeline is built only when the timeline is on screen.
- The catalogue and layout document now serve the admin board and any portal using `/api/me/layout`. A person's own page is no longer a smaller copy of this board: see below.
- The desk and the boards read the same tokens, so the product reads as one thing, and the choice of light or dark follows from one page to the other. The board keeps its dense layout, because its readers check it daily.
- A count is a door. Every To do row opens the rows behind it, under the list, in the same panel: the open review requests with a done action, the flagged pages by report, the thumbs-down reasons with the question, the wrong-and-confident answers from the latest run with their citations. While a detail is open the panel takes the full row without writing that to the layout; closing it restores the width. Still no answer text on the board: each row links to the stored answer in the chat. `#todo=kind` opens one on load.


## The deck

The proposal deck (`deck.html`) was first restyled to the FatherPeng look with an emoji in every heading, six pastel card colours and coloured pills. The saved design notes call that out as generated-looking, so it was redone as a designer pass: remove, then keep only what is ownable. The rules, and where each comes from:

- **No emoji as headings or icons.** "Emoji are not an icon system… rockets and sparkles in headings are the clearest sign of slop." (Zero Slop Design, tip 13). `EMOJI = false` in `deck.html` switches them off at render; setting it to `true` brings them back.
- **No generic gradients, excessive cards, pills or predictable layouts.** (Web design prompts 02 and 04: "Remove the AI look… Don't ask AI to add more, ask what should be removed.") The rainbow progress bar, the section pill, the pill chips and the pastel card cycle are gone. Text-heavy grids are ruled rows; status lists are plain rows.
- **Three colours carry the page.** Cream, teal, coral (60-30-10, and "two colours carry the page" from the measured white-desert.com example). Yellow appears only as the meme caption highlight. Green, amber and coral mean pass, partly and not yet, and are never the only signal: every status row also says Built, Partly or Not yet.
- **Flat surfaces, small corners.** A one-pixel outline on a raised surface, 4px corners, no shadows and no blur ("Blur hides weak hierarchy… a raised surface and a one pixel outline say the same thing").
- **One spacing scale** (8, 12, 16, 24, 32) and a short type scale (66, 40, 18 and a mono label), not per-slide values. Headlines use `text-wrap: balance`.
- **The first five seconds.** The cover says what it is, who it is for and why; the one-line slide is a single coral statement with one stamp.
- **Personality is kept where it is ownable.** The caption, the stamp and the hand-drawn cat come from the FatherPeng project and appear on three slides only (cover, "today", close). Everything else stays quiet so they read as a choice.
- **Review loop.** "Review the running site, find the 10 highest-impact design problems, fix them, reopen, review again" was run twice on this deck, with screenshots at 1280x720 and `?reveal=all`.

## A person's workspace

`/dashboard?user=` used to show one panel per table, and then a set of charts that still had to be worked out. It now answers what a person needs in order: what is this based on, what should I do next, what do I know, and can I trust it.

| Pattern | Taken from | Here |
|---|---|---|
| A topic's mentions across time, and all of them in one view | AlphaSense's document trend chart and Snippet Explorer | The topic table; a number opens the pages behind it |
| One filter row that scopes everything | AlphaSense dashboard filters | Period and outcome; every chart re-renders from the same rows |
| Every cell links to its source passage | Elicit | Numbers, page marks and citations all open the page |
| An assistant in a side panel, scoped to what is on screen, with fuller research elsewhere | AlphaSense's Ask a Question | The assistant, which hands content questions to the desk |
| A chart keeps a link to how it was made | AI analytics tools such as Collate | Reports and dashboards point at stored answers; each fact opens its flow |
| Steps as nodes on a dotted canvas, coloured by type | Workflow builders | How we got this, as a record and not a builder: no palette, no dragging, no editing |

Rules it follows:

- **Same world as the desk.** Edge to edge, hairlines and no gaps or cards, an ink bar, the serif for claims. Panels share the window and rows stretch to fill it.
- **Every suggestion carries its action.** Nothing on the first screen leaves a person to work out what to do. Each next step says why, in a sentence, and has the button.
- **Yellow still means proof or current.** It marks what is selected and the words found on a page. Chart hues are their own tokens, and the pair passed the dataviz palette checks against the panel in both themes.
- **The topics teach before anyone clicks.** Each report gets the phrase most particular to it, found by counting with running heads left out, so nothing here needs a model or can be made up.
- **A number means the same thing in every row.** Shades are fixed ranges, and every number is printed, so colour is never the only signal.
- **A report cannot hold an unchecked sentence.** A finding is stored as a pointer to one claim of one of the person's own answers, and the server fills in its words. Anything a person writes is labelled as their note. There is no generated summary, for the reason given under the research desk.
- **The flow is a record, and it plays.** Its branches come only from the stored trace, one answer or one report at a time, so it never becomes the crossing node graph removed from the admin board. The sequence plays on arrival and on Replay, in the order things happened, paced so it can be read, while the real time stays beside each step. Reduced motion shows it complete. Run again stays on the page and plays the new run.
- **The proof opens where you are.** A page opens beside the flow: the original PDF when the file is on the server, and otherwise the words as they were read with the quote marked, saying so.
- **The assistant arranges, it does not answer.** It is given counts and titles, never report text. It can only use a fixed list of actions, which are built and checked on the server. A number it states must be in what it was given, the same rule the check applies to a claim.
