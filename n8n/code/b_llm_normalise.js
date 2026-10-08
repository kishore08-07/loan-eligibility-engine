// Stage 3: the LLM is used ONLY to turn free-text eligibility wording into structured thresholds,
// once per distinct wording (results cached in llm_cache). It is never called per user or per pair.
// @include _llm.js
const EMPLOYMENT = ['Salaried', 'Self-Employed', 'Business'];

const todo = [];
const seen = new Set();
for (const item of $input.all()) {
  const r = item.json;
  if (!r.cache_key || seen.has(r.cache_key)) continue;
  seen.add(r.cache_key);
  todo.push(r);
}

const num = (v) => (typeof v === 'number' && isFinite(v) && v >= 0 ? v : null);
const rows = [];
const errors = [];
let calls = 0;

const configError = llmConfigError();
if (todo.length && configError) {
  errors.push(`${configError}: ${todo.length} product(s) with free-text criteria were skipped`);
} else {
  for (const r of todo) {
    const prompt =
      'Extract loan eligibility thresholds from the text below. Respond with a single JSON object using exactly these keys: ' +
      'min_income (minimum MONTHLY income as a number, convert yearly figures to monthly), min_credit_score, ' +
      'max_credit_score, min_age, max_age, allowed_employment (array containing any of "Salaried", ' +
      '"Self-Employed", "Business"; null if not restricted). Use null for anything not stated. ' +
      'Do not guess.\n\nTEXT:\n' + r.criteria_text;
    let parsed;
    try {
      calls++;
      parsed = await callLLM(this, prompt);
    } catch (e) {
      errors.push(`${r.product_name}: ${e.message}`);
      continue;
    }
    // Sanity-check before caching: a wrong extraction would silently mis-match thousands of users.
    const t = { min_income: num(parsed.min_income), min_credit_score: num(parsed.min_credit_score),
                max_credit_score: num(parsed.max_credit_score), min_age: num(parsed.min_age), max_age: num(parsed.max_age) };
    const implausible =
      (t.min_income != null && (t.min_income < 1000 || t.min_income > 10000000)) ||
      [t.min_credit_score, t.max_credit_score].some((v) => v != null && (v < 300 || v > 900)) ||
      [t.min_age, t.max_age].some((v) => v != null && (v < 18 || v > 100)) ||
      (t.min_credit_score != null && t.max_credit_score != null && t.min_credit_score > t.max_credit_score) ||
      (t.min_age != null && t.max_age != null && t.min_age > t.max_age);
    if (implausible) {
      errors.push(`${r.product_name}: implausible extraction ${JSON.stringify(t)} (not cached)`);
      continue;
    }
    const allowed = Array.isArray(parsed.allowed_employment)
      ? parsed.allowed_employment.filter((e) => EMPLOYMENT.includes(e)) : [];
    rows.push({
      cache_key: r.cache_key,
      result: {
        min_income: num(parsed.min_income), min_credit_score: num(parsed.min_credit_score),
        max_credit_score: num(parsed.max_credit_score), min_age: num(parsed.min_age),
        max_age: num(parsed.max_age), allowed_employment: allowed.length ? allowed : null,
      },
    });
  }
}
const uploadJobId = $('Verify Signature').first().json.upload_job_id;
return [{ json: { upload_job_id: uploadJobId, rows, llm_calls: calls, errors } }];
