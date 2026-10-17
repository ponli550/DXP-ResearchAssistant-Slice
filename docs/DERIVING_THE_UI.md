# How the canvas and steps views were derived from two screen recordings

This records the method, so it can be repeated or checked. It is not a claim that anything was copied. Read the first section before the rest.

## What was and was not taken

Two screen recordings of an AI investigation page in a commercial observability product (Datadog) were used as a reference for interaction patterns. Nothing from the product was reproduced: no code, no assets, no text, no layout measurements. The visual design, the wording, the colours and every number in the CSS are this project's own.

What was taken is a list of behaviours, the kind that are general UI patterns:

- one canvas that the camera pans and zooms between steps, instead of scrolling a page
- a floating list of sections that highlights where the camera is, and jumps on click
- cards that expand in place to show their reasoning
- evidence shown beside each finding, with a link back to what produced it
- a second view that is a plain log of numbered steps, one line each, expandable
- empty results shown as results, and a footer on each step saying what kind of step it was and how long it took
- an action that a person has to press, and thumbs feedback

What was not taken, on purpose:

- **The "Verified" badge.** In the recording all three hypotheses carry one, while the conclusion says none of them was the cause, so it seems to mean "investigated". This project's labels say exactly what happened: "Quote on page N" or "Removed", and the page says the check proves the source, not that the claim answers the question.
- **Any content that is not real data.** Every node and step shows data the pipeline produced. The staged reveal is presentation only, and the docs say so.

## The material, and where it must stay

The recordings are of a work tool, show an internal project and a work email address, and belong on the person's machine only. They are not in this repository, the extracted frames were deleted after use, and this document names no internal resource, project, alert or person. If this repository is ever shared, check the employer's policy on using work screens as reference before sharing anything that could be traced to them.

## Method

The commands are macOS with Homebrew ffmpeg.

1. **Find the file.** macOS screen recordings put a narrow no-break space before AM or PM, so a typed path fails. Match with a wildcard:
   ```
   find ~/Desktop -maxdepth 1 -name "Screen Recording 2026-10-17 at 8.44.58*"
   ```
2. **Probe it.**
   ```
   ffprobe -v error -show_entries format=duration,size:stream=width,height -of default=nw=1 FILE
   ```
   The recordings were 33 and 45 seconds, about 1460 by 950.
3. **Sample for structure.** One frame every 3 seconds at 900 px wide shows the screens and the states, but not motion:
   ```
   ffmpeg -i FILE -vf "fps=1/3,scale=900:-2" f%02d.png
   ```
4. **Read the motion from a contact sheet.** At 4 frames a second, tiled, the sheet shows how states change between neighbours:
   ```
   ffmpeg -i FILE -vf "fps=4,scale=260:-2,tile=8x5" -frames:v 1 sheet.png
   ```
   Tiles that small are unreadable but show the pattern: where the whole tree shrinks to an overview, where a card grows and overlaps its neighbours.
5. **Zoom into the transitions.** Cut a short window around a transition and tile it larger:
   ```
   ffmpeg -ss 4.5 -t 2.5 -i FILE -vf "fps=3,scale=520:-2,tile=4x2" -frames:v 1 sheet-t1.png
   ```
   In those frames the tree is first zoomed out with the conclusion open, then scales up with cards overlapping mid-move, then settles. A hovered card grows in place and shows its reasoning and a source line, and the left list changes its highlight as the camera moves.
6. **Read the second recording the same way.** It was a different tab of the same page. It showed a log of numbered steps with a rail of dots, one-line summaries that expand, empty results, a kind and duration footer, an expand-all control, and findings that link back to their search.
7. **Write down the pattern, then build it from this project's own data.** Each pattern was mapped to something real in the pipeline before any code was written.

## From observation to implementation

| Observed | Built | Real data behind it |
|---|---|---|
| Camera moves between steps | `focusNode`, `overview`, an eased transform on one canvas | none, it is presentation |
| Left list follows the camera | a list with `aria-current` set from the camera position | none |
| Cards expand in place on hover | source cards, hover or tap or Enter | the retrieved passages |
| Evidence beside each finding | "Show the quote" under every claim, citation chip opens the page with the quote highlighted | the cited quote and page |
| A log of steps, one line each | the Investigation steps tab | `result.trace` |
| Empty results shown | "No passage matched. Nothing was drafted." | the search step with `found: 0` |
| Kind and duration footer | "Model call" or "Plain code", and a duration | measured with `perf_counter` |
| Findings link to their search | "Show how it was found" jumps to the exact source card in step 1 | the search step and the cited page |
| An action only on a click | "Create review request" writes a log entry | the `reviews` table |
| Thumbs feedback | thumbs up, or down with a reason | the `feedback` table |

## What the recordings could not tell me

- **Exact easing and durations.** At 3 to 4 frames a second you can see that the movement eases, not its curve. The values used here (a cubic-bezier of .22,1,.36,1 over .75 s) are this project's choice, not measured.
- **Hover timing and keyboard behaviour.** A recording of a mouse shows neither.
- **What happens on errors, and what the data model is.**
- **Audio.** It was not used.

## How each behaviour was checked

- **Screenshots in headless Chrome**, as two users, at desktop width and at a true 390 px width.
- **The page script in jsdom**, 18 checks on a copy of the database: source cards, nav, "Show how it was found", expand all, thumbs and the review request (`tools/dom_check.mjs`, with `tools/standin_server.py`, which is a labelled stand-in and not a model).
- **Not checked:** camera maths, hover and drag, because jsdom has no layout and headless Chrome cannot be driven here.

Problems met on the way, so nobody repeats them. Causes are inferred from behaviour unless stated, and none of them was investigated further:

| Problem | Likely cause | What worked |
|---|---|---|
| Phone screenshot looked clipped | headless Chrome seems to enforce a minimum window width, so the layout was wider than 390 | put the page in a 390 px iframe on a local file and screenshot that |
| Text unreadable at phone width | the 700 px canvas was scaled to fit | let the canvas width follow the screen so the scale stays 1 |
| Whole page scrolled when the quote was highlighted | `scrollIntoView` scrolls the document | set the scroll position of the panel instead |
| Chrome command never returned | `--dump-dom` together with `--screenshot` often did not exit | take the screenshot alone and poll for the file |
| Iframe stuck on its first node | the virtual clock appeared not to advance timers inside an iframe | `?reveal=0` skips the staged reveal |
| Playwright would not install | unknown: the install hung under Python 3.14 and stalled on a 41 MB download under Python 3.12 | skipped it, used Chrome flags and jsdom |
| Project virtual environment rebuilt | `uv run --python 3.12` inside the project | run one-off tools with `--no-project` from another folder |

## Repeating this for another recording

1. Keep the recording and every extracted frame out of the repository. Delete frames when done.
2. Probe, sample at 1 frame per 3 seconds, then contact-sheet the transitions.
3. Write down patterns, not pixels. For each one, name the real data that will feed it.
4. Say what you could not see. Do not present guessed numbers as measured.
5. Change any label that overstates what the system knows.
6. Check in a browser and in jsdom, and say what neither covered.
