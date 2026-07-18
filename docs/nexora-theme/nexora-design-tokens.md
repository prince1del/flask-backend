# NEXORA — visual theme specification

Single source of truth for colors, type, spacing and icons across the Flask web app and the Flutter mobile app. CP and ASG should both implement from this file — not from eyeballing screenshots — so the two surfaces stay in sync.

## 1. Color tokens

| Token | Hex | Usage |
|---|---|---|
| `void` | `#05070C` | Page background (darkest) |
| `bg-1` | `#0A0E18` | Sidebar / shell background |
| `bg-2` | `#0E1524` | Main panel background gradient stop |
| `glass` | `rgba(255,255,255,0.045)` | Card / panel fill |
| `glass-hover` | `rgba(255,255,255,0.075)` | Card hover state |
| `glass-border` | `rgba(255,255,255,0.09)` | Card / divider borders |
| `cyan` (primary accent) | `#25E0FF` | Primary actions, active nav, links, live/online state |
| `violet` (secondary accent) | `#9A6BFF` | Secondary emphasis, AI/insight elements |
| `mint` (success) | `#3FE0A5` | Positive deltas, success states |
| `amber` (warning) | `#FFB648` | Warnings, mismatches, pending states |
| `coral` (danger) | `#FF6B6B` | Errors, blocked, negative deltas |
| `text` | `#EAF0FB` | Primary text |
| `text-dim` | `#8B96B8` | Secondary text, labels |
| `text-faint` | `#4B5474` | Tertiary text, timestamps, mono captions |

Rule: cyan and violet are the only two accent hues used for brand/decoration. Mint / amber / coral are reserved strictly for status meaning (success / warning / danger) — never used decoratively.

## 2. Typography

| Role | Typeface | Weight | Usage |
|---|---|---|---|
| Display | Orbitron | 700 / 900 | Wordmark, page titles, section headers only — used sparingly |
| Body / UI | Sora | 400 / 500 / 600 | All body text, labels, buttons, nav |
| Data / mono | JetBrains Mono | 400 / 500 | Numbers, metrics, timestamps, status readouts |

Type scale (px): 11 (caption) · 12.5 (body-sm) · 14 (body) · 16 (body-lg) · 19 (h3) · 23 (h2 / metric value) · 28 (h1).

Do not use Orbitron for body copy — it is a display face only and hurts readability below 16px.

## 3. Spacing & shape

- Base spacing unit: 4px. Common gaps: 8 / 10 / 14 / 16 / 22 / 26px.
- Card radius: 14px. Pill / avatar radius: 50%. Small tile radius: 10–11px.
- Borders: always 0.5px hairline, never 1px+, using `glass-border`.
- Card top-edge highlight: 1px gradient line (transparent → white 35% → transparent) — the one recurring "glossy" signature.

## 4. Glow & elevation rules

Glow is a status signal, not decoration — use it deliberately:
- Active nav item / focused element → cyan glow, `drop-shadow(0 0 4px rgba(37,224,255,0.7))`
- AI / insight elements → violet glow
- Live/online status dot → mint glow
- Core brand mark (logo orb) → the one animated element in the whole system; pulses cyan→violet every ~3.2s. This is the signature — do not add competing motion elsewhere.
- No glow on static/inactive elements. No glow on body text.

## 5. Icon system

14 custom outline icons, one per NEXORA module, stored as standalone SVG in `nexora-icons/`:

`dashboard · sales · purchase · inventory · finance · fulfillment · distributors · retailers · article-master · analytics · banking · approvals · users-roles · settings`

Icon rules:
- 24×24 viewBox, `stroke="currentColor"`, `fill="none"`, `stroke-width="1.6"`, round linecap/linejoin.
- Never filled/solid style — outline only, matches the glass aesthetic.
- Color inherits from context via `currentColor` — set the parent's `color` (web) or `IconThemeData.color` (Flutter) rather than editing the SVG.
- Active/selected icon gets the cyan glow treatment described above; inactive icons use `text-dim`.
- Display size: 17–20px inline (nav), 20px in icon tiles, never below 14px or above 24px.

## 6. Implementation files

- `nexora-theme.css` — CSS variables + base component classes for the Flask/web frontend.
- `nexora_theme.dart` — Flutter `ThemeData`, `ColorScheme` and text styles for the mobile app.
- `nexora-icons/*.svg` — the 14 module icons (web: inline or `<img>`; Flutter: `flutter_svg` package).

Both theme files consume the same token values above — if a token changes, update it in both files and this doc in the same commit.
