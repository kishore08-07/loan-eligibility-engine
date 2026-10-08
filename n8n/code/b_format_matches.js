// Stage 2 result: Postgres already applied the hard criteria, scored each pair with the weights from
// "Plan Batches" and kept the top-K products per user, so only those rows reach n8n.
// Here we just shape them for the bulk insert and report how many pairs survived stage 1.
const uploadJobId = $('Verify Signature').first().json.upload_job_id;
const rows = $input.all().map((i) => i.json).filter((r) => r.product_id != null); // drop the empty-batch placeholder
return [{
  json: {
    upload_job_id: uploadJobId,
    pairs_after_sql: rows.length ? Number(rows[0].eligible_total) : 0,
    rows: rows.map((r) => ({
      user_id: r.user_id,
      product_id: Number(r.product_id),
      score: Number(r.score),
      match_stage: r.via_llm ? 'rules_llm' : 'rules',
      reasons: { income_fit: +Number(r.income_fit).toFixed(2), credit_fit: +Number(r.credit_fit).toFixed(2),
                 rate_fit: +Number(r.rate_fit).toFixed(2) },
    })),
  },
}];
