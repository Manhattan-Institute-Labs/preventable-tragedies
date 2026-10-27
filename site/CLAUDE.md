# Preventable Tragedies — site

The Preventable Tragedies site (project overview and content rules are in the repo-root `CLAUDE.md`). Paths below are relative to this `site/` folder.

**Status:** first draft, published to GitHub Pages by `.github/workflows/pages.yml`. The landing page's random case and the stories index show **real cases** from `database/exports/stories.json`. All other copy is placeholder (`[Bracketed descriptions]` + lorem ipsum), chart values are illustrative, and photos are SVG placeholders. Pages carry `noindex` and a draft banner.

## Pages

| File | Page | What it does |
|---|---|---|
| `index.html` | Landing | Hero, shows a **random case** from the story database, links to the other three pages, email sign-up |
| `stories.html` | Read the stories | Index of case cards (photo, details, source link) with category filter, search, sort, load-more |
| `facts.html` | Get the facts | Scrolling explainer with three chapters, charts, footnotes and a sources list. Chapter I has a sticky scrollytelling chart |
| `solve.html` | Solve the problem | Five policy proposals, each with a graphic, plus a sign-up CTA |

## Structure

```
index.html  stories.html  facts.html  solve.html
assets/
  css/styles.css        design tokens (CSS custom properties) + all component classes
  js/main.js            all behavior: random case, sign-up, index filters, scrollytelling
build.py                builds ../_site/: copies the pages and assets, generates assets/js/stories-data.js
docs/design-system.md   MI brand rules this design follows (colors, type, slash motif, charts)
design-source/          the original Claude Design canvas files (reference only; see below)
```

## Running it

From the repo root (standard library only, no installs):

```
python3 site/build.py                     # writes _site/ (gitignored)
python3 -m http.server -d _site 8000      # then visit http://localhost:8000
```

`build.py` turns each database row into the fields the templates read (`nameAge`, `dateLabel`, `headline`, `record.count`/`record.label`, `recordAlt`, `status`/`statusLabel`, `source.outlet`/`source.url`) and writes them to `_site/assets/js/stories-data.js` as `window.STORIES` and `window.CATEGORIES`. It's a plain script, not JSON loaded with `fetch`, so the built site also works from `file://`. The source pages without a build show no cases.

**Field mapping rules** (in `build.py`): the headline is the offense type (for a serious-mental-illness incident with no offense, what happened) plus the outcome when the outcome is short; filter categories are the exports' `categories` (frequent violent offender, very frequent petty offender, serious mental illness, record not detailed; "not yet categorized" for rows the sweep hasn't reached), and a card with several shows under each, its tag listing them; a card leads with prior arrests, else convictions, else felony convictions, showing only counts the article stated, and a serious-mental-illness card then shows the earlier system contacts its articles quote (days since a release first, when printed); which side of a harm the subject was on is never shown; `none stated` release status shows nothing on cards and "Not stated" on the landing page; `photo` stays `null` even where the database has a `mugshot_url`, until usage rights are confirmed.

## How the code is organized

- **Static HTML + one stylesheet + one script.** Every page loads `styles.css` and `main.js`. Pages that show cases (`index`, `stories`) also load the generated `stories-data.js` first.
- **`main.js` init functions guard themselves.** `initRandomCase`, `initSignup`, `initIndex` and `initScrolly` each look for their own markup and do nothing if it's missing.
- **Templates are declarative.** Story markup lives in HTML (the `#random-case` article and the `<template id="story-card-template">`). Elements say what they show with `data-field="headline"`, `data-field="source.outlet"`, `data-href="source.url"` and `data-photo`. `fill()` in `main.js` populates them using `textContent`, never `innerHTML`.
- **Scrollytelling:** `.scrolly` contains `.step[data-step="n"]` blocks and chart layers marked `[data-hl="n"]`. An IntersectionObserver sets `.scrolly[data-active]` and CSS fades the matching layer in. On narrow screens and with reduced motion, every layer stays visible.
- **The header and footer are duplicated** in all four HTML files. Change all four together, or introduce templating (see backlog) before the page count grows.

## Design rules (summary; full detail in `docs/design-system.md`)

- **Use the tokens.** Colors, fonts and the container width are CSS variables in `:root` of `styles.css`. Don't hard-code hex values in new work. A few chart SVGs still carry literal fills; that's fine for illustrative charts.
- **Palette:** MI Blue `#2C48DC` is the primary accent. Slate Black `#20222E` is used for text and dark bands. Sky Blue tints are for grounds. MI Orange `#FF8000` is for **actions and key callouts only**; primary buttons are orange with slate text, because white on orange fails contrast.
- **Type:** serif (Source Serif 4, standing in for MI's Le Monde Livre Pro) for headings and body text. Sans (Public Sans, standing in for GT America) for labels, metadata, nav, buttons and chart text.
- **The "crosswalk" slash** is MI's forward-slanting bar motif. Use it as a decorative accent, always forward-slanting and always on the right side of the element it accompanies. Don't recreate the MI logo itself.
- **Charts:** Slate Black for most information (axes, labels, text), MI Blue for titles/headers and the emphasized series, MI data colors (grey, cyan, etc.) to distinguish other series. Label anything illustrative as such.
- **Accessibility:** real `<button>`, `<a>` and `<label>` elements, 44px minimum touch targets, 4.5:1 text contrast, `aria-hidden` on decorative SVGs and `role="img"` + `aria-label` on charts. Pages must work at phone width with no horizontal scroll.

## Content rules

See the content rules in the repo-root `CLAUDE.md`. They apply to everything here.

## Backlog / suggested next steps

1. Replace the placeholder copy. The cases are real now (generated from `database/` by `build.py`).
2. Add the colleague's database to the build. (The serious-mental-illness category is in: `smi/` incidents arrive through `database/exports/`.)
3. Add a story detail page (e.g. `story.html?id=case-001`) and link cards to it.
4. Drive the Facts and Solve charts from real data. Hand-built SVG is fine, or move to a chart library if they multiply.
5. Wire the sign-up forms to the email provider (`TODO` in `initSignup`) and add a privacy policy.
6. Build "Submit a case" and "Methodology" pages, which are linked from the footer and stories page.
7. If the header, footer and page count become painful to maintain, move to a static-site generator (Eleventy or Astro). The CSS and JS carry over as-is.
8. Before launch: remove `noindex` and the draft banner, Open Graph and social tags, favicon, analytics, a custom domain, and a Lighthouse/accessibility pass. Wording about named people needs editorial and legal review (see the root `CLAUDE.md`).

## design-source/

These are the original artboards from the Claude Design canvas ("Preventable Tragedies Mockup", https://claude.ai/artifact/JEfeCRBWw4KhM6ABedHdFA). They use a canvas-only component format (`<x-dc>`, `<sc-for>`, `{{holes}}`) and **won't render outside that editor**. Use them only to compare against the design. The HTML pages in `site/` are the working source.
