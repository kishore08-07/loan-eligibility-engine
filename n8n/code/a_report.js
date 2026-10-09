// Fail the run loudly if any source could not be extracted, after all sources were stored.
// (crawl_state.last_status already records the error; this makes the execution show up red in n8n.)
const failed = $('LLM Extract Products').all().filter((i) => i.json.error).map((i) => `${i.json.source}: ${i.json.error}`);
if (failed.length) throw new Error(`Product extraction failed for ${failed.join(' | ')}`);
return $input.all();
