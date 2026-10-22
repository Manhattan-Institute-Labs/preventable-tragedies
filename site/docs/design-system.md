# Design system

This design follows the **Manhattan Institute Brand Guidelines, Complete Version (December 2022)**, which is on SharePoint at *MIAllshare › Templates Brand Guidelines › Brand Guidelines › Manhattan Institute › 04_Brand Guidelines & Info › MI-BrandGuidelines-Beta1.pdf*. Questions about the brand go to the MI Marketing team.

Every value below is a CSS custom property in `assets/css/styles.css`.

## Color

### Primary palette (from the guidelines)

| Token | Hex | MI name | Use here |
|---|---|---|---|
| `--mi-blue` | `#2C48DC` | MI Blue | Links, kickers, accents, emphasized chart series, the slash motif |
| `--mi-blue-dark` | `#1F38BC` | MI Dark Blue | Hover/pressed, soft-tag text |
| `--mi-orange` | `#FF8000` | MI Orange | **Actions and key callouts only**: primary buttons, chart call-out dots, accents on dark bands |
| `--mi-slate` | `#20222E` | MI Slate Black | Body text, dark bands |
| `--mi-sky` | `#E8ECFC` | MI Sky Blue | Tinted bands, soft tags, context boxes |
| `--mi-sky-dark` | `#E1E6FA` | MI Sky Blue Dark | Hairlines and dividers |
| `--mi-sky-light` | `#F0F3FF` | MI Sky Blue Light | Page ground |

### Data colors (from the guidelines)

| Token | Hex | MI name |
|---|---|---|
| `--mi-grey` | `#AEB0B7` | MI Grey (baseline / de-emphasized series) |
| `--mi-cyan` | `#2FA8FF` | MI Cyan (secondary series; also the focus ring) |

The guidelines also list MI Red `#E60049`, Aqua `#0BA39A`, Yellow `#E6D800`, Green `#6AE257` and Purple `#CA51F4` for charts that need more series.

### Derived (not in the guidelines; tuned for contrast)

`--text-body #3A3D4A`, `--text-muted #4A4D5C`, `--border-soft #C3CBEF`, `--on-dark-muted #C9CEE3`, and the photo-placeholder tints `--photo-bg / --photo-line / --photo-figure`.

### Contrast notes

- MI Orange on white is only about 2.3:1, so **never use orange for text on light grounds**. Primary buttons use Slate Black text on orange (about 8:1).
- Orange text is fine on Slate Black (about 7:1).
- MI Blue on white or Sky Blue Light passes AA for body text.

## Typography

| Role | MI brand font | Web stand-in (Google Fonts) | Token |
|---|---|---|---|
| Headings + body | Le Monde Livre Pro | **Source Serif 4** (400/600/700) | `--font-serif` |
| Labels, meta, nav, buttons, chart text | GT America | **Public Sans** (400–900) | `--font-sans` |
| System fallbacks (per guidelines) | Georgia / Arial | — | built into the stacks |

If MI licenses its brand fonts for web, swap the two `font-family` stacks in `:root` and remove the Google Fonts `<link>` from each page.

This follows the guidelines: "Le Monde Livre [is] the key player for headings and body copy, whereas GT America is used as a supporting font… such as job titles or categories."

Scale: `.h1` uses clamp(48px → 84px), `.h2` clamp(34px → 44px), `.h3` clamp(28px → 34px), and `.lede` 19–22px. Kickers are 13px sans, bold, uppercase, with 0.12em tracking.

## The crosswalk / slash motif

The guidelines describe the crosswalk as integral to keeping the brand present: "always keep the forward-slanting motif present, at the correct angle, and always on the right side of the graphic."

On this site it appears:

- after the "PREVENTABLE TRAGEDIES" wordmark (two bars)
- after section kickers (small two-bar mark)
- on the key-figure band in `facts.html` (large, Sky Blue + Orange)
- to the right of the pull quote (quote styling)

Markup: `<svg class="slash" …><path d="M8 0h6L6 24H0Z"/><path d="M20 0h6l-8 24h-6Z"/></svg>`. Add `.slash--orange` on dark grounds.

**Don't** redraw or imitate the MI logo itself. Use the official logo files from the brand folder if a lockup is needed (for example "A project of the Manhattan Institute"), and follow the logo exclusion-zone and minimum-size rules (100px wide minimum for the full mark on screen).

## Imagery

The guidelines' digital image treatment is: convert to black and white, then add a Sky Blue layer in Luminosity blend mode. The booking-photo placeholders use a Sky Blue duotone to preview this. Real photos get `filter: grayscale(1)` via `.photo` for now. Pre-treat images in the asset pipeline for the full effect.

## Icons

The guidelines call for sharp lines and corners, minimal components and high contrast. Inline SVG icons here use `stroke-linecap="square"`.

## Charts and data

From the guidelines: "Slate Black is used for the majority of information, with key items such as column headers in MI Blue. For graphs and graphics, the tertiary colors can be used to distinguish data clearly."

In this project:

- chart titles use `.figure__title` (MI Blue, sans, 800)
- axes and labels are Slate Black or muted, in Public Sans
- the emphasized series is MI Blue, the baseline series is MI Grey, and a single call-out uses MI Orange
- captions use `.figure__caption`, and every caption states its source; illustrative charts say so

## Layout

- 1200px max container, 32px gutters (16px under 760px).
- The guidelines specify a 12-column web grid. This build uses auto-fit CSS grids and flex wrapping instead, so every section collapses to one column on phones.
- Section rhythm alternates white / Sky Blue Light / Sky Blue / Slate Black / MI Blue bands.

## Components (class names in `styles.css`)

- **Shell:** `.mock-banner`, `.site-header`, `.wordmark`, `.site-nav`, `.site-footer`, `.skip-link`
- **Controls:** `.btn--primary` (orange), `.btn--secondary` (outline), `.chip[aria-pressed]`, `.tag--solid`, `.tag--soft`, `.input`, `.select`, `.search`
- **Landing:** `.stat-grid`, `.feature-case`, `.record-strip`, `.path-card`, `.signup-form`
- **Stories:** `.filter-bar` (sticky), `.card-grid`, `.story-card`
- **Facts:** `.chapter-nav`, `.scrolly` / `.step` / `[data-hl]`, `.figure`, `.legend`, `.keyfig`, `.hbar`, `.context-box`, `.pullquote`, `.sources`
- **Solve:** `.proposal-nav`, `.proposal` (`--reverse`), `.flow`, `.threshold-grid`, `.compare`, `.vbars`, `.dash-sketch`
