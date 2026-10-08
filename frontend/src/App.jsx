import React, { useCallback, useEffect, useRef, useState } from "react";
import { getMatches, getUpload, health, listUploads, onApiCall, uploadCsv } from "./api.js";

const REQUIRED = ["user_id", "email", "monthly_income", "credit_score", "employment_status", "age"];

async function checkHeader(file) {
  const head = await file.slice(0, 4096).text();
  const cols = head.split(/\r?\n/)[0].split(",").map((c) => c.trim());
  const missing = REQUIRED.filter((c) => !cols.includes(c));
  return missing.length ? `Missing columns: ${missing.join(", ")}` : null;
}

function Svg({ d, size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
  );
}

function Badge({ value }) {
  return <span className={`badge ${String(value).toLowerCase()}`}>{String(value).replaceAll("_", " ")}</span>;
}

function Stats({ job }) {
  const f = job.funnel;
  const items = [
    ["Applicants loaded", job.valid_rows],
    ["Candidate pairs", f?.pairs_naive],
    ["After pre-filter", f?.pairs_after_sql],
    ["Eligible offers", job.matches],
  ];
  return (
    <div className="stats">
      {items.map(([l, n]) => (
        <div className="stat" key={l}><b>{n == null ? "–" : Number(n).toLocaleString()}</b><span>{l}</span></div>
      ))}
    </div>
  );
}

// Derive what each backend component is doing from the job row.
function stagesFor(job, uploading, progress) {
  const ing = job?.status, mat = job?.matching_status;
  const ingestDone = ing === "COMPLETED", ingestBad = ing === "FAILED" || ing === "DUPLICATE";
  const s = (state, d) => ({ state, d });
  return [
    { t: "Reserve upload", tag: "API · Lambda",
      ...(job || uploading ? s("done", "Job recorded, single-use upload URL issued") : s("wait", "Awaiting file")) },
    { t: "Transfer to storage", tag: "S3",
      ...(job ? s("done", "Encrypted object stored in the uploads bucket") : uploading ? s("active", `Streaming straight to storage · ${progress}%`) : s("wait", "Sent directly from the browser, never through the API")) },
    { t: "Queue for processing", tag: "SQS",
      ...(!job ? s("wait", "Storage event enqueued; dead-letter after 3 attempts") : ing === "PENDING" ? s("active", "Waiting for a worker to pick it up") : s("done", "Picked up by a worker")) },
    { t: "Validate and load", tag: "Lambda · RDS",
      ...(!job ? s("wait", "Row-level validation, then a bulk load") : ingestBad ? s("error", job.error_message || ing)
        : ing === "PROCESSING" ? s("active", "Reading rows and loading them into Postgres")
        : ingestDone ? s("done", `${job.valid_rows.toLocaleString()} accepted · ${job.rejected_rows} rejected`) : s("wait", "Pending")) },
    { t: "Evaluate eligibility", tag: "n8n",
      ...(!ingestDone ? s("wait", "Pre-filter, score against product rules, keep the best fits") : mat === "FAILED" ? s("error", "The matching workflow did not complete")
        : mat === "DONE" ? s("done", `${job.matches.toLocaleString()} eligible offers selected`)
        : mat === "RUNNING" ? s("active", "Scoring applicants against active products") : s("active", "Handing off to the matching workflow")) },
    { t: "Deliver offers", tag: "SES",
      ...(mat !== "DONE" ? s("wait", "One email per eligible applicant") : job.notified >= job.matches || job.matches === 0
        ? s("done", `${job.notified.toLocaleString()} delivered`) : s("active", `${job.notified.toLocaleString()} of ${job.matches.toLocaleString()} delivered`)) },
  ];
}

function Pipeline({ job, uploading, progress }) {
  const icon = { done: <Svg d="M5 12.5l4.5 4.5L19 7.5" />, error: <Svg d="M7 7l10 10M17 7L7 17" />, active: <i className="spin" />, wait: null };
  return (
    <ol className="pipeline">
      {stagesFor(job, uploading, progress).map((x, i) => (
        <li key={x.t} className={`stage ${x.state}`}>
          <span className="node">{icon[x.state]}</span>
          <div><div className="t">{x.t}</div><div className="d">{x.d}</div></div>
          <span className="tag">{x.tag}</span>
        </li>
      ))}
    </ol>
  );
}

