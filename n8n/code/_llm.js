// ---- shared LLM helper (inlined into Code nodes by build_workflows.py) ----
// LLM_PROVIDER=gemini (hosted, rate-limited free tier) | ollama (local open model, unlimited, no key)
const LLM_PROVIDER = String($env.LLM_PROVIDER || 'gemini').toLowerCase();
function llmConfigError() {
  if (LLM_PROVIDER === 'gemini' && !$env.GEMINI_API_KEY) return 'LLM_PROVIDER=gemini but GEMINI_API_KEY is not set';
  if (LLM_PROVIDER !== 'gemini' && LLM_PROVIDER !== 'ollama') return `unknown LLM_PROVIDER "${LLM_PROVIDER}"`;
  return null;
}
// Returns the model's answer parsed as JSON. Retries transient failures with backoff.
async function callLLM(ctx, prompt) {
  let lastError;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      let text;
      if (LLM_PROVIDER === 'ollama') {
        const resp = await ctx.helpers.httpRequest({
          method: 'POST', url: `${$env.OLLAMA_URL || 'http://ollama:11434'}/api/chat`, json: true, timeout: 300000,
          body: { model: $env.OLLAMA_MODEL || 'qwen2.5:3b', stream: false, format: 'json',
                  options: { temperature: 0 }, messages: [{ role: 'user', content: prompt }] },
        });
        text = resp.message.content;
      } else {
        const model = $env.GEMINI_MODEL || 'gemini-3.5-flash-lite';
        const resp = await ctx.helpers.httpRequest({
          method: 'POST', json: true, timeout: 90000,
          url: `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
          headers: { 'x-goog-api-key': $env.GEMINI_API_KEY, 'Content-Type': 'application/json' },
          body: { contents: [{ parts: [{ text: prompt }] }],
                  generationConfig: { responseMimeType: 'application/json', temperature: 0 } },
        });
        text = resp.candidates[0].content.parts.find((p) => typeof p.text === 'string').text;
      }
      return JSON.parse(text);
    } catch (e) {
      lastError = e;
      if (attempt < 3) await new Promise((r) => setTimeout(r, 2000 * attempt));
    }
  }
  throw lastError;
}
// ---- end shared helper ----
