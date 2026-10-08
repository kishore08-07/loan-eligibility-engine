// List of public pages to crawl (edit freely) + crawl state from the previous run.
// @include _llm.js
if (llmConfigError()) {
  throw new Error(`${llmConfigError()}. The crawler needs an LLM to extract products; seeded products stay active.`);
}
const state = {};
for (const r of $('Load Crawl State').all()) if (r.json.source) state[r.json.source] = r.json.content_hash;
const SOURCES = [
  { source: 'bankbazaar', url: 'https://www.bankbazaar.com/personal-loan.html' },
  { source: 'hdfcbank', url: 'https://www.hdfcbank.com/personal/borrow/popular-loans/personal-loan' },
];
return SOURCES.map((s) => ({ json: { ...s, previous_hash: state[s.source] || null } }));
