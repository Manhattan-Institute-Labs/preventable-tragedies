# Preventable Tragedies site (first draft)

A static four-page site for Preventable Tragedies, a project of the Manhattan Institute. The landing page and the stories index show real cases from [`../database/`](../database/); everything else is still placeholder.

- `index.html`: landing page (random case, sign-up)
- `stories.html`: the case index (filters, search)
- `facts.html`: the research explainer (scrollytelling charts)
- `solve.html`: five policy proposals

**To preview:** from the repo root, run `python3 site/build.py`, then `python3 -m http.server -d _site 8000` and visit http://localhost:8000.

**Publishing:** `.github/workflows/pages.yml` builds and deploys to GitHub Pages on every push to `site/` or the exports, and after each data workflow run.

Copy, photos and chart values are placeholders.

**Working on it with Claude Code:** open the repo and start a session. `CLAUDE.md` holds the project brief, conventions and backlog. `docs/design-system.md` covers the MI brand rules the design follows.
