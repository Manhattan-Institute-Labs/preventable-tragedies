# Preventable Tragedies

A Manhattan Institute site that documents "preventable tragedies": serious crimes committed by people who, under better policy, would already have been detained, supervised or in treatment. It covers three groups: frequent violent offenders, habitual offenders whose records escalate, and people with untreated serious mental illness. The site is a sourced repository of these cases, plus the research and policy proposals behind them.

## Repo layout

- `mockup/`: static four-page design mockup of the site (placeholder content). Its own `CLAUDE.md` covers structure, conventions, design rules and backlog.

Other parts of the project (data, research, tooling) will get their own top-level folders.

## Content rules — important for this subject

- **Every case must be sourced** and link to its original reporting or court record. Never invent cases, names, quotes or statistics in anything that could ship.
- Keep the distinction between **arrests, charges and convictions** accurate in copy and data fields. Wording about named individuals needs editorial and legal review before launch.
- **Booking photos:** confirm usage rights and the source agency. The `photo` field is `null` until then.
- **Facts page figures** must come from the cited research (the numbered sources list). Keep the "what the data does not show" context box in the mental-illness chapter.
- Placeholders use `[Square brackets]`, so `grep -n "\[" mockup/*.html` finds what's left to write.
