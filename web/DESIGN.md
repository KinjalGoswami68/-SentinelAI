# SentinelAI design system

One system for the app (`/app/`) and the landing page (`/`). Dark only. Minimal and technical:
lots of space, restrained type, flat surfaces, thin borders. No glassmorphism, glow, gradients
or stock illustrations.

Source: the ui-ux-pro-max design-system search ("real-time operations monitoring" and
"developer tool monitoring dark minimal"), adjusted to the product's decisions:
- Pattern: *Real-Time / Operations Landing*: show a live preview, label live data only when it
  comes from a current source (with its update time and a stale state), and render a static
  final snapshot under reduced motion.
- Style: *Minimalism & Swiss*: grid-based, high contrast, functional.
- Palette: the search's dark palette (slate greys, a green run colour) moved to neutral greys,
  with green, amber and red used only as status colours (below).
- Fonts: the *Developer Mono* pairing, IBM Plex Sans for the UI and JetBrains Mono for ids and
  numbers.

Live preview of every component: `web/design-preview.html`.

## Colour

Colours have meaning; nothing is coloured for decoration.

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#0A0A0B` | page |
| `--surface` | `#111113` | cards, table, code block |
| `--raised` | `#18181B` | hover row, popover, input |
| `--border` | `#27272A` | dividers, card edges (decorative) |
| `--border-strong` | `#3F3F46` | emphasised dividers (decorative) |
| `--control` | `#71717A` | input and secondary-button outlines (needs 3:1) |
| `--text` | `#EDEDEF` | primary text |
| `--muted` | `#A1A1AA` | secondary text, labels |
| `--subtle` | `#8A8A93` | timestamps, hints (smallest text allowed in this colour: 12 px) |
| `--ok` | `#3DDC84` | **healthy / ready / OK** only, plus the primary button and focus ring |
| `--on-ok` | `#06210F` | text on `--ok` |
| `--warn` | `#F5B544` | **provisional / warning / SEV3** only |
| `--bad` | `#FF5C5C` | **incidents only** (open incident, SEV1/SEV2, flagged answers) |
| `--ok-tint`, `--warn-tint`, `--bad-tint` | the colour at 12 % over `--surface` | badge backgrounds |

Rules:
- Red appears only for incidents and flagged answers, never for form errors or "delete".
  Form errors use `--warn` plus an icon and text.
- Status is never shown by colour alone: every badge has a word ("ready", "SEV2"), and chart
  markers use a shape (flagged answer = diamond) as well as a colour.
- Resolved incidents are grey (`--muted`). Red means "needs attention now".

### Contrast (WCAG 2.2, computed with the relative-luminance formula)

| Foreground | on `--bg` | on `--surface` | on `--raised` | Needed |
|---|---|---|---|---|
| `--text` | 16.93 | 16.13 | 15.15 | 4.5 (AA) |
| `--muted` | 7.72 | 7.36 | 6.91 | 4.5 |
| `--subtle` | 5.78 | 5.51 | 5.18 | 4.5 |
| `--ok` | 11.09 | 10.57 | 9.93 | 4.5 |
| `--warn` | 10.91 | 10.40 | 9.77 | 4.5 |
| `--bad` | 6.54 | 6.23 | 5.85 | 4.5 |
| `--control` (UI outline) | 4.09 | 3.90 | 3.67 | 3 (1.4.11) |

Badges: `--ok` on `--ok-tint` 8.57, `--warn` on `--warn-tint` 8.35, `--bad` on `--bad-tint`
5.42. Primary button: `--on-ok` on `--ok` 9.55. Focus ring `--ok` on `--surface` 10.57.
`--border` / `--border-strong` (1.27 / 1.81 on `--surface`) are decorative only and never the sole outline
of a control.

## Type

| Role | Font | Weights |
|---|---|---|
| UI and prose | IBM Plex Sans | 400, 500, 600 |
| ids, numbers, code, timestamps | JetBrains Mono | 400, 500 |

Both are SIL Open Font Licence (free). The app and the landing page self-host them as woff2
(`@fontsource` packages, latin subset only), so no request goes to Google and the first paint
is not blocked. `font-display: swap`. Numbers in tables and stat tiles use
`font-variant-numeric: tabular-nums`.

| Token | Size / line height | Use |
|---|---|---|
| `--t-xs` | 12 / 16 | badge, timestamp, chart axis (minimum size) |
| `--t-sm` | 13 / 20 | table cells, secondary text |
| `--t-base` | 14 / 22 | app body |
| `--t-md` | 16 / 24 | landing body, note text |
| `--t-lg` | 20 / 28 | section title (app) |
| `--t-xl` | 24 / 32 | page title (app), stat value |
| `--t-2xl` | 32 / 40 | landing section title |
| `--t-hero` | 56 / 60 (40 / 44 on mobile) | landing hero only |

