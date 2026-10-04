//! One collector per agent. Parsers are pure functions (line -> records) so they can be unit tested on fixtures.
use crate::core::*;
use crate::pb;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub struct Cfg {
    pub home: PathBuf,
    pub host: String,
    pub mac: bool,
}

fn rel(p: &Path, base: &Path) -> String { p.strip_prefix(base).unwrap_or(p).to_string_lossy().to_string() }

/// Join text blocks of a Claude-style content value (string, or array of {type:text,text}).
fn blocks_text(c: &Value, kinds: &[&str]) -> String {
    match c {
        Value::String(s) => s.clone(),
        Value::Array(a) => a.iter()
            .filter(|b| b.get("type").or_else(|| b.get("kind")).and_then(|t| t.as_str()).map(|t| kinds.contains(&t)).unwrap_or(false))
            .filter_map(|b| b.get("text").and_then(|t| t.as_str()))
            .collect::<Vec<_>>().join("\n"),
        _ => String::new(),
    }
}

// ---------------- generic JSONL driver ----------------
fn drive_jsonl<F>(store: &mut Store, agent: &str, host: &str, base: &Path, file: &Path, mut parse: F) -> std::io::Result<()>
where F: FnMut(&str, &str, u64, &mut Value) -> Vec<Record> {
    let key = rel(file, base);
    let cur = store.cursor(agent, &key).cloned();
    let (pos0, mut aux) = match &cur {
        Some(Value::Object(o)) => (o.get("pos").and_then(|p| p.as_u64()).unwrap_or(0), cur.clone().unwrap()),
        Some(v) => (v.as_u64().unwrap_or(0), json!({})),
        None => (0, json!({})),
    };
    if let Ok(m) = std::fs::metadata(file) { if m.len() == pos0 && cur.is_some() { return Ok(()); } }
    let raw = store.root.join("raw").join(agent).join(host).join(&key);
    let mut recs: Vec<Record> = Vec::new();
    let path_s = file.to_string_lossy().to_string();
    let (newpos, reset) = tail_lines(file, &raw, pos0, |line, pos| { recs.extend(parse(line, &path_s, pos, &mut aux)); })?;
    let dup = cur.is_none() || reset;
    for r in recs { store.emit(r, dup)?; }
    let mut nv = if aux.is_object() { aux } else { json!({}) };
    nv["pos"] = json!(newpos);
    store.set_cursor(agent, &key, nv);
    Ok(())
}

// ---------------- claude ----------------
pub fn parse_claude(line: &str, path: &str, pos: u64, host: &str) -> Vec<Record> {
    let Ok(v) = serde_json::from_str::<Value>(line) else { return vec![] };
    let role = match v.get("type").and_then(|t| t.as_str()) { Some(r @ ("user" | "assistant")) => r, _ => return vec![] };
    let Some(ts) = v.get("timestamp").and_then(|t| t.as_str()).and_then(norm_ts) else { return vec![] };
    let text = blocks_text(v.pointer("/message/content").unwrap_or(&Value::Null), &["text"]);
    if text.trim().is_empty() { return vec![]; }
    let session = Path::new(path).file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    vec![Record { ts, agent: "claude".into(), host: host.into(), session, role: role.into(), text,
        model: v.pointer("/message/model").and_then(|m| m.as_str()).map(String::from),
        cwd: v.get("cwd").and_then(|m| m.as_str()).map(String::from), src_path: path.into(), src_pos: pos }]
}

pub fn collect_claude(store: &mut Store, cfg: &Cfg) -> std::io::Result<()> {
    let base = cfg.home.join(".claude/projects");
    let mut files = vec![]; walk(&base, "jsonl", &mut files);
    for f in files {
        let host = cfg.host.clone();
        drive_jsonl(store, "claude", &cfg.host, &base, &f, |l, p, pos, _| parse_claude(l, p, pos, &host))?;
    }
    Ok(())
}

// ---------------- codex ----------------
fn codex_session(path: &str) -> String {
    let stem = Path::new(path).file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let b = stem.as_bytes();
    if b.len() >= 36 {
        let t = &stem[stem.len() - 36..];
        if t.chars().enumerate().all(|(i, c)| if [8, 13, 18, 23].contains(&i) { c == '-' } else { c.is_ascii_hexdigit() }) { return t.to_string(); }
    }
    stem
}

