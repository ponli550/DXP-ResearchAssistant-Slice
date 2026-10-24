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
- Width is a CSS variable (`--span`) on the panel, so the phone breakpoint still collapses everything to one column; a layout cannot break the small screen.
- `x`, `y`, `h` and `settings` are validated and stored but not yet interpreted. They exist so a builder is a front end over the same document, not a schema change.
- The layout note under the grid says where the arrangement came from (catalogue default, or saved and when) and offers reset. Status in words, not colour alone.
