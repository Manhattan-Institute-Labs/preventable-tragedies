/* ==========================================================================
   Story database (placeholder).
   Loaded as a plain script so the site works from file:// with no server.
   When this moves to a real backend/CMS, keep the same field names and the
   page templates will keep working.

   Schema
   ------
   id            string   unique slug
   name          string   subject's name as reported
   age           string|number
   city, state   string
   date          string   ISO date of the offense (YYYY-MM-DD) once real
   category      'violent' | 'escalating' | 'smi'   (see CATEGORIES)
   headline      string   one-line description of the crime
   summary       string   2–3 sentence sourced summary
   priorArrests  number|string
   priorViolent  number|string
   status        string   release status at time of offense
   source        { outlet: string, url: string, date?: string }
   photo         string|null  path/URL to booking photo; null = placeholder
   featured      boolean  eligible for the landing page's random case
   ========================================================================== */

window.CATEGORIES = {
  violent: 'Violent repeat offender',
  escalating: 'Escalating offender',
  smi: 'Serious mental illness'
};

window.STORIES = [
  { id: 'case-001', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'escalating',
    headline: '[Headline] Lorem ipsum dolor sit amet consectetur',
    summary: 'Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua ut enim ad minim.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: true },
  { id: 'case-002', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'violent',
    headline: '[Headline] Ut enim ad minim veniam quis nostrud',
    summary: 'Exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat duis aute.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: true },
  { id: 'case-003', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'smi',
    headline: '[Headline] Duis aute irure dolor in reprehenderit',
    summary: 'In voluptate velit esse cillum dolore eu fugiat nulla pariatur excepteur sint.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: true },
  { id: 'case-004', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'violent',
    headline: '[Headline] Excepteur sint occaecat cupidatat non',
    summary: 'Proident sunt in culpa qui officia deserunt mollit anim id est laborum sed ut.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: true },
  { id: 'case-005', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'escalating',
    headline: '[Headline] Sed ut perspiciatis unde omnis iste',
    summary: 'Natus error sit voluptatem accusantium doloremque laudantium totam rem aperiam.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: false },
  { id: 'case-006', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'smi',
    headline: '[Headline] Nemo enim ipsam voluptatem quia voluptas',
    summary: 'Sit aspernatur aut odit aut fugit sed quia consequuntur magni dolores eos qui.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: false },
  { id: 'case-007', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'violent',
    headline: '[Headline] Neque porro quisquam est qui dolorem',
    summary: 'Ipsum quia dolor sit amet consectetur adipisci velit sed quia non numquam eius.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: false },
  { id: 'case-008', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'escalating',
    headline: '[Headline] Quis autem vel eum iure reprehenderit',
    summary: 'Qui in ea voluptate velit esse quam nihil molestiae consequatur vel illum qui.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: false },
  { id: 'case-009', name: '[Name]', age: '[Age]', city: '[City]', state: '[ST]', date: '[Date]', category: 'smi',
    headline: '[Headline] At vero eos et accusamus et iusto odio',
    summary: 'Dignissimos ducimus qui blanditiis praesentium voluptatum deleniti atque corrupti.',
    priorArrests: '[00]', priorViolent: '[0]', status: '[Release status]',
    source: { outlet: '[Source outlet]', url: 'https://example.com' }, photo: null, featured: false }
];
