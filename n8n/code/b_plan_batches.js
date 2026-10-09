// Stage 0: work only on the users of THIS upload, in fixed-size batches.
// The scoring weights and TOP_K live here (in n8n) and are passed to the SQL stage as parameters.
const BATCH_SIZE = 1000;
// MATCH_TOP_K: 'all' (default) stores every eligible match; a number keeps only the best K products per user.
const topK = String($env.MATCH_TOP_K || 'all').toLowerCase();
const CONFIG = { w_income: 0.35, w_credit: 0.25, w_rate: 0.4,
                 top_k: topK === 'all' || Number(topK) <= 0 || !Number.isFinite(Number(topK)) ? 1000000 : Math.floor(Number(topK)) };
const { upload_job_id } = $('Verify Signature').first().json;
const start = $('Start Run').first().json;
if (!Number(start.job_found)) {
  throw new Error(`Upload job ${upload_job_id} is not in a COMPLETED state; nothing to match`);
}
const batches = Math.max(1, Math.ceil(Number(start.users_count) / BATCH_SIZE));
return Array.from({ length: batches }, (_, i) => ({
  json: { upload_job_id, offset: i * BATCH_SIZE, batch_size: BATCH_SIZE, ...CONFIG },
}));
