# Preventable Tragedies — project brief

A Manhattan Institute site that documents "preventable tragedies": serious crimes committed by people who, under better policy, would already have been detained, supervised or in treatment. It covers three groups: frequent violent offenders, habitual offenders whose records escalate, and people with untreated serious mental illness. The site is a sourced repository of these cases, plus the research and policy proposals behind them.

**Status:** design mockup. All copy is placeholder (`[Bracketed descriptions]` + lorem ipsum). Chart values are illustrative. Mugshots are SVG placeholders. Nothing here is real data yet.

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
  js/stories-data.js    the story "database": window.STORIES + window.CATEGORIES, with a documented schema
  js/main.js            all behavior: random case, sign-up, index filters, scrollytelling
docs/design-system.md   MI brand rules this design follows (colors, type, slash motif, charts)
design-source/          the original Claude Design canvas files (reference only; see below)
```

## Running it

There's no build step and no dependencies. Open `index.html` in a browser, or serve the folder:

```
python3 -m http.server 8000   # then visit http://localhost:8000
```

`stories-data.js` is a plain script, not JSON loaded with `fetch`, so the site also works from `file://`.

## How the code is organized

- **Static HTML + one stylesheet + one script.** Every page loads `styles.css` and `main.js`. Pages that show cases (`index`, `stories`) also load `stories-data.js` first.
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

## Content rules — important for this subject

- **Every case must be sourced** and link to its original reporting or court record. Never invent cases, names, quotes or statistics in anything that could ship.
- Keep the distinction between **arrests, charges and convictions** accurate in copy and data fields. Wording about named individuals needs editorial and legal review before launch.
- **Booking photos:** confirm usage rights and the source agency. The `photo` field is `null` until then.
- **Facts page figures** must come from the cited research (the numbered sources list). Keep the "what the data does not show" context box in the mental-illness chapter.
- Placeholders use `[Square brackets]`, so `grep -n "\[" *.html` finds what's left to write.

## Backlog / suggested next steps

1. Replace placeholder copy and the nine sample records in `stories-data.js` with real, sourced cases (the schema is documented at the top of that file).
2. Decide where the case database lives (Airtable, a CMS or a JSON file in the repo) and load it at build time or runtime, keeping the same field names.
3. Add a story detail page (e.g. `story.html?id=case-001`) and link cards to it.
4. Drive the Facts and Solve charts from real data. Hand-built SVG is fine, or move to a chart library if they multiply.
5. Wire the sign-up forms to the email provider (`TODO` in `initSignup`) and add a privacy policy.
6. Build "Submit a case" and "Methodology" pages, which are linked from the footer and stories page.
7. If the header, footer and page count become painful to maintain, move to a static-site generator (Eleventy or Astro). The CSS and JS carry over as-is.
8. Before launch: Open Graph and social tags, favicon, analytics, a deploy target, and a Lighthouse/accessibility pass.

## design-source/

These are the original artboards from the Claude Design canvas ("Preventable Tragedies Mockup", https://claude.ai/artifact/JEfeCRBWw4KhM6ABedHdFA). They use a canvas-only component format (`<x-dc>`, `<sc-for>`, `{{holes}}`) and **won't render outside that editor**. Use them only to compare against the design. The HTML pages in the project root are the working source.
