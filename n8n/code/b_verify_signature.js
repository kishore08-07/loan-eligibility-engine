// Verify the HMAC-SHA256 signature the ingest Lambda puts on the webhook:
//   X-Signature: sha256=HMAC(WEBHOOK_SECRET, "<X-Timestamp>.<raw body>")
// and reject anything older than 5 minutes (replay protection).
const crypto = require('crypto');
const headers = $input.first().json.headers || {};
const raw = await this.helpers.getBinaryDataBuffer(0, 'data');
const ts = headers['x-timestamp'];
const sig = String(headers['x-signature'] || '').replace(/^sha256=/, '');
const expected = crypto.createHmac('sha256', $env.WEBHOOK_SECRET || '').update(`${ts}.`).update(raw).digest('hex');

let valid = Boolean($env.WEBHOOK_SECRET) && Boolean(ts) && sig.length === expected.length
  && crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))
  && Math.abs(Date.now() / 1000 - Number(ts)) <= 300;

let uploadJobId = null;
if (valid) {
  try { uploadJobId = JSON.parse(raw.toString('utf8')).upload_job_id; } catch (e) { valid = false; }
  valid = valid && /^[0-9a-f-]{36}$/.test(uploadJobId || '');
}
return [{ json: { valid, upload_job_id: uploadJobId } }];
