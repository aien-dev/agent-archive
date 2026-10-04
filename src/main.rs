mod collectors;
mod core;
mod pb;

use collectors::*;
use core::*;
use regex::Regex;
use std::path::PathBuf;
use std::time::{Duration, Instant};

fn home() -> PathBuf { PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/root".into())) }
fn default_root() -> PathBuf {
    std::env::var("AGENT_ARCHIVE_ROOT").map(PathBuf::from).unwrap_or_else(|_| home().join("archive/agent-chats"))
}

pub fn pass(store: &mut Store, cfg: &Cfg, mac: bool) {
    type C = fn(&mut Store, &Cfg) -> std::io::Result<()>;
    let steps: [(&str, C); 5] = [("claude", collect_claude), ("codex", collect_codex), ("opencode", collect_opencode),
        ("gemini", collect_gemini), ("muse", collect_muse)];
    for (name, f) in steps {
        if let Err(e) = f(store, cfg) { eprintln!("collector {name} failed: {e}"); }
    }
    if mac {
        if let Err(e) = pull_macbook(store, 300) { eprintln!("macbook pull failed: {e}"); }
        if let Err(e) = collect_gemini_mac(store) { eprintln!("gemini macbook parse failed: {e}"); }
    }
    if let Err(e) = store.finish_pass() { eprintln!("finish_pass failed: {e}"); }
}

fn summary(store: &mut Store) -> String {
    let mut v: Vec<String> = store.emitted.iter().map(|(a, n)| format!("{a}={n}")).collect();
    v.sort();
    store.emitted.clear();
    v.join(" ")
}

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned())
}

fn status(root: &std::path::Path) {
    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    let st = Store::open(root).expect("open archive");
    println!("{:<9} {:<26} {:>12} {:>8} {}", "agent", "last record", "today", "sources", "preview");
    for a in AGENTS {
        let tail = load_tail(root, a, 1);
        let (last, prev) = tail.last().map(|(t, r)| (t.clone(), r.text.chars().take(80).collect::<String>().replace('\n', " "))).unwrap_or(("-".into(), String::new()));
        let todayn = std::fs::read_to_string(root.join("records").join(a).join(format!("{today}.jsonl"))).map(|s| s.lines().count()).unwrap_or(0);
        let total: usize = std::fs::read_dir(root.join("records").join(a)).map(|d| d.flatten().filter_map(|e| std::fs::read_to_string(e.path()).ok()).map(|s| s.lines().count()).sum()).unwrap_or(0);
        let sources = st.state_keys(a);
        println!("{:<9} {:<26} {:>6}/{:<6} {:>8} {}", a, last, todayn, total, sources, prev);
    }
    println!("(today/total records)");
}

