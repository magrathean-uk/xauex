//! tick_parser — high-performance Dukascopy CSV → H1 OHLC bar parser
//!
//! Exposed to Python as:
//!   tick_parser.parse_to_bars(path: str, start_iso: str, end_iso: str) -> list[dict]
//!
//! Each returned dict contains:
//!   open_time: str (ISO-8601 UTC), open/high/low/close: float,
//!   max_spread: float, tick_count: int

use pyo3::prelude::*;
use chrono::{DateTime, Utc, NaiveDateTime, TimeZone, Datelike, Timelike};
use std::fs::File;
use std::io::{BufRead, BufReader};

// ─────────────────────────────────────────────────────────
// Internal types
// ─────────────────────────────────────────────────────────

struct Bar {
    open_time: DateTime<Utc>,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    max_spread: f64,
    tick_count: u64,
}

// ─────────────────────────────────────────────────────────
// Timestamp parsing — ISO 8601 or millisecond epoch
// ─────────────────────────────────────────────────────────

fn parse_ts(s: &str) -> Option<DateTime<Utc>> {
    let s = s.trim();
    // Millisecond epoch (duka native format)
    if s.chars().all(|c| c.is_ascii_digit()) {
        let ms: i64 = s.parse().ok()?;
        return DateTime::from_timestamp(ms / 1000, ((ms % 1000) * 1_000_000) as u32);
    }
    // ISO 8601 with Z or +00:00
    if let Ok(dt) = s.parse::<DateTime<Utc>>() {
        return Some(dt);
    }
    // ISO 8601 without timezone (assume UTC) — e.g. "2026-03-10T09:00:00"
    if let Ok(ndt) = s.parse::<NaiveDateTime>() {
        return Some(Utc.from_utc_datetime(&ndt));
    }
    // "2026-03-10 09:00:00"
    if let Ok(ndt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S") {
        return Some(Utc.from_utc_datetime(&ndt));
    }
    // "2026-03-10 09:00:00.000"
    if let Ok(ndt) = NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S%.f") {
        return Some(Utc.from_utc_datetime(&ndt));
    }
    None
}

// ─────────────────────────────────────────────────────────
// Column detection — case-insensitive
// ─────────────────────────────────────────────────────────

fn detect_columns(headers: &[&str]) -> Option<(usize, usize, usize)> {
    let lower: Vec<String> = headers.iter().map(|h| h.trim().to_lowercase()).collect();
    let ts_idx = lower.iter().position(|h| h.contains("time") || h.contains("date"))?;
    let bid_idx = lower.iter().position(|h| h.contains("bid") && !h.contains("vol"))?;
    let ask_idx = lower.iter().position(|h| h.contains("ask") && !h.contains("vol"))?;
    Some((ts_idx, bid_idx, ask_idx))
}

// ─────────────────────────────────────────────────────────
// H1 bar truncation
// ─────────────────────────────────────────────────────────

fn bar_open(ts: &DateTime<Utc>) -> DateTime<Utc> {
    Utc.with_ymd_and_hms(ts.year(), ts.month(), ts.day(), ts.hour(), 0, 0)
        .unwrap()
}

// ─────────────────────────────────────────────────────────
// Core parsing logic
// ─────────────────────────────────────────────────────────

fn parse_file(
    path: &str,
    start: &DateTime<Utc>,
    end: &DateTime<Utc>,
) -> Result<Vec<Bar>, String> {
    let file = File::open(path).map_err(|e| format!("Cannot open {path}: {e}"))?;
    let reader = BufReader::new(file);
    let mut lines = reader.lines();

    // Read header
    let header_line = match lines.next() {
        Some(Ok(l)) => l,
        _ => return Err(format!("Empty file: {path}")),
    };
    let headers: Vec<&str> = header_line.split(',').collect();
    let (ts_idx, bid_idx, ask_idx) = detect_columns(&headers)
        .ok_or_else(|| format!("Cannot detect columns in {path}: {header_line}"))?;

    let mut bars: Vec<Bar> = Vec::new();
    let mut current_bar: Option<Bar> = None;

    for line_result in lines {
        let line = match line_result {
            Ok(l) => l,
            Err(_) => continue,
        };
        if line.trim().is_empty() {
            continue;
        }

        let fields: Vec<&str> = line.split(',').collect();
        if fields.len() <= ts_idx.max(bid_idx).max(ask_idx) {
            continue;
        }

        let ts = match parse_ts(fields[ts_idx]) {
            Some(t) => t,
            None => continue,
        };
        if ts < *start || ts >= *end {
            continue;
        }

        let bid: f64 = match fields[bid_idx].trim().parse() {
            Ok(v) => v,
            Err(_) => continue,
        };
        let ask: f64 = match fields[ask_idx].trim().parse() {
            Ok(v) => v,
            Err(_) => continue,
        };

        let mid = (bid + ask) * 0.5;
        let spread = ask - bid;
        let bar_time = bar_open(&ts);

        match current_bar.as_mut() {
            Some(bar) if bar.open_time == bar_time => {
                // Same bar — update
                if mid > bar.high { bar.high = mid; }
                if mid < bar.low  { bar.low  = mid; }
                bar.close = mid;
                if spread > bar.max_spread { bar.max_spread = spread; }
                bar.tick_count += 1;
            }
            _ => {
                // New bar boundary
                if let Some(finished) = current_bar.take() {
                    bars.push(finished);
                }
                current_bar = Some(Bar {
                    open_time: bar_time,
                    open: mid,
                    high: mid,
                    low: mid,
                    close: mid,
                    max_spread: spread,
                    tick_count: 1,
                });
            }
        }
    }
    if let Some(bar) = current_bar {
        bars.push(bar);
    }

    Ok(bars)
}

// ─────────────────────────────────────────────────────────
// Python-exposed function
// ─────────────────────────────────────────────────────────

/// parse_to_bars(path, start_iso, end_iso) -> list[dict]
///
/// Parse a Dukascopy CSV file into H1 OHLC bar dicts.
/// start_iso and end_iso are inclusive start / exclusive end in ISO-8601 UTC.
/// Returns an empty list if the file has no ticks in range.
#[pyfunction]
fn parse_to_bars(
    py: Python<'_>,
    path: &str,
    start_iso: &str,
    end_iso: &str,
) -> PyResult<PyObject> {
    let start: DateTime<Utc> = start_iso.parse().map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Bad start_iso {start_iso}: {e}"))
    })?;
    let end: DateTime<Utc> = end_iso.parse().map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Bad end_iso {end_iso}: {e}"))
    })?;

    let bars = parse_file(path, &start, &end).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(e)
    })?;

    let py_list = pyo3::types::PyList::empty_bound(py);
    for bar in bars {
        let d = pyo3::types::PyDict::new_bound(py);
        d.set_item("open_time", bar.open_time.to_rfc3339())?;
        d.set_item("open",      bar.open)?;
        d.set_item("high",      bar.high)?;
        d.set_item("low",       bar.low)?;
        d.set_item("close",     bar.close)?;
        d.set_item("max_spread", bar.max_spread)?;
        d.set_item("tick_count", bar.tick_count)?;
        py_list.append(d)?;
    }

    Ok(py_list.into_any().unbind())
}

// ─────────────────────────────────────────────────────────
// Module definition
// ─────────────────────────────────────────────────────────

#[pymodule]
fn tick_parser(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_to_bars, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
