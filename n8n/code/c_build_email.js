// One email per distinct recipient address. CSV values are untrusted: always HTML-escape them.
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const live = $env.NOTIFY_LIVE === 'true';
const [baseLocal, baseDomain] = String($env.TEST_RECIPIENT || '').trim().split('@');
const from = $env.SES_FROM || 'loans@example.test';
const transport = String($env.EMAIL_TRANSPORT || 'smtp').toLowerCase();

const out = [];
let n = 0;
for (const item of $input.all()) {
  const r = item.json;
  if (!r.email) continue; // placeholder item from an empty result
  n += 1;
  const tag = String(n); // abcd+1, abcd+2, ... : a small, predictable set of aliases to verify in the SES sandbox
  const to = live ? r.email : `${baseLocal}+${tag}@${baseDomain}`;
  const products = (r.products || []).slice(0, 10);
  const rows = products.map((p) => `<tr><td>${esc(p.product_name)}</td>` +
    `<td>${p.interest_rate == null ? '-' : esc(p.interest_rate) + '%'}</td>` +
    `<td>${p.min_income == null ? '-' : esc(p.min_income)}</td>` +
    `<td>${p.min_credit_score == null ? '-' : esc(p.min_credit_score)}</td></tr>`).join('');
  const html = `<p>Hi ${esc(r.name || 'there')},</p>` +
    `<p>Based on your profile, you may qualify for these personal loans:</p>` +
    `<table border="1" cellpadding="6" cellspacing="0"><tr><th>Product</th><th>Interest rate</th>` +
    `<th>Min. monthly income</th><th>Min. credit score</th></tr>${rows}</table>` +
    `<p>This is an indication only; final approval is up to the lender.</p>` +
    (live ? '' : `<p><small>Test mode: originally addressed to ${esc(r.email)}</small></p>`);
  out.push({ json: { to, from, subject: `You may qualify for ${products.length} personal loan offer(s)`,
                     html, match_ids: r.match_ids, original_email: r.email, transport } });
}
return out.length ? out : [{ json: { empty: true } }];
