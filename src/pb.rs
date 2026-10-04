//! Minimal protobuf wire-format walker (no schema). Used for Gemini/Antigravity step blobs.

pub enum Val<'a> {
    Varint(u64),
    Bytes(&'a [u8]),
    Fixed(#[allow(dead_code)] u64),
}

pub fn varint(b: &[u8], i: &mut usize) -> Option<u64> {
    let mut v = 0u64;
    let mut shift = 0;
    while *i < b.len() {
        let c = b[*i];
        *i += 1;
        v |= ((c & 0x7f) as u64) << shift;
        if c & 0x80 == 0 {
            return Some(v);
        }
        shift += 7;
        if shift > 63 {
            return None;
        }
    }
    None
}

/// Parse one message level. Returns None if the bytes are not a clean message.
pub fn fields(b: &[u8]) -> Option<Vec<(u32, Val<'_>)>> {
    let mut i = 0;
    let mut out = Vec::new();
    while i < b.len() {
        let tag = varint(b, &mut i)?;
        let f = (tag >> 3) as u32;
        if f == 0 {
            return None;
        }
        match tag & 7 {
            0 => out.push((f, Val::Varint(varint(b, &mut i)?))),
            1 => {
                let e = i.checked_add(8)?;
                if e > b.len() { return None; }
                out.push((f, Val::Fixed(u64::from_le_bytes(b[i..e].try_into().ok()?))));
                i = e;
            }
            2 => {
                let n = varint(b, &mut i)? as usize;
                let e = i.checked_add(n)?;
                if e > b.len() { return None; }
                out.push((f, Val::Bytes(&b[i..e])));
                i = e;
            }
            5 => {
                let e = i.checked_add(4)?;
                if e > b.len() { return None; }
                out.push((f, Val::Fixed(u32::from_le_bytes(b[i..e].try_into().ok()?) as u64)));
                i = e;
            }
            _ => return None,
        }
    }
    Some(out)
}

pub fn dump(b: &[u8], depth: usize, path: &str, out: &mut String) {
    if let Some(fs) = fields(b) {
        for (f, v) in fs {
            let p = format!("{path}.{f}");
            match v {
                Val::Varint(n) => out.push_str(&format!("{p} = {n}\n")),
                Val::Fixed(n) => out.push_str(&format!("{p} = fixed {n}\n")),
                Val::Bytes(x) => {
                    if depth < 8 && !x.is_empty() && fields(x).is_some() && std::str::from_utf8(x).map(|s| s.chars().any(|c| c.is_control() && c != '\n')).unwrap_or(true) {
                        out.push_str(&format!("{p} {{\n"));
                        dump(x, depth + 1, &p, out);
                        out.push_str("}\n");
                    } else if let Ok(s) = std::str::from_utf8(x) {
                        let t: String = s.chars().take(100).collect();
                        out.push_str(&format!("{p} = {:?}\n", t));
                    } else {
                        out.push_str(&format!("{p} = <{} bytes>\n", x.len()));
                    }
                }
            }
        }
    }
}
