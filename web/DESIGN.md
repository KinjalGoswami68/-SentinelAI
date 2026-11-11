# SentinelAI design system v2: liquid chrome

Approved 2026-11-11; replaces the v1 flat dark system (in git history). One system for the
landing page (`/`) and the app (`/app/`). Live preview: `web/design-preview.html`.
Implementation: tokens and components in `web/src/styles.css`, theme controls in
`web/src/theme.ts`, landing-only pieces in `web/src/landing/`.

Source: the ui-ux-pro-max skill had no matching style ("Liquid Glass" is glassmorphism,
"Y2K" is neon pink), so the visual system is designed to the brief. From the skill:
Space Grotesk as the display face (its "Tech Startup" pairing), and the magnetic-button preset
with its limit of 1-2 magnetic elements per screen.

## Identity

Polished chrome on deep black. Chrome is used where you act (primary buttons, stickers, the
brand word); everything you read sits on quiet graphite. Status colours keep their meaning
(green healthy / ready, amber provisional / SEV3, red incidents only) and are the only
saturated colours, plus one icy cyan accent for focus and links.

No glassmorphism, no blur, no gradient blobs, no glitter. Grain is a fixed 160 px SVG noise
at 5-9 % opacity.

## Theme: two modes x intensity

| | dark (default) | light |
|---|---|---|
| background, intensity 0 -> 100 | `#050506` -> `#1C1D21` | `#D8D9D7` -> `#F7F7F5` |
| surface | `#0E0F12` -> `#26272C` | `#E5E6E4` -> `#FFFFFF` |
| text / muted / subtle | `#F2F3F5` / `#A9ADB5` / `#8E929B` | `#111214` / `#43464D` / `#575B63` |
| accent (focus, links) | `#86DBFF` | `#075A7F` |
| ok / warn / bad | `#3DDC84` / `#F5B544` / `#FF6B6B` | `#0D6332` / `#7D5200` / `#B4232B` |

Intensity (`--i`, 0-1, default 0.5) mixes background and surface between the two ends
(`color-mix` in oklab), and scales reflections, grain and the liquid background. Contrast
was computed at intensity 0, 0.5 and 1 on both background and surface: every text colour is
at least 4.5:1 everywhere (worst: dark subtle 4.78, light ok 5.21). The chrome headline in
light mode uses a graphite chrome whose lightest stop `#6F747C` is at least 3.32:1 (large
display type needs 3:1). Text on chrome (buttons, stickers) uses a light-only chrome whose
darkest stop `#B9BEC5` gives 10.47:1 with `#0B0C0E`.

Controls: a round light/dark toggle, and the intensity fader (a vertical hardware slider
docked at the right edge on desktop, a short horizontal one in the header on mobile).
Keyboard: arrows / Page Up / Down. Both are stored in `localStorage`
(`sentinel.theme`, `sentinel.intensity`) and applied before first paint, so no flash.

## Type

| Role | Font | |
|---|---|---|
| brand word, headings, buttons, stat values | Space Grotesk 500-700 | brand: 46-184 px, -0.055em |
| body, tables, notes | IBM Plex Sans 400-600 | kept: long text stays readable |
| ids, numbers, ASCII, labels, terminal | JetBrains Mono 400-500 | |

All three are free (SIL OFL) and self-hosted as latin woff2 in the real sites.

## Motion and interaction (what each effect is for)

| Effect | Where | Rule |
|---|---|---|
| Liquid chrome background (WebGL, one fragment shader, half resolution) | landing page | renders only while the pointer moves (ripple decays in ~2 s), then stops: no idle frames. Off for reduced motion and touch devices (static frame). CSS gradient if no WebGL. |
| Reflections follow the cursor | brand word, chrome buttons, stickers | one CSS variable updated per animation frame, no layout work |
| Edge light on panels | stat tiles, feature cards (hover) | border-only highlight under the pointer |
| Magnetic pull + springy press | the one primary CTA per screen | skill preset: at most 1-2 per screen |
| Click pulse | buttons | 0.6 s ring in the accent colour |
| ASCII cursor (`+`, becomes a ring with `[open]` / `[press]` / `[drag]` / `[zoom]`) | landing page only, fine pointers only (the app keeps the normal cursor) | native cursor kept for text fields |
| ASCII decode of the brand word | landing hero, once | 0.7 s; the real text is in the page from the start, so it is not a preloader blocking content |
| Scroll reveals (rise + fade, staggered 70 ms) | landing sections; app: first load of a screen only | once per element |
| Pinned scroll sequence (GSAP + ScrollTrigger) | landing page | as now |
| Floating stickers (7 s bob, parallax, hover tilt, click to enlarge in a dialog) | landing hero only, 4 stickers, art-directed to the right column | hidden below 640 px |
| ASCII spinner (braille frames) | loading / waking-up states | |

Reduced motion: no WebGL animation, no bob, no parallax, no decode, reveals shown at once,
the scroll sequence as static frames.

## New pieces

- Mark: a sentinel ring with crosshair ticks, chrome gradient (graphite in light mode).
- Stickers: the mark, `SEV2 opened` in an ASCII box, `kNN · k=5 / p99 ± 0.013`, `Σ cusum ▁▂▃▅▇`.
  Real product terms, not decoration for its own sake.
- ASCII dividers: `── 01 ── components ─────`.
- Empty state: a small ASCII field (`~ quiet ~`) next to the statement.
- Terminal block for code: always dark, in both modes.

## Budgets (unchanged targets)

Landing: Lighthouse mobile performance >= 85, accessibility >= 95, JS < 250 KB gzipped
(the shader is ~2 KB of inline code; no 3D library). App: no WebGL, so its bundle grows only
by the cursor and theme code.