/// `aux` carries the per-file model/cwd learnt from session_meta / turn_context lines.
pub fn parse_codex(line: &str, path: &str, pos: u64, host: &str, aux: &mut Value) -> Vec<Record> {
    let Ok(v) = serde_json::from_str::<Value>(line) else { return vec![] };
    match v.get("type").and_then(|t| t.as_str()) {
        Some("turn_context") | Some("session_meta") => {
            if let Some(m) = v.pointer("/payload/model").and_then(|x| x.as_str()) { aux["model"] = json!(m); }
            if let Some(c) = v.pointer("/payload/cwd").and_then(|x| x.as_str()) { aux["cwd"] = json!(c); }
            return vec![];
        }
        Some("response_item") => {}
        _ => return vec![],
    }
    let p = &v["payload"];
    if p.get("type").and_then(|t| t.as_str()) != Some("message") { return vec![]; }
    let role = match p.get("role").and_then(|r| r.as_str()) { Some("user") => "user", Some("assistant") => "assistant", Some("developer") => "system", _ => return vec![] };
    let Some(ts) = v.get("timestamp").and_then(|t| t.as_str()).and_then(norm_ts) else { return vec![] };
    let text = blocks_text(&p["content"], &["input_text", "output_text"]);
    if text.trim().is_empty() { return vec![]; }
    vec![Record { ts, agent: "codex".into(), host: host.into(), session: codex_session(path), role: role.into(), text,
        model: aux.get("model").and_then(|m| m.as_str()).map(String::from),
        cwd: aux.get("cwd").and_then(|m| m.as_str()).map(String::from), src_path: path.into(), src_pos: pos }]
}

pub fn collect_codex(store: &mut Store, cfg: &Cfg) -> std::io::Result<()> {
    let base = cfg.home.join(".codex/sessions");
    let mut files = vec![]; walk(&base, "jsonl", &mut files);
    for f in files {
        let host = cfg.host.clone();
        drive_jsonl(store, "codex", &cfg.host, &base, &f, |l, p, pos, aux| parse_codex(l, p, pos, &host, aux))?;
    }
    Ok(())
}

// ---------------- muse ----------------
pub fn parse_muse(line: &str, path: &str, pos: u64, host: &str) -> Vec<Record> {
    let Ok(v) = serde_json::from_str::<Value>(line) else { return vec![] };
    let Some(pt) = v.get("payload_type").and_then(|t| t.as_str()) else { return vec![] }; // framed retained lines are skipped; raw keeps them
    let Some(ts) = v.get("recorded_at").and_then(|t| t.as_i64()).and_then(|us| ts_from_ms(us / 1000)) else { return vec![] };
    let session = v.pointer("/stream/id").and_then(|s| s.as_str()).unwrap_or("").to_string();
    let mk = |role: &str, text: String| Record { ts: ts.clone(), agent: "muse".into(), host: host.into(), session: session.clone(),
        role: role.into(), text, model: None, cwd: None, src_path: path.into(), src_pos: pos };
    if pt == "runtime.user_intent.accepted" {
        let mut out = vec![];
        if let Some(ms) = v.pointer("/payload/model_messages").and_then(|m| m.as_array()) {
            for m in ms {
                let t = blocks_text(&m["content"], &["text"]);
                if !t.trim().is_empty() { out.push(mk("user", t)); }
            }
        }
        return out;
    }
    if pt == "runtime.session" && v.pointer("/payload/event/kind").and_then(|k| k.as_str()) == Some("assistant_message_committed") {
        if let Some(t) = v.pointer("/payload/event/text").and_then(|t| t.as_str()) {
            if !t.trim().is_empty() { return vec![mk("assistant", t.to_string())]; }
        }
    }
    vec![]
}

pub fn collect_muse(store: &mut Store, cfg: &Cfg) -> std::io::Result<()> {
    let base = cfg.home.join(".local/share/muse/sessions");
    let mut files = vec![]; walk(&base, "jsonl", &mut files);
    for f in files {
        if f.file_name().map(|n| n != "session.jsonl").unwrap_or(true) { continue; }
        let host = cfg.host.clone();
        drive_jsonl(store, "muse", &cfg.host, &base, &f, |l, p, pos, _| parse_muse(l, p, pos, &host))?;
    }
    Ok(())
}