function Log({ lines }) {
  const ref = useRef();
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [lines]);
  return (
    <div className="log" ref={ref}>
      {lines.length === 0 && <span className="ts">Nothing to show yet. Run the pipeline to see requests and state changes here.</span>}
      {lines.map((l) => (
        <div key={l.id}><span className="ts">{l.ts}</span> <span className={l.kind}>{l.text}</span></div>
      ))}
    </div>
  );
}

function Matches({ jobId }) {
  const [page, setPage] = useState({ items: [], total: 0 });
  const [offset, setOffset] = useState(0);
  const limit = 25;
  useEffect(() => {
    getMatches(jobId, offset, limit).then(setPage).catch(() => {});
  }, [jobId, offset]);
  if (!page.total) return <p className="muted">No eligible offers for this file.</p>;
  return (
    <>
      <table>
        <thead><tr><th>User</th><th>Email</th><th>Product</th><th>Rate</th><th>Score</th><th>Sent</th></tr></thead>
        <tbody>
          {page.items.map((m, i) => (
            <tr key={`${m.user_id}-${m.product_name}-${i}`}>
              <td>{m.name || m.user_id}</td><td>{m.email}</td><td>{m.product_name}</td>
              <td>{m.interest_rate == null ? "-" : `${m.interest_rate}%`}</td>
              <td className="num">{m.score}</td><td>{m.notified_at ? "yes" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="pager">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</button>
        <span>{offset + 1}–{Math.min(offset + limit, page.total)} of {page.total.toLocaleString()}</span>
        <button disabled={offset + limit >= page.total} onClick={() => setOffset(offset + limit)}>Next</button>
      </div>
    </>
  );
}

function JobDetail({ jobId, onEvent }) {
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let stop = false;
    let timer;
    let settledPolls = 0; // after matching ends, keep polling briefly so the "emailed" count catches up
    const tick = async () => {
      try {
        const j = await getUpload(jobId);
        if (stop) return;
        setJob((prev) => {
          if (!prev || prev.status !== j.status) onEvent(`load: ${j.status.toLowerCase()}`);
          if (!prev || prev.matching_status !== j.matching_status) onEvent(`matching: ${j.matching_status.toLowerCase().replace("_", " ")}`);
          if (prev && prev.notified !== j.notified) onEvent(`delivered: ${j.notified}/${j.matches}`);
          return j;
        });
        setError(null);
        const terminal = j.status === "FAILED" || j.status === "DUPLICATE" || j.matching_status === "FAILED";
        if (terminal) return;
        if (j.matching_status === "DONE") {
          if (++settledPolls > 12) return;
          timer = setTimeout(tick, 5000);
        } else {
          timer = setTimeout(tick, 2000);
        }
      } catch (e) {
        if (!stop) { setError(e.message); timer = setTimeout(tick, 5000); }
      }
    };
    tick();
    return () => { stop = true; clearTimeout(timer); };
  }, [jobId]);

  if (!job) return <p className="muted">{error || "Loading…"}</p>;
  return (
    <section className="card">
      <div className="job-head">
        <span className="name">{job.filename}</span><Badge value={job.status} />
      </div>
      {error && <p className="error">{error}</p>}
      {job.rejected_rows > 0 && job.error_report_url &&
        <p className="muted">{job.rejected_rows} rows rejected · <a href={job.error_report_url}>download error report</a></p>}
      <Pipeline job={job} />
      <Stats job={job} />
      {job.matches > 0 && <div style={{ marginTop: 24 }}><Matches jobId={jobId} /></div>}
    </section>
  );
}

export default function App() {
  const [file, setFile] = useState(null);
  const [fileError, setFileError] = useState(null);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [history, setHistory] = useState([]);
  const [over, setOver] = useState(false);
  const [online, setOnline] = useState(null);
  const [logs, setLogs] = useState([]);
  const input = useRef();
  const seq = useRef(0);

  const addLog = useCallback((text, kind = "") => {
    const ts = new Date().toLocaleTimeString([], { hour12: false });
    setLogs((l) => [...l.slice(-199), { id: seq.current++, ts, text, kind }]);
  }, []);

  useEffect(() => onApiCall((e) => addLog(`${e.method} ${e.path.split("?")[0]} → ${e.status} (${e.ms} ms)`,
    e.status === "ERR" || e.status >= 400 ? "err" : "ok")), [addLog]);

  useEffect(() => {
    const ping = () => health().then(() => setOnline(true)).catch(() => setOnline(false));
    ping();
    const t = setInterval(ping, 30000);
    return () => clearInterval(t);
  }, []);

  const refresh = useCallback(() => listUploads().then((r) => setHistory(r.items)).catch(() => {}), []);
  useEffect(() => { refresh(); }, [refresh, jobId]);

  async function pick(f) {
    setFile(f); setError(null);
    setFileError(f ? (f.name.toLowerCase().endsWith(".csv") ? await checkHeader(f) : "File must be a .csv") : null);
  }

  async function submit() {
    setError(null); setProgress(0);
    try {
      addLog(`transfer started: ${file.name} (${file.size} bytes)`, "ev");
      const id = await uploadCsv(file, setProgress);
      addLog(`transfer complete · job ${id.slice(0, 8)}`, "ev");
      setJobId(id); setFile(null);
      if (input.current) input.current.value = "";
    } catch (e) {
      setError(e.message);
    } finally {
      setProgress(null);
    }
  }

  return (
    <main>
      <header>
        <span className="brand"><Svg d="M4 17l5-5 4 4 7-8" size={16} /> Lendlens</span>
        <span className="right">
        <span className="sys"><i className={`dot ${online === null ? "" : online ? "ok" : "err"}`} />
          {online === null ? "Connecting" : online ? "Operational" : "Unreachable"}</span></span>
      </header>
      <h1>Match applicants to the right credit products</h1>
      <p className="sub">Upload user data and Lendlens automatically matches every applicant with the loan products they qualify for.</p>

      <section className="card">
        <h2>Applicant file</h2>
        <label className={`drop ${over ? "over" : ""}`}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0] || null); }}>
          <input ref={input} type="file" accept=".csv,text/csv" onChange={(e) => pick(e.target.files[0] || null)} />
          <Svg d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" size={30} />
          <b>{file ? file.name : "Drop a CSV, or browse"}</b>
          <span className="muted">{file ? `${(file.size / 1024).toFixed(1)} KB` : REQUIRED.join(", ")}</span>
        </label>
        {fileError && <p className="error">{fileError}</p>}
        {progress !== null && <div className="bar"><i style={{ width: `${progress}%` }} /></div>}
        <button className="btn" disabled={!file || fileError || progress !== null} onClick={submit}>
          {progress === null ? "Run pipeline" : `Transferring · ${progress}%`}
        </button>
        {error && <p className="error">{error}</p>}
      </section>

      {progress !== null && !jobId && (
        <section className="card"><h2>Progress</h2><Pipeline job={null} uploading progress={progress} /></section>
      )}
      {jobId && <JobDetail key={jobId} jobId={jobId} onEvent={(t) => addLog(t, "ev")} />}

      <section className="card">
        <h2>Trace</h2>
        <Log lines={logs} />
      </section>

      <section className="card">
        <h2>History</h2>
        {history.length === 0 ? <p className="muted">No files processed yet.</p> : (
          <table>
            <thead><tr><th>File</th><th>Load</th><th>Matching</th><th className="num">Rows</th><th>Created</th><th /></tr></thead>
            <tbody>
              {history.map((j) => (
                <tr key={j.id}>
                  <td>{j.filename}</td><td><Badge value={j.status} /></td><td><Badge value={j.matching_status} /></td>
                  <td className="num">{j.valid_rows}/{j.total_rows}</td>
                  <td>{new Date(j.created_at).toLocaleString()}</td>
                  <td><button className="link" onClick={() => setJobId(j.id)}>Open</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}
