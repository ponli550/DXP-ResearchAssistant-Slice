# Design rules for the admin board, and where each came from

The board at `/dashboard` follows rules that were already written down, not taste. Two sources: the stylesheets and README of SekerinShotto, a tool one of us built and uses daily, and the `uiux-tips` report that SekerinShotto compiles from saved design notes (fifteen posts as of 17 October 2026). Each rule below names its source. Where a saved tip is a claim from a post, it is treated as a claim: applied because it is consistent with the rest, not because it is proven.

## From SekerinShotto's own pages

| Rule | Where it is used | Source |
|---|---|---|
| One screen, two dense columns, counts with text bars, "what to do" first | the grid, the To do panel, the `█` bars | `panels.py`, the home board |
| Counts, names, reasons and dates; never content text on a board, because a board stays on screen | no passage or answer text anywhere on the board | `panels.py` docstring |
| One sentence of counts on return, silent when nothing happened, with a line saying why | the brief and its `why?` hover | README, "A brief when you come back" |
| Warm ground, one accent, flat rows with hairlines, small-caps section labels, tabular numerals | tokens, panels, `h2`, `.mono` | `inbox-worker/src/core.ts` |
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
- One page, two catalogues. The user board (`/dashboard?user=`) is the admin page in user mode: same grid, same expand, same lazy loading, its own four widgets and head. What a subscriber sees is their own rows and nothing about other users, the library's health or the model runs. A board for MARC's subscribers must show them things MARC Online lacks (their answers, their ratings, their review requests), not a smaller copy of the admin view.
- The chat page uses the same tokens as the boards, so the product reads as one thing: warm background, one accent, 6px radii, no filled tag pills. The answer's claims are set in the serif because they are the product's voice; citations and badges are mono because they are data. While waiting, the status line says what is happening in the order the trace measures it: searching, drafting with the model for about ten seconds, and after twelve seconds that the first draft failed a check and is being redone. No spinner without words.
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