// ---------------- opencode ----------------
pub fn collect_opencode(store: &mut Store, cfg: &Cfg) -> std::io::Result<()> {
    let dbp = cfg.home.join(".local/share/opencode/opencode.db");
    if !dbp.exists() { return Ok(()); }
    let conn = match Connection::open_with_flags(&dbp, OpenFlags::SQLITE_OPEN_READ_ONLY) { Ok(c) => c, Err(_) => return Ok(()) };
    let _ = conn.busy_timeout(std::time::Duration::from_secs(5));
    let cur = store.cursor("opencode", "parts").and_then(|v| v.as_str()).unwrap_or("0|").to_string();
    let (ct, cid) = cur.split_once('|').map(|(a, b)| (a.parse::<i64>().unwrap_or(0), b.to_string())).unwrap_or((0, String::new()));
    let fresh = store.cursor("opencode", "parts").is_none();
    let now_ms = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_millis() as i64).unwrap_or(0);
    let sql = "SELECT p.id, p.time_created, p.time_updated, p.data, m.data, s.id, s.directory, s.model \
               FROM part p JOIN message m ON m.id = p.message_id JOIN session s ON s.id = p.session_id \
               WHERE (p.time_created > ?1 OR (p.time_created = ?1 AND p.id > ?2)) AND json_extract(p.data,'$.type') = 'text' \
               ORDER BY p.time_created, p.id";
    let mut st = match conn.prepare(sql) { Ok(s) => s, Err(_) => return Ok(()) };
    let mut rows = match st.query(rusqlite::params![ct, cid]) { Ok(r) => r, Err(_) => return Ok(()) };
    let raw_path = store.root.join("raw/opencode").join(&cfg.host).join("opencode-rows.jsonl");
    std::fs::create_dir_all(raw_path.parent().unwrap())?;
    let mut raw = std::fs::OpenOptions::new().create(true).append(true).open(&raw_path)?;
    let mut last = (ct, cid.clone());
    while let Ok(Some(r)) = rows.next() {
        let id: String = r.get(0).unwrap_or_default();
        let tc: i64 = r.get(1).unwrap_or(0);
        let tu: i64 = r.get(2).unwrap_or(0);
        if now_ms - tu < 30_000 { break; } // still streaming; take it on a later pass
        let pdata: String = r.get(3).unwrap_or_default();
        let mdata: String = r.get(4).unwrap_or_default();
        let sid: String = r.get(5).unwrap_or_default();
        let dir: Option<String> = r.get(6).ok();
        let smodel: Option<String> = r.get(7).ok().flatten();
        let (pv, mv): (Value, Value) = (serde_json::from_str(&pdata).unwrap_or(Value::Null), serde_json::from_str(&mdata).unwrap_or(Value::Null));
        use std::io::Write;
        let _ = writeln!(raw, "{}", json!({"part_id":id,"session_id":sid,"time_created":tc,"time_updated":tu,"part":pv,"message":mv}));
        last = (tc, id.clone());
        let role = match mv.get("role").and_then(|x| x.as_str()) { Some(r @ ("user" | "assistant")) => r, _ => continue };
        let text = pv.get("text").and_then(|t| t.as_str()).unwrap_or("");
        if text.trim().is_empty() { continue; }
        let Some(ts) = ts_from_ms(tc) else { continue };
        let model = mv.get("modelID").and_then(|x| x.as_str()).or_else(|| mv.pointer("/model/modelID").and_then(|x| x.as_str())).map(String::from).or(smodel);
        let rec = Record { ts, agent: "opencode".into(), host: cfg.host.clone(), session: sid, role: role.into(), text: text.to_string(),
            model, cwd: dir, src_path: "opencode.db#part".into(), src_pos: fnv(&id) };
        store.emit(rec, fresh)?;
    }
    drop(rows);
    store.set_cursor("opencode", "parts", json!(format!("{}|{}", last.0, last.1)));
    Ok(())
}

fn fnv(s: &str) -> u64 { let mut h = 0xcbf29ce484222325u64; for b in s.bytes() { h ^= b as u64; h = h.wrapping_mul(0x100000001b3); } h >> 11 }

