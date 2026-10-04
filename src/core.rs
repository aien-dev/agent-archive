//! Record schema, archive store (records, raw mirror, latest views, state cursors).
use chrono::{DateTime, SecondsFormat, TimeZone, Utc};
use serde_json::{json, Value};
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

pub const LATEST_LINES: usize = 20000;
pub const TXT_MAX_CHARS: usize = 8000;
pub const AGENTS: [&str; 5] = ["claude", "codex", "opencode", "gemini", "muse"];

#[derive(Clone, Debug, PartialEq)]
pub struct Record {
    pub ts: String,
    pub agent: String,
    pub host: String,
    pub session: String,
    pub role: String,
    pub text: String,
    pub model: Option<String>,
    pub cwd: Option<String>,
    pub src_path: String,
    pub src_pos: u64,
}

impl Record {
    pub fn to_json(&self) -> Value {
        json!({"ts":self.ts,"agent":self.agent,"host":self.host,"session":self.session,
               "role":self.role,"text":self.text,"model":self.model,"cwd":self.cwd,
               "src":{"path":self.src_path,"pos":self.src_pos}})
    }
    pub fn from_json(v: &Value) -> Option<Record> {
        let s = |k: &str| v.get(k).and_then(|x| x.as_str()).map(String::from);
        Some(Record {
            ts: s("ts")?, agent: s("agent")?, host: s("host")?, session: s("session")?,
            role: s("role")?, text: s("text")?, model: s("model"), cwd: s("cwd"),
            src_path: v.pointer("/src/path")?.as_str()?.to_string(),
            src_pos: v.pointer("/src/pos")?.as_u64()?,
        })
    }
    pub fn date(&self) -> &str { &self.ts[..10] }
    pub fn key(&self) -> u64 {
        // FNV-1a over the identity (agent, host, session, role, src path, src pos).
        let mut h: u64 = 0xcbf29ce484222325;
        for part in [self.agent.as_str(), &self.host, &self.session, &self.role, &self.src_path] {
            for b in part.bytes().chain(std::iter::once(0u8)) { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
        }
        for b in self.src_pos.to_le_bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); }
        h
    }
    pub fn txt_line(&self) -> String {
        let mut t: String = self.text.chars().take(TXT_MAX_CHARS).collect();
        if self.text.chars().count() > TXT_MAX_CHARS { t.push_str(" ...[truncated]"); }
        let t = t.replace('\r', "").replace('\n', "\\n");
        let s8: String = self.session.chars().take(8).collect();
        format!("[{} {} {}] {}", self.ts, s8, self.role, t)
    }
}

/// Normalise an RFC3339 string to millisecond UTC "…Z". None if unparseable.
pub fn norm_ts(s: &str) -> Option<String> {
    DateTime::parse_from_rfc3339(s).ok().map(|d| d.with_timezone(&Utc).to_rfc3339_opts(SecondsFormat::Millis, true))
}
pub fn ts_from_ms(ms: i64) -> Option<String> {
    Utc.timestamp_millis_opt(ms).single().map(|d| d.to_rfc3339_opts(SecondsFormat::Millis, true))
}
pub fn ts_from_secs_nanos(s: i64, n: u32) -> Option<String> {
    Utc.timestamp_opt(s, n).single().map(|d| d.to_rfc3339_opts(SecondsFormat::Millis, true))
}

pub struct Store {
    pub root: PathBuf,
    state: HashMap<String, Value>,
    state_dirty: HashSet<String>,
    seen: HashMap<PathBuf, HashSet<u64>>,
    pub fresh: VecDeque<Record>, // unused window staging
    latest: HashMap<String, Vec<(String, Record)>>,
    latest_dirty: HashSet<String>,
    pub emitted: HashMap<String, u64>,
    appenders: HashMap<PathBuf, File>,
}

