# Helper

A separate layer that tells a new user where to go and shows them around. It is not part of the app: `ra/` does not mention it,
and a test (`tests/test_helper.py`) fails if it ever does.

## What it does

- A **Help** button, and `?` as a shortcut, opens a panel: examples to try, then what you may want to do, each with a button that does it. The button sits at the end of the page's top bar (the bar holding `.nav`), floats bottom-left only on a page without one, and is left out where the page has its own guide (`#helpPop`, the desk), so there is one Help per page; `?` still opens the panel there.
- **Try one** comes from the person's own history and library, so the examples work for them: questions they have already had answered (opens the stored answer, no model call) and "finish the sentence" stubs for the newest editions.
- **Show me around** is a six-stop tour that dims the page and highlights one element at a time. It is offered once, on first visit, and remembered in the browser.
- It adapts to the page: the research desk, a user's activity, the admin board.

## Run it in front of the app

```
uv run --python 3.14 python helper/serve.py --app http://127.0.0.1:8765 --port 8770
```

Open http://127.0.0.1:8770 instead of :8765. It is a reverse proxy: every request goes to the app unchanged, an HTML response gets one script tag, and `/__helper/helper.js` and `helper.css` are served from this folder. To share it, point a tunnel at 8770: `cloudflared tunnel --url http://127.0.0.1:8770`.

`#helper=open` and `#helper=tour` in the address open the panel or start the tour, for links and screenshots.

## Other ways to load it

- One script tag in any page: `<script src="https://your-host/__helper/helper.js" defer></script>` (it loads `helper.css` from beside itself).
- A bookmarklet: `javascript:(function(){var s=document.createElement('script');s.src='http://127.0.0.1:8770/__helper/helper.js';document.body.appendChild(s)})()`

## What it assumes about the page

It reads the page, never the app's code. Each of these is optional; a missing one only removes the matching button or tour stop.

| Page | Elements it looks for |
|---|---|
| Research desk (`/`) | `#q`, `#go`, `#findBtn`, `#askMode`, `#modeBtn`, `#scopeBtn`, `#railOpen`, `#plusReports`, `#user`, `#melink`, `.nav` |
| Workspace (`/dashboard?user=`) | `#sources`, `#assistantBtn`, `[data-type=next]`, `[data-type=topics]`, `[data-type=found]` |
| Admin board (`/dashboard`) | `#brief`, `#keyrow`, `[data-type=todo]`, `[data-type=timeline]`, `.tabs`, `#newuser`, `#newkey`, `#try` |

Endpoints it calls, all existing and read-only: `/api/me/widget/answers` and `/api/me/widget/library`. It also reads the chat's `?user=` and `?a=` links, and handles `?tab=find` itself.

## Rules it follows

No framework, no markup built from strings, nothing in the page's namespace (every class is `.rah-*`, one global `__raHelper`), keyboard and screen-reader reachable (a dialog with focus managed, Esc closes), reduced motion respected, and the look from `docs/DESIGN.md`: flat, one-pixel outline, a word on every button, no emoji.