// ---------------- gemini (Antigravity) ----------------
/// Step blob layout inferred by inspection (UNVERIFIED against any official schema):
/// step_type 14 = user input (text at field 19.3.1, else 19.2), 15 = model response (text at field 20.1; 20.3 is thinking, skipped),
/// timestamp at 5.1 {seconds,nanos}.
pub fn parse_gemini_step(step_type: i64, blob: &[u8]) -> Option<(String, &'static str, String)> {
    use pb::{fields, Val};
    let sub = |b: &[u8], f: u32| -> Option<Vec<u8>> { fields(b)?.into_iter().find_map(|(n, v)| if n == f { if let Val::Bytes(x) = v { Some(x.to_vec()) } else { None } } else { None }) };
    let varint = |b: &[u8], f: u32| -> Option<u64> { fields(b)?.into_iter().find_map(|(n, v)| if n == f { if let Val::Varint(x) = v { Some(x) } else { None } } else { None }) };
    let meta = sub(blob, 5)?;
    let t = sub(&meta, 1)?;
    let ts = ts_from_secs_nanos(varint(&t, 1)? as i64, varint(&t, 2).unwrap_or(0) as u32)?;
    let utf = |b: Vec<u8>| String::from_utf8(b).ok();
    match step_type {
        14 => {
            let m = sub(blob, 19)?;
            let text = sub(&m, 3).and_then(|x| sub(&x, 1)).and_then(utf).or_else(|| sub(&m, 2).and_then(utf))?;
            Some((ts, "user", text))
        }
        15 => {
            let m = sub(blob, 20)?;
            let text = utf(sub(&m, 1)?)?;
            Some((ts, "assistant", text))
        }
        _ => None,
    }
}

fn gemini_db(store: &mut Store, host: &str, db: &Path, key: &str) -> std::io::Result<()> {
    let session = file_stem(db);
    let conn = match Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY) { Ok(c) => c, Err(_) => return Ok(()) };
    let _ = conn.busy_timeout(std::time::Duration::from_secs(5));
    let cur = store.cursor("gemini", key).and_then(|v| v.as_i64());
    let after = cur.unwrap_or(-1);
    let mut st = match conn.prepare("SELECT idx, step_type, step_payload FROM steps WHERE idx > ?1 AND step_type IN (14,15) ORDER BY idx") { Ok(s) => s, Err(_) => return Ok(()) };
    let mut rows = match st.query([after]) { Ok(r) => r, Err(_) => return Ok(()) };
    let mut max = after;
    let mut recs = vec![];
    while let Ok(Some(r)) = rows.next() {
        let idx: i64 = r.get(0).unwrap_or(0);
        let ty: i64 = r.get(1).unwrap_or(0);
        let blob: Vec<u8> = r.get(2).unwrap_or_default();
        max = max.max(idx);
        if let Some((ts, role, text)) = parse_gemini_step(ty, &blob) {
            if text.trim().is_empty() { continue; }
            recs.push(Record { ts, agent: "gemini".into(), host: host.into(), session: session.clone(), role: role.into(), text,
                model: None, cwd: None, src_path: key.into(), src_pos: idx as u64 });
        }
    }
    drop(rows); drop(st);
    for r in recs { store.emit(r, cur.is_none())?; }
    // Steps are appended with increasing idx, but a running step can be rewritten; keep cursor at max seen.
    store.set_cursor("gemini", key, json!(max));
    Ok(())
}

fn copy_if_changed(store: &mut Store, src: &Path, dst: &Path, sig_key: &str) -> std::io::Result<bool> {
    let m = std::fs::metadata(src)?;
    let sig = format!("{}:{}", m.len(), m.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0));
    if store.cursor("gemini", sig_key).and_then(|v| v.as_str()) == Some(sig.as_str()) && dst.exists() { return Ok(false); }
    if let Some(p) = dst.parent() { std::fs::create_dir_all(p)?; }
    let tmp = dst.with_extension("part");
    std::fs::copy(src, &tmp)?;
    std::fs::rename(&tmp, dst)?;
    store.set_cursor("gemini", sig_key, json!(sig));
    Ok(true)
}