impl Store {
    pub fn open(root: &Path) -> std::io::Result<Store> {
        for d in ["records", "raw", "latest", "state"] { fs::create_dir_all(root.join(d))?; }
        let mut s = Store { root: root.to_path_buf(), state: HashMap::new(), state_dirty: HashSet::new(),
            seen: HashMap::new(), fresh: VecDeque::new(), latest: HashMap::new(), latest_dirty: HashSet::new(),
            emitted: HashMap::new(), appenders: HashMap::new() };
        for a in AGENTS {
            if let Ok(t) = fs::read_to_string(root.join("state").join(format!("{a}.json"))) {
                if let Ok(v) = serde_json::from_str::<Value>(&t) { s.state.insert(a.to_string(), v); }
            }
        }
        Ok(s)
    }

    // ---- cursors ----
    pub fn cursor(&self, agent: &str, key: &str) -> Option<&Value> { self.state.get(agent)?.get(key) }
    pub fn set_cursor(&mut self, agent: &str, key: &str, v: Value) {
        let e = self.state.entry(agent.to_string()).or_insert_with(|| json!({}));
        e[key] = v;
        self.state_dirty.insert(agent.to_string());
    }
    pub fn save_state(&mut self) -> std::io::Result<()> {
        let dirty: Vec<String> = self.state_dirty.drain().collect();
        for a in dirty {
            atomic_write(&self.root.join("state").join(format!("{a}.json")), serde_json::to_string(&self.state[&a]).unwrap().as_bytes())?;
        }
        Ok(())
    }

    // ---- records ----
    /// Append a record. `check_dup`: consult the existing date file first (used when a source is re-read after state loss).
    pub fn emit(&mut self, r: Record, check_dup: bool) -> std::io::Result<bool> {
        let dir = self.root.join("records").join(&r.agent);
        fs::create_dir_all(&dir)?;
        let path = dir.join(format!("{}.jsonl", r.date()));
        if check_dup && !self.seen.contains_key(&path) {
            let mut set = HashSet::new();
            if let Ok(f) = File::open(&path) {
                for l in BufReader::new(f).lines().map_while(Result::ok) {
                    if let Some(rec) = serde_json::from_str::<Value>(&l).ok().and_then(|v| Record::from_json(&v)) { set.insert(rec.key()); }
                }
            }
            self.seen.insert(path.clone(), set);
        }
        if let Some(set) = self.seen.get_mut(&path) {
            if !set.insert(r.key()) { return Ok(false); }
        }
        if !self.appenders.contains_key(&path) {
            if self.appenders.len() > 64 { self.appenders.clear(); }
            self.appenders.insert(path.clone(), OpenOptions::new().create(true).append(true).open(&path)?);
        }
        let mut line = serde_json::to_string(&r.to_json()).unwrap();
        line.push('\n');
        self.appenders.get_mut(&path).unwrap().write_all(line.as_bytes())?;
        *self.emitted.entry(r.agent.clone()).or_insert(0) += 1;
        let agent = r.agent.clone();
        let v = self.latest_for(&agent);
        v.push((r.ts.clone(), r));
        if v.len() > LATEST_LINES * 2 { v.sort_by(|a, b| a.0.cmp(&b.0)); let n = v.len() - LATEST_LINES; v.drain(..n); }
        self.latest_dirty.insert(agent);
        Ok(true)
    }

    fn latest_for(&mut self, agent: &str) -> &mut Vec<(String, Record)> {
        if !self.latest.contains_key(agent) {
            let v = load_tail(&self.root, agent, LATEST_LINES);
            self.latest.insert(agent.to_string(), v);
        }
        self.latest.get_mut(agent).unwrap()
    }

    /// End of pass: flush cursors and rewrite latest views for agents that got new records.
    pub fn finish_pass(&mut self) -> std::io::Result<()> {
        self.appenders.clear();
        self.seen.clear();
        let dirty: Vec<String> = self.latest_dirty.drain().collect();
        for a in dirty {
            let v = self.latest.get_mut(&a).unwrap();
            v.sort_by(|x, y| x.0.cmp(&y.0));
            if v.len() > LATEST_LINES { let n = v.len() - LATEST_LINES; v.drain(..n); }
            let mut txt = String::new();
            let mut js = String::new();
            for (_, r) in v.iter() {
                txt.push_str(&r.txt_line()); txt.push('\n');
                js.push_str(&serde_json::to_string(&r.to_json()).unwrap()); js.push('\n');
            }
            atomic_write(&self.root.join("latest").join(format!("{a}.txt")), txt.as_bytes())?;
            atomic_write(&self.root.join("latest").join(format!("{a}.jsonl")), js.as_bytes())?;
        }
        self.save_state()
    }
}

