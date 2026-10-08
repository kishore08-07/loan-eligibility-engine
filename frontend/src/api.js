const API = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/$/, "");

const listeners = new Set();
/** Subscribe to every API call (method, path, status, ms) so the UI can show what is happening. */
export const onApiCall = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const emit = (e) => listeners.forEach((fn) => fn(e));

export const API_URL = API;

async function request(path, options) {
  const method = (options && options.method) || "GET";
  const t0 = performance.now();
  let res;
  try {
    res = await fetch(`${API}${path}`, options);
  } catch (e) {
    emit({ method, path, status: "ERR", ms: Math.round(performance.now() - t0) });
    throw e;
  }
  emit({ method, path, status: res.status, ms: Math.round(performance.now() - t0) });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed (${res.status})`);
  }
  return res.json();
}

export const health = () => request("/health");
export const listUploads = () => request("/uploads");
export const getUpload = (id) => request(`/uploads/${id}`);
export const getMatches = (id, offset = 0, limit = 25) =>
  request(`/uploads/${id}/matches?offset=${offset}&limit=${limit}`);

/** 1) ask the API for a presigned S3 POST, 2) upload the file straight to S3 (with progress). */
export async function uploadCsv(file, onProgress) {
  const created = await request("/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name }),
  });
  const { url, fields } = created.upload;
  const form = new FormData();
  Object.entries(fields).forEach(([k, v]) => form.append(k, v));
  form.append("file", file); // must be the last field
  await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => (xhr.status >= 200 && xhr.status < 300 ? resolve() : reject(new Error(`S3 upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("S3 upload failed (network/CORS)"));
    xhr.send(form);
  });
  return created.job_id;
}
