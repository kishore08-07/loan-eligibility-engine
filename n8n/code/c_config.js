// Guard rails for notifications: authenticate the caller and refuse to email real CSV addresses
// unless NOTIFY_LIVE=true. Normally every mail goes to an alias of TEST_RECIPIENT.
const headers = $input.first().json.headers || {};
if (!$env.WEBHOOK_SECRET || headers['x-internal-token'] !== $env.WEBHOOK_SECRET) {
  throw new Error('Unauthorised call to notify-users webhook');
}
const jobId = ($input.first().json.body || {}).upload_job_id;
if (!/^[0-9a-f-]{36}$/.test(jobId || '')) throw new Error('upload_job_id missing or invalid');

const live = $env.NOTIFY_LIVE === 'true';
const base = String($env.TEST_RECIPIENT || '').trim();
if (!live && !/^[^@\s+]+@[^@\s]+$/.test(base)) {
  throw new Error('Set TEST_RECIPIENT (e.g. abcd@gmail.com): refusing to email addresses from the CSV');
}
const transport = String($env.EMAIL_TRANSPORT || 'smtp').toLowerCase();
if (transport === 'ses' && !$env.SES_FROM) throw new Error('EMAIL_TRANSPORT=ses requires SES_FROM');
const max = parseInt($env.NOTIFY_MAX_EMAILS || '', 10);
return [{ json: { upload_job_id: jobId, limit: Number.isFinite(max) && max > 0 ? max : 1000000, transport } }];