pub fn collect_gemini(store: &mut Store, cfg: &Cfg) -> std::io::Result<()> {
    let base = cfg.home.join(".gemini");
    for sub in ["antigravity-cli", "antigravity-ide", "antigravity"] {
        let dir = base.join(sub).join("conversations");
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        let mut dbs: Vec<PathBuf> = rd.flatten().map(|e| e.path()).filter(|p| p.extension().map(|x| x == "db").unwrap_or(false)).collect();
        dbs.sort();
        for db in dbs {
            let relp = format!("{sub}/conversations/{}", db.file_name().unwrap().to_string_lossy());
            let dst = store.root.join("raw/gemini").join(&cfg.host).join(&relp);
            let mut changed_wal = false;
            let changed0 = match copy_if_changed(store, &db, &dst, &format!("sig:{relp}")) { Ok(c) => c, Err(e) => { eprintln!("gemini raw copy {relp}: {e}"); true } };
            let wal = db.with_extension("db-wal");
            if wal.metadata().map(|m| m.len() > 0).unwrap_or(false) {
                let wc = copy_if_changed(store, &wal, &dst.with_extension("db-wal"), &format!("sig:{relp}-wal")).unwrap_or(false); changed_wal = wc;
            }
            let k = format!("spark/{relp}");
            let changed = changed0 || changed_wal;
            if changed || store.cursor("gemini", &k).is_none() { gemini_db(store, &cfg.host, &db, &k)?; }
        }
    }
    let hist = base.join("antigravity-cli/history.jsonl");
    if hist.exists() { let _ = copy_if_changed(store, &hist, &store.root.join("raw/gemini").join(&cfg.host).join("antigravity-cli/history.jsonl"), "sig:history"); }
    Ok(())
}

/// Read-only pull of the MacBook's Antigravity conversation DBs (ssh + scp), at most every `every_s` seconds.
pub fn pull_macbook(store: &mut Store, every_s: u64) -> std::io::Result<()> {
    use std::process::Command;
    let now = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    if let Some(t) = store.cursor("gemini", "macpull:last").and_then(|v| v.as_u64()) { if now < t + every_s { return Ok(()); } }
    store.set_cursor("gemini", "macpull:last", json!(now));
    let ssh = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/agent-archive-mac-%C", "-o", "ControlPersist=60"];
    let listing = Command::new("ssh").args(ssh).arg("macbook")
        .arg("for d in antigravity-cli antigravity-ide antigravity; do find ~/.gemini/$d/conversations -maxdepth 1 -type f \\( -name \"*.db\" -o -name \"*.db-wal\" \\) -exec stat -f \"$d %N %z %m\" {} + 2>/dev/null; done; find ~/.gemini/antigravity-cli -maxdepth 1 -name history.jsonl -exec stat -f \"hist %N %z %m\" {} + 2>/dev/null; true")
        .output();
    let out = match listing { Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(), _ => { eprintln!("macbook unreachable, skipped"); return Ok(()); } };
    for l in out.lines() {
        let p: Vec<&str> = l.split(' ').collect();
        if p.len() != 4 { continue; }
        let (sub, path, size, mtime) = (p[0], p[1], p[2], p[3]);
        let name = path.rsplit('/').next().unwrap_or("");
        let relp = if sub == "hist" { "antigravity-cli/history.jsonl".to_string() } else { format!("{sub}/conversations/{name}") };
        let dst = store.root.join("raw/gemini/macbook").join(&relp);
        let sig = format!("{size}:{mtime}");
        let sk = format!("macsig:{relp}");
        if store.cursor("gemini", &sk).and_then(|v| v.as_str()) == Some(sig.as_str()) && dst.exists() { continue; }
        if let Some(d) = dst.parent() { std::fs::create_dir_all(d)?; }
        let tmp = dst.with_extension("part");
        let ok = Command::new("scp").args(ssh).arg("-q").arg(format!("macbook:{path}")).arg(&tmp).status().map(|s| s.success()).unwrap_or(false);
        if ok { std::fs::rename(&tmp, &dst)?; store.set_cursor("gemini", &sk, json!(sig)); } else { let _ = std::fs::remove_file(&tmp); eprintln!("scp failed: {relp}"); }
    }
    Ok(())
}

/// Parse the already-pulled MacBook copies (raw/gemini/macbook/**/*.db).
pub fn collect_gemini_mac(store: &mut Store) -> std::io::Result<()> {
    let base = store.root.join("raw/gemini/macbook");
    let mut dbs = vec![]; walk(&base, "db", &mut dbs); dbs.sort();
    for db in dbs {
        let relp = rel(&db, &base);
        let k = format!("macbook/{relp}");
        let m = std::fs::metadata(&db)?;
        let sig = format!("{}:{}", m.len(), m.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_secs()).unwrap_or(0));
        let sk = format!("msig:{relp}");
        if store.cursor("gemini", &sk).and_then(|v| v.as_str()) == Some(sig.as_str()) { continue; }
        gemini_db(store, "macbook", &db, &k)?;
        store.set_cursor("gemini", &sk, json!(sig));
    }
    Ok(())
}