pub fn atomic_write(path: &Path, data: &[u8]) -> std::io::Result<()> {
    if let Some(p) = path.parent() { fs::create_dir_all(p)?; }
    let tmp = path.with_extension(format!("tmp{}", std::process::id()));
    { let mut f = File::create(&tmp)?; f.write_all(data)?; f.sync_all().ok(); }
    fs::rename(&tmp, path)
}

/// Last `n` records for an agent, read from the newest date files backwards.
pub fn load_tail(root: &Path, agent: &str, n: usize) -> Vec<(String, Record)> {
    let dir = root.join("records").join(agent);
    let mut files: Vec<PathBuf> = fs::read_dir(&dir).map(|d| d.filter_map(|e| e.ok().map(|e| e.path())).collect()).unwrap_or_default();
    files.sort();
    let mut out: Vec<(String, Record)> = Vec::new();
    for f in files.iter().rev() {
        let mut part = Vec::new();
        if let Ok(fh) = File::open(f) {
            for l in BufReader::new(fh).lines().map_while(Result::ok) {
                if let Some(r) = serde_json::from_str::<Value>(&l).ok().and_then(|v| Record::from_json(&v)) { part.push((r.ts.clone(), r)); }
            }
        }
        out.extend(part);
        if out.len() >= n { break; }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    if out.len() > n { let k = out.len() - n; out.drain(..k); }
    out
}

/// Tail an append-only JSONL source from `cursor`: calls `f(line, byte_pos)` for every complete line, mirrors the
/// bytes verbatim into `raw_path`, and returns the new cursor. Returns (new_cursor, reset) where reset means the source
/// shrank and was re-read from 0.
pub fn tail_lines<F: FnMut(&str, u64)>(src: &Path, raw_path: &Path, cursor: u64, mut f: F) -> std::io::Result<(u64, bool)> {
    let len = fs::metadata(src)?.len();
    let (mut pos, reset) = if len < cursor { (0, true) } else { (cursor, false) };
    if len == pos { return Ok((pos, reset)); }
    let mut fh = File::open(src)?;
    fh.seek(SeekFrom::Start(pos))?;
    let mut rd = BufReader::with_capacity(1 << 20, fh.take(len - pos));
    if let Some(p) = raw_path.parent() { fs::create_dir_all(p)?; }
    let mut raw = OpenOptions::new().create(true).append(true).open(raw_path)?;
    let mut raw_len = raw.metadata()?.len();
    let mut buf = Vec::new();
    loop {
        buf.clear();
        let n = rd.read_until(b'\n', &mut buf)?;
        if n == 0 { break; }
        if buf.last() != Some(&b'\n') { break; } // partial last line: leave for next pass
        let start = pos;
        pos += n as u64;
        // verbatim mirror: write only the bytes the mirror does not have yet
        if pos > raw_len {
            let skip = raw_len.saturating_sub(start) as usize;
            raw.write_all(&buf[skip.min(buf.len())..])?;
            raw_len = pos;
        }
        let line = String::from_utf8_lossy(&buf[..n - 1]);
        let line = line.trim_end_matches('\r');
        if !line.is_empty() { f(line, start); }
    }
    Ok((pos, reset))
}

pub fn walk(dir: &Path, ext: &str, out: &mut Vec<PathBuf>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let p = e.path();
        if p.is_dir() { walk(&p, ext, out); }
        else if p.extension().map(|x| x == ext).unwrap_or(false) { out.push(p); }
    }
}

pub fn file_stem(p: &Path) -> String { p.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default() }

impl Store {
    /// Number of real sources tracked for an agent (bookkeeping keys with a "xxx:" prefix are not counted).
    pub fn state_keys(&self, agent: &str) -> usize {
        self.state.get(agent).and_then(|v| v.as_object()).map(|o| o.keys().filter(|k| !k.contains(':') || k.as_str() == "parts").count()).unwrap_or(0)
    }
}
