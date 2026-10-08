// Strip a fetched page down to readable text, hash it, and flag whether it changed since last crawl.
const crypto = require('crypto');
const MAX_CHARS = 60000;
const sources = $('Sources').all();
return $input.all().map((item, i) => {
  const meta = sources[i].json;
  const html = typeof item.json.data === 'string' ? item.json.data : '';
  if (item.json.error || !html) {
    return { json: { source: meta.source, ok: false, changed: false, reason: String(item.json.error?.message || item.json.error || 'empty response') } };
  }
  const text = html
    .replace(/<(script|style|noscript|svg)[\s\S]*?<\/\1>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ').trim().slice(0, MAX_CHARS);
  const hash = crypto.createHash('sha256').update(text).digest('hex');
  return { json: { source: meta.source, url: meta.url, ok: text.length > 500, changed: hash !== meta.previous_hash, content_hash: hash, text } };
});
