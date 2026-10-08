// Stage 0: work only on the users of THIS upload, in fixed-size batches.
// The scoring weights and TOP_K live here (in n8n) and are passed to the SQL stage as parameters.
const BATCH_SIZE = 1000;
const CONFIG = { w_income: 0.35, w_credit: 0.25, w_rate: 0.4, top_k: 5 };
const { upload_job_id } = $('Verify Signature').first().json;
const start = $('Start Run').first().json;
if (!Number(start.job_found)) {
  throw new Error(`Upload job ${upload_job_id} is not in a COMPLETED state; nothing to match`);
}
const batches = Math.max(1, Math.ceil(Number(start.users_count) / BATCH_SIZE));
return Array.from({ length: batches }, (_, i) => ({
  json: { upload_job_id, offset: i * BATCH_SIZE, batch_size: BATCH_SIZE, ...CONFIG },
}));