fn query(root: &std::path::Path, args: &[String]) {
    let agent = arg(args, "--agent");
    let since = arg(args, "--since").unwrap_or_default();
    let re = arg(args, "--grep").map(|g| Regex::new(&g).unwrap_or_else(|e| { eprintln!("bad --grep: {e}"); std::process::exit(2) }));
    let limit: usize = arg(args, "--limit").and_then(|l| l.parse().ok()).unwrap_or(50);
    let json = args.iter().any(|a| a == "--json");
    let agents: Vec<String> = match agent { Some(a) => vec![a], None => AGENTS.iter().map(|s| s.to_string()).collect() };
    let mut hits: Vec<(String, Record)> = vec![];
    for a in agents {
        let dir = root.join("records").join(&a);
        let mut files: Vec<PathBuf> = std::fs::read_dir(&dir).map(|d| d.flatten().map(|e| e.path()).collect()).unwrap_or_default();
        files.sort();
        for f in files {
            let day = f.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
            if !since.is_empty() && day.as_str() < &since[..since.len().min(10)] { continue; }
            let Ok(t) = std::fs::read_to_string(&f) else { continue };
            for l in t.lines() {
                let Some(r) = serde_json::from_str(l).ok().and_then(|v| Record::from_json(&v)) else { continue };
                if !since.is_empty() && r.ts.as_str() < since.as_str() { continue; }
                if let Some(re) = &re { if !re.is_match(&r.text) { continue; } }
                hits.push((r.ts.clone(), r));
            }
        }
    }
    hits.sort_by(|a, b| a.0.cmp(&b.0));
    let skip = hits.len().saturating_sub(limit);
    for (_, r) in hits.into_iter().skip(skip) {
        if json { println!("{}", serde_json::to_string(&r.to_json()).unwrap()); } else { println!("{} {}", r.agent, r.txt_line()); }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let root = default_root();
    let cmd = args.first().map(|s| s.as_str()).unwrap_or("help");
    let cfg = Cfg { home: home(), host: "spark".into(), mac: true };
    match cmd {
        "once" => {
            let t = Instant::now();
            let mut st = Store::open(&root).expect("open archive");
            pass(&mut st, &cfg, cfg.mac && !args.iter().any(|a| a == "--no-mac"));
            println!("once done in {:.1}s: {}", t.elapsed().as_secs_f64(), summary(&mut st));
        }
        "collect" => {
            let mut st = Store::open(&root).expect("open archive");
            loop {
                pass(&mut st, &cfg, cfg.mac);
                let s = summary(&mut st);
                if !s.is_empty() { eprintln!("{} +{s}", chrono::Utc::now().format("%H:%M:%S")); }
                std::thread::sleep(Duration::from_secs(10));
            }
        }
        "query" => query(&root, &args[1..]),
        "status" => status(&root),
        "debug-pb" => { let b = std::fs::read(&args[1]).expect("read file"); let mut s = String::new(); pb::dump(&b, 0, "", &mut s); print!("{s}"); }
        _ => {
            println!("agent-archive collect | once [--no-mac] | status | debug-pb FILE | query [--agent A] [--since TS] [--grep RE] [--limit N] [--json]\nroot: {} (override with AGENT_ARCHIVE_ROOT)", root.display());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("aa-test-{}-{}", name, std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }
    fn fixture(n: &str) -> String { fs::read_to_string(format!("{}/tests/fixtures/{n}", env!("CARGO_MANIFEST_DIR"))).unwrap() }
    fn count(root: &std::path::Path, agent: &str) -> usize {
        fs::read_dir(root.join("records").join(agent)).map(|d| d.flatten().map(|e| fs::read_to_string(e.path()).unwrap().lines().count()).sum()).unwrap_or(0)
    }

    #[test]
    fn claude_parser_keeps_text_only() {
        let lines: Vec<String> = fixture("claude.jsonl").lines().map(String::from).collect();
        let recs: Vec<_> = lines.iter().enumerate().flat_map(|(i, l)| parse_claude(l, "/x/eb18088a.jsonl", i as u64, "spark")).collect();
        assert_eq!(recs.len(), 2);
        assert_eq!(recs[0].role, "user");
        assert_eq!(recs[1].text, "Looking now.");
        assert_eq!(recs[1].model.as_deref(), Some("claude-opus-5-5"));
        assert_eq!(recs[0].ts, "2026-09-26T09:35:16.500Z");
        assert_eq!(recs[0].session, "eb18088a");
    }

    #[test]
    fn codex_parser_roles_and_session() {
        let mut aux = serde_json::json!({});
        let p = "/s/rollout-2026-09-05T12-11-22-01a0728d-ae40-7261-b95b-8cdb98c5bc5f.jsonl";
        let recs: Vec<_> = fixture("codex.jsonl").lines().flat_map(|l| parse_codex(l, p, 0, "spark", &mut aux)).collect();
        let roles: Vec<&str> = recs.iter().map(|r| r.role.as_str()).collect();
        assert_eq!(roles, ["system", "user", "assistant"]);
        assert_eq!(recs[0].session, "01a0728d-ae40-7261-b95b-8cdb98c5bc5f");
        assert_eq!(recs[2].model.as_deref(), Some("gpt-6-astra"));
    }

    #[test]
    fn muse_parser_user_and_assistant() {
        let recs: Vec<_> = fixture("muse.jsonl").lines().flat_map(|l| parse_muse(l, "/m/session.jsonl", 0, "spark")).collect();
        assert_eq!(recs.len(), 2);
        assert_eq!((recs[0].role.as_str(), recs[0].text.as_str()), ("user", "ls"));
        assert_eq!((recs[1].role.as_str(), recs[1].text.as_str()), ("assistant", "225"));
        assert!(recs[0].ts.starts_with("2026-09-20T"));
    }

    // Blob built to the layout observed in a real Antigravity step (field numbers: 5.1 time, 19.3.1 user text, 20.1 reply text).
    fn tag(f: u32, w: u32) -> Vec<u8> { let mut o = vec![]; let mut v = (f << 3) | w; loop { let c = (v & 0x7f) as u8; v >>= 7; if v == 0 { o.push(c); break; } o.push(c | 0x80); } o }
    fn enc_len(f: u32, b: &[u8]) -> Vec<u8> {
        let mut o = tag(f, 2);
        let mut n = b.len();
        loop { let c = (n & 0x7f) as u8; n >>= 7; if n == 0 { o.push(c); break; } o.push(c | 0x80); }
        o.extend_from_slice(b); o
    }
    fn enc_var(f: u32, mut v: u64) -> Vec<u8> {
        let mut o = tag(f, 0);
        loop { let c = (v & 0x7f) as u8; v >>= 7; if v == 0 { o.push(c); break; } o.push(c | 0x80); }
        o
    }
    fn step(top: u32, inner: Vec<u8>) -> Vec<u8> {
        let time = [enc_var(1, 1790836387), enc_var(2, 593377033)].concat();
        let meta = enc_len(1, &time);
        [enc_var(1, 14), enc_len(5, &meta), enc_len(top, &inner)].concat()
    }

    #[test]
    fn gemini_step_user_and_assistant() {
        let user = step(19, [enc_len(2, b"/plan raw"), enc_len(3, &enc_len(1, b" cleaned text"))].concat());
        let (ts, role, text) = parse_gemini_step(14, &user).unwrap();
        assert_eq!((role, text.as_str()), ("user", " cleaned text"));
        assert_eq!(ts, "2026-10-01T06:33:07.593Z");
        let asst = step(20, [enc_len(1, b"the answer"), enc_len(3, b"thinking")].concat());
        let (_, role, text) = parse_gemini_step(15, &asst).unwrap();
        assert_eq!((role, text.as_str()), ("assistant", "the answer"));
        assert!(parse_gemini_step(132, &asst).is_none());
    }

    #[test]
    fn full_pass_incremental_and_dedupe_after_state_loss() {
        let home = tmp("home");
        let root = tmp("root");
        let proj = home.join(".claude/projects/-p");
        fs::create_dir_all(&proj).unwrap();
        let f = proj.join("sess1.jsonl");
        let fx = fixture("claude.jsonl");
        fs::write(&f, &fx).unwrap();
        let cfg = Cfg { home: home.clone(), host: "spark".into(), mac: false };
        let mut st = Store::open(&root).unwrap();
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "claude"), 2);
        // raw mirror is byte-identical
        assert_eq!(fs::read_to_string(root.join("raw/claude/spark/-p/sess1.jsonl")).unwrap(), fx);
        // latest views exist
        let txt = fs::read_to_string(root.join("latest/claude.txt")).unwrap();
        assert!(txt.contains("[2026-09-26T09:35:16.500Z sess1 user] Check the live"));
        // second pass: nothing new
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "claude"), 2);
        // append one complete line and one partial line
        let mut more = fx.clone();
        let extra = "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":\"second\"},\"timestamp\":\"2026-09-26T10:00:00.000Z\"}\n";
        more.push_str(extra);
        more.push_str("{\"type\":\"user\",\"mess");
        let more_complete_len = fx.len() + extra.len();
        fs::write(&f, &more).unwrap();
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "claude"), 3);
        // lose all cursors: re-read must not duplicate records
        fs::remove_dir_all(root.join("state")).unwrap();
        let mut st2 = Store::open(&root).unwrap();
        pass(&mut st2, &cfg, false);
        assert_eq!(count(&root, "claude"), 3);
        // raw never doubled either
        assert!(fs::read_to_string(root.join("raw/claude/spark/-p/sess1.jsonl")).unwrap().starts_with(&fx));
        assert_eq!(fs::metadata(root.join("raw/claude/spark/-p/sess1.jsonl")).unwrap().len() as usize, more_complete_len);
    }

    #[test]
    fn opencode_collects_text_parts() {
        let home = tmp("ochome");
        let root = tmp("ocroot");
        let d = home.join(".local/share/opencode");
        fs::create_dir_all(&d).unwrap();
        let c = rusqlite::Connection::open(d.join("opencode.db")).unwrap();
        c.execute_batch("create table session(id text primary key, directory text, model text);
            create table message(id text primary key, session_id text, time_created integer, data text);
            create table part(id text primary key, message_id text, session_id text, time_created integer, time_updated integer, data text);
            insert into session values('ses_1','/home/x',null);
            insert into message values('msg_1','ses_1',1790977000000,'{\"role\":\"user\",\"model\":{\"modelID\":\"m1\"}}');
            insert into message values('msg_2','ses_1',1790977001000,'{\"role\":\"assistant\",\"modelID\":\"m1\"}');
            insert into part values('prt_1','msg_1','ses_1',1790977000000,1790977000100,'{\"type\":\"text\",\"text\":\"hi there\"}');
            insert into part values('prt_2','msg_2','ses_1',1790977001000,1790977001100,'{\"type\":\"reasoning\",\"text\":\"think\"}');
            insert into part values('prt_3','msg_2','ses_1',1790977002000,1790977002100,'{\"type\":\"text\",\"text\":\"hello\"}');").unwrap();
        drop(c);
        let cfg = Cfg { home, host: "spark".into(), mac: false };
        let mut st = Store::open(&root).unwrap();
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "opencode"), 2);
        let recs = load_tail(&root, "opencode", 10);
        assert_eq!(recs[0].1.text, "hi there");
        assert_eq!(recs[0].1.model.as_deref(), Some("m1"));
        assert_eq!(recs[1].1.role, "assistant");
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "opencode"), 2);
    }

    #[test]
    fn gemini_db_pass() {
        let home = tmp("gmhome");
        let root = tmp("gmroot");
        let d = home.join(".gemini/antigravity-cli/conversations");
        fs::create_dir_all(&d).unwrap();
        let c = rusqlite::Connection::open(d.join("conv-1.db")).unwrap();
        c.execute_batch("create table steps(idx integer primary key, step_type integer, step_payload blob);").unwrap();
        let u = step(19, enc_len(3, &enc_len(1, b"question")));
        let a = step(20, enc_len(1, b"answer"));
        c.execute("insert into steps values(0,14,?1)", [u]).unwrap();
        c.execute("insert into steps values(1,15,?1)", [a]).unwrap();
        drop(c);
        let cfg = Cfg { home, host: "spark".into(), mac: false };
        let mut st = Store::open(&root).unwrap();
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "gemini"), 2);
        assert!(root.join("raw/gemini/spark/antigravity-cli/conversations/conv-1.db").exists());
        pass(&mut st, &cfg, false);
        assert_eq!(count(&root, "gemini"), 2);
    }

    #[test]
    fn latest_window_is_capped_and_sorted() {
        let root = tmp("latest");
        let mut st = Store::open(&root).unwrap();
        for i in 0..(LATEST_LINES + 50) {
            let r = Record { ts: format!("2026-10-04T00:{:02}:{:02}.{:03}Z", (i / 60000) % 60, (i / 1000) % 60, i % 1000), agent: "claude".into(), host: "spark".into(),
                session: "s".into(), role: "user".into(), text: format!("m{i}"), model: None, cwd: None, src_path: "p".into(), src_pos: i as u64 };
            st.emit(r, false).unwrap();
        }
        st.finish_pass().unwrap();
        let t = fs::read_to_string(root.join("latest/claude.txt")).unwrap();
        assert_eq!(t.lines().count(), LATEST_LINES);
        assert!(t.lines().last().unwrap().ends_with(&format!("m{}", LATEST_LINES + 49)));
        assert!(!t.contains("] m0\n"));
    }
}