Headings use weight 600 and letter-spacing -0.01em (-0.02em at 32 px and above). Body text is
at most ~70 characters wide.

## Space, radius, elevation

- Spacing scale (4 px base): `4 8 12 16 24 32 48 64 96`. App density: 12-24 inside cards, 24-32
  between them. Landing: 96 between sections on desktop, 64 on mobile.
- Radius: `--r-sm 4` (badge, kbd), `--r-md 6` (button, input, code block), `--r-lg 8` (card,
  chart), `--r-xl 12` (large panels on the landing page).
- Elevation is a border, not a shadow. Popovers only: `0 8px 24px rgb(0 0 0 / .4)`.
- Layout: app max width 1200, landing 1120; 16 px side gutter on mobile; breakpoints 640 / 1024.

## Motion

- App: no scroll animation. Hover and press transitions 150 ms `ease-out` (colour and
  background only, never width/height). Loading uses a skeleton, not a spinner, if longer than
  300 ms.
- Landing: GSAP + ScrollTrigger only (free under GSAP's standard licence, ScrollTrigger
  included). One pinned section (the scroll sequence), scrubbed (`scrub: 1`), as the skill's
  pin-sequence preset advises ("don't pin more than 1-2 sections"). Other sections reveal with
  the skill's viewport-enter preset: `opacity 0 -> 1, y 12 -> 0, 0.35 s, power1.out`, once.
- `prefers-reduced-motion: reduce`: no pin, no scrub, no reveal; the sequence is shown as static
  frames and the note is shown fully typed.

## Components

1. **Button**: height 40 (44 touch target on mobile through padding). *Primary*: `--ok` fill,
   `--on-ok` text, one per view. *Secondary*: transparent, `--control` outline, `--text`.
   *Ghost*: text only, `--muted` -> `--text` on hover. Focus: 2 px `--ok` ring, 2 px offset.
   Disabled: 40 % opacity and `aria-disabled`.
2. **Badge**: 20 high, 12 px text, radius 4, tinted background plus coloured text, always a word.
   State: `learning` (grey), `provisional` (amber), `ready` (green). Severity: `SEV1` (solid red
   fill, `--bg` text), `SEV2` (red tint), `SEV3` (amber tint). Status: `open` (red dot + text),
   `resolved` (grey).
3. **Card**: `--surface`, 1 px `--border`, radius 8, padding 20 (16 on mobile). A title row
   (`--t-sm`, `--muted`, uppercase off) and content. No shadow.
4. **Table row (incident)**: 56 high, hover `--raised`, whole row is one link with a visible
   focus ring. Columns: severity badge, status, started (mono, relative and absolute in a
   `title`), duration (mono), cause and confidence (`prompt_change` in mono, `0.88` in mono).
   On mobile the row becomes a two-line stacked item.
5. **Code block**: `--surface`, radius 6, JetBrains Mono 13/20, a copy button at the top right
   (ghost, with the label "copy" changing to "copied" for 2 s and an `aria-live` announcement).
   Syntax colours: strings `--ok`, keywords `--muted`; nothing else.
6. **Stat tile**: label (`--t-xs`, `--muted`), value (`--t-xl`, mono, tabular), optional
   context line (`--t-xs`, `--subtle`). Counts are written as counts ("7 of 8"), not percentages.
7. **Empty state**: a one-line statement ("No incidents"), one line of context (the bot's state
   and threshold), at most one action. No illustration.
8. **Chart container**: card with a title, a one-line summary sentence (the text alternative),
   and the chart. Distance-from-normal line in `--muted`; threshold as a dashed `--subtle`
   horizontal line with a label; incident window shaded `--bad` at 8 %; version changes as
   dashed vertical lines with a mono label (`prompt v2`); flagged answers as `--bad` diamonds,
   normal answers as small grey dots. Axis text 12 px `--subtle`. Library: uPlot (~50 KB)
   styled with these tokens.
9. **Status notice** (cold start, error): an inline bar inside the content area, not a toast.
   Cold start: "The server is waking up (free tier), about a minute." with an elapsed timer.
   Error: what failed and a "retry" button.

## Copy

Plain, short sentences. Never: "revolutionary", "seamless", "unlock", "supercharge",
"game-changer", "AI-powered". Never "detects hallucinations". Numbers are counts.

## Config

Product name, tagline and API base URL live in one file, `web/src/config.ts`, so the M12 rename
is a single edit.
