// Ask the LLM to extract products from the page text, then validate before anything is written.
// @include _llm.js
const EMPLOYMENT = ['Salaried', 'Self-Employed', 'Business'];
const num = (v) => (typeof v === 'number' && isFinite(v) && v >= 0 ? v : null);
const PROMPT =
  'You extract personal loan products from a lender web page. Respond with a single JSON object ' +
  '{"products": [...]}. Each product: {product_name, interest_rate (lowest annual % as a number or null), ' +
  'min_income (minimum MONTHLY income as a number or null), min_credit_score, max_credit_score, min_age, max_age, ' +
  'allowed_employment (array of "Salaried", "Self-Employed", "Business" or null), criteria_text (the page\'s ' +
  'eligibility wording for this product, verbatim, or null)}. Only include personal loan products actually ' +
  'described on the page. Use null for anything not stated. Do not invent values.\n\nPAGE TEXT:\n';

const meta = $json;
let raw = [];
let error = null;
try {
  const parsed = await callLLM(this, PROMPT + meta.text);
  raw = Array.isArray(parsed) ? parsed : (parsed.products || []);
} catch (e) {
  error = e.message; // nothing is written and the hash is not stored, so the next run retries
}

const products = [];
for (const p of raw) {
  if (!p || typeof p.product_name !== 'string' || !p.product_name.trim()) continue;
  const allowed = Array.isArray(p.allowed_employment) ? p.allowed_employment.filter((e) => EMPLOYMENT.includes(e)) : [];
  const row = {
    product_name: p.product_name.trim().slice(0, 200),
    interest_rate: num(p.interest_rate),
    min_income: num(p.min_income), min_credit_score: num(p.min_credit_score), max_credit_score: num(p.max_credit_score),
    min_age: num(p.min_age), max_age: num(p.max_age),
    allowed_employment: allowed.length ? allowed : null,
    criteria_text: typeof p.criteria_text === 'string' && p.criteria_text.trim() ? p.criteria_text.trim().slice(0, 2000) : null,
  };
  if (row.min_income == null && row.min_credit_score == null && !row.criteria_text) continue; // no criteria at all
  products.push(row);
}
return { json: { source: meta.source, content_hash: meta.content_hash, products, error } };
