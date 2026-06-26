use crate::types::{BoardRow, Part, ROUND_EPSILON, WeightTable};
use anyhow::{Context, Result, bail};
use calamine::{Reader, open_workbook_auto};
use chrono::{Datelike, Local, NaiveDate};
use csv::{ReaderBuilder, WriterBuilder};
use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::HashMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

const CHAR_THICK: &str = "厚";
const CHAR_QTY: &str = "数量";
const CHAR_PIECES: &[&str] = &["块", "件", "片", "个"];
const CHAR_ENUM_SEPARATORS: &[&str] = &["、"];

static LEADING_INDEX_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*\d+\s*[\.\)\]]\s*").expect("valid regex"));

fn piece_units_pattern() -> String {
    format!(
        r"(?:{}|{}|{}|{}|pcs|pc)",
        CHAR_PIECES[0], CHAR_PIECES[1], CHAR_PIECES[2], CHAR_PIECES[3]
    )
}

pub fn normalize_header(value: &str) -> String {
    let text = value.trim().to_lowercase();
    match text.as_str() {
        "thickness" | "thickness_mm" | "厚度" => "thickness".into(),
        "weight" | "weight_per_m2" | "kg_per_m2" | "kg/m2" | "kg/平米" | "重量"
        | "每平方米重量" | "每平米重量" | "每平米kg" => "weight".into(),
        "length" | "长度" => "length".into(),
        "width" | "宽度" => "width".into(),
        "quantity" | "数量" => "quantity".into(),
        other => other.into(),
    }
}

pub fn normalize_filename(filename: &str) -> Result<String> {
    let mut name = filename.trim().trim_matches('"').to_string();
    if name.is_empty() {
        bail!("文件名不能为空");
    }
    if !name.to_lowercase().ends_with(".csv") {
        name.push_str(".csv");
    }
    if name
        .chars()
        .any(|ch| matches!(ch, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'))
    {
        bail!("文件名包含 Windows 非法字符: {name}");
    }
    Ok(name)
}

pub fn filename_stem(filename: &str) -> String {
    Path::new(filename)
        .file_stem()
        .and_then(|stem| stem.to_str())
        .unwrap_or(filename)
        .to_string()
}

pub fn parse_archive_date(value: Option<&str>) -> Result<NaiveDate> {
    match value {
        None => Ok(Local::now().date_naive()),
        Some(text) if text.trim().is_empty() => Ok(Local::now().date_naive()),
        Some(text) => {
            let text = text.trim();
            let separators = ['-', '.', '/'];
            let parts: Vec<_> = text.split(|ch| separators.contains(&ch)).collect();
            if parts.len() != 3 {
                bail!("--date 格式应类似 2026-6-24");
            }
            let year: i32 = parts[0].parse().context("无效的日期年份")?;
            let month: u32 = parts[1].parse().context("无效的日期月份")?;
            let day: u32 = parts[2].parse().context("无效的日期日份")?;
            NaiveDate::from_ymd_opt(year, month, day).context("无效的归档日期")
        }
    }
}

pub fn build_output_path(
    archive_root: &Path,
    date_value: NaiveDate,
    filename: &str,
    customer: Option<&str>,
) -> Result<PathBuf> {
    let month_dir = format!("{}-{}", date_value.year(), date_value.month());
    let day_dir = format!("{}-{}", date_value.month(), date_value.day());
    let mut output_dir = archive_root.join(month_dir).join(day_dir);
    let folder_name = customer
        .map(|value| value.trim().trim_matches('"').to_string())
        .unwrap_or_else(|| filename_stem(filename));
    if folder_name.is_empty() {
        bail!("目录名不能为空");
    }
    if folder_name
        .chars()
        .any(|ch| matches!(ch, '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*'))
    {
        bail!("目录名包含 Windows 非法字符: {folder_name}");
    }
    output_dir.push(folder_name);
    fs::create_dir_all(&output_dir)
        .with_context(|| format!("创建归档目录失败: {}", output_dir.display()))?;
    Ok(output_dir.join(filename))
}

pub fn read_raw_data_from_args(data: Option<&str>, input: Option<&Path>) -> Result<String> {
    if let Some(raw) = data {
        return Ok(raw.to_string());
    }
    if let Some(path) = input {
        let mut text = fs::read_to_string(path)
            .with_context(|| format!("读取输入文件失败: {}", path.display()))?;
        if text.starts_with('\u{feff}') {
            text.remove(0);
        }
        return Ok(text);
    }
    let mut buffer = String::new();
    std::io::stdin()
        .read_to_string(&mut buffer)
        .context("读取 stdin 失败")?;
    if buffer.trim().is_empty() {
        bail!("没有输入数据，请传入 --data、--input，或通过 stdin 传入");
    }
    Ok(buffer)
}

pub fn discover_weight_table_path() -> Option<PathBuf> {
    let mut candidates = Vec::new();
    let mut roots = Vec::new();
    if let Ok(current_exe) = std::env::current_exe() {
        if let Some(parent) = current_exe.parent() {
            roots.push(parent.to_path_buf());
        }
    }
    if let Ok(current_dir) = std::env::current_dir() {
        roots.push(current_dir);
    }

    for root in roots {
        if let Ok(entries) = fs::read_dir(root) {
            for entry in entries.flatten() {
                let path = entry.path();
                if !path.is_file() {
                    continue;
                }
                let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
                    continue;
                };
                let lower = name.to_lowercase();
                if lower.ends_with(".csv") && (lower.contains("weight") || name.contains("重量"))
                {
                    candidates.push(path);
                }
            }
        }
    }

    candidates.sort();
    candidates.into_iter().next()
}

pub fn load_weight_table(path: &Path) -> Result<WeightTable> {
    let mut reader = ReaderBuilder::new()
        .has_headers(true)
        .from_path(path)
        .with_context(|| format!("打开重量表失败: {}", path.display()))?;
    let headers = reader
        .headers()
        .with_context(|| format!("重量表没有表头: {}", path.display()))?
        .iter()
        .map(normalize_header)
        .collect::<Vec<_>>();

    let required = ["thickness", "weight"];
    for key in required {
        if !headers.iter().any(|header| header == key) {
            bail!("重量表缺少列: {key}");
        }
    }

    let mut table = Vec::new();
    for (row_no, record) in reader.records().enumerate() {
        let row_no = row_no + 2;
        let record = record.with_context(|| format!("读取重量表第 {row_no} 行失败"))?;
        let mut thickness = None;
        let mut weight = None;
        for (index, value) in record.iter().enumerate() {
            match headers.get(index).map(|s| s.as_str()) {
                Some("thickness") => thickness = value.trim().parse::<f64>().ok(),
                Some("weight") => weight = value.trim().parse::<f64>().ok(),
                _ => {}
            }
        }
        let Some(thickness) = thickness else { continue };
        let Some(weight) = weight else { continue };
        if thickness <= 0.0 || weight <= 0.0 {
            bail!("重量表第 {row_no} 行必须为正数");
        }
        table.push((thickness, weight));
    }

    if table.is_empty() {
        bail!("重量表没有可用数据: {}", path.display());
    }
    table.sort_by(|left, right| {
        left.0
            .partial_cmp(&right.0)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    Ok(table)
}

pub fn weight_for_thickness(thickness: f64, weight_table: Option<&WeightTable>) -> Option<f64> {
    let table = weight_table?;
    table
        .iter()
        .find(|(candidate, _)| (candidate - thickness).abs() < ROUND_EPSILON)
        .map(|(_, weight)| *weight)
}

pub fn validate_thicknesses(rows: &[BoardRow], weight_table: Option<&WeightTable>) -> Result<()> {
    let Some(weight_table) = weight_table else {
        return Ok(());
    };
    let allowed: Vec<f64> = weight_table
        .iter()
        .map(|(thickness, _)| *thickness)
        .collect();
    for row in rows {
        if !allowed
            .iter()
            .any(|candidate| (candidate - row.thickness).abs() < ROUND_EPSILON)
        {
            let allowed_text = allowed
                .iter()
                .map(|value| fmt_number(*value))
                .collect::<Vec<_>>()
                .join(", ");
            bail!(
                "第 {} 行：厚度 {} 不在重量表中。允许的厚度有：{}",
                row.source_line,
                row.thickness,
                allowed_text
            );
        }
    }
    Ok(())
}

pub fn normalize_line(line: &str) -> String {
    let mut normalized = line.trim().to_string();
    for marker in CHAR_ENUM_SEPARATORS {
        normalized = normalized.replace(marker, " ");
    }
    normalized = normalized
        .replace('×', "x")
        .replace('*', "x")
        .replace('X', "x")
        .replace('，', ",")
        .replace('；', ";")
        .replace('：', ":");
    LEADING_INDEX_RE.replace(&normalized, "").to_string()
}

#[derive(Debug, Clone, Copy, PartialEq)]
struct NumberToken {
    value: f64,
    start: usize,
    end: usize,
}

fn find_numbers(line: &str) -> Vec<NumberToken> {
    let re = Regex::new(r"\d+(?:\.\d+)?").expect("valid regex");
    re.find_iter(line)
        .filter_map(|m| {
            m.as_str().parse::<f64>().ok().map(|value| NumberToken {
                value,
                start: m.start(),
                end: m.end(),
            })
        })
        .collect()
}

fn find_quantity(line: &str) -> Option<NumberToken> {
    let piece_units = piece_units_pattern();
    let patterns = [
        format!(r"(?:{}|qty|q)\s*[:=]?\s*(\d+(?:\.\d+)?)", CHAR_QTY),
        format!(r"(\d+(?:\.\d+)?)\s*(?:{})(?=\s|$)", piece_units),
    ];
    let mut matches = Vec::new();
    for pattern in patterns {
        let re = Regex::new(&pattern).expect("valid quantity regex");
        for caps in re.captures_iter(line) {
            if let Some(m) = caps.get(1) {
                if let Ok(value) = m.as_str().parse::<f64>() {
                    matches.push(NumberToken {
                        value,
                        start: m.start(),
                        end: m.end(),
                    });
                }
            }
        }
    }
    matches.into_iter().max_by_key(|token| token.start)
}

fn find_thickness(line: &str) -> Option<NumberToken> {
    let patterns = [
        format!(
            r"(?:{}|{}度)\s*[:=]?\s*(\d+(?:\.\d+)?)",
            CHAR_THICK, CHAR_THICK
        ),
        String::from(r"\bt\s*[:=]?\s*(\d+(?:\.\d+)?)"),
        format!(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*{}(?!\s*\d)", CHAR_THICK),
    ];
    for pattern in patterns {
        let re = Regex::new(&pattern).expect("valid thickness regex");
        let mut matches = Vec::new();
        for caps in re.captures_iter(line) {
            if let Some(m) = caps.get(1) {
                if let Ok(value) = m.as_str().parse::<f64>() {
                    matches.push(NumberToken {
                        value,
                        start: m.start(),
                        end: m.end(),
                    });
                }
            }
        }
        if let Some(token) = matches.into_iter().min_by_key(|token| token.start) {
            return Some(token);
        }
    }
    None
}

fn same_span(left: NumberToken, right: NumberToken) -> bool {
    left.start == right.start && left.end == right.end
}

fn as_quantity(value: f64, line_no: usize) -> Result<usize> {
    let quantity = value as usize;
    if quantity == 0 || (quantity as f64 - value).abs() > ROUND_EPSILON {
        bail!("第 {line_no} 行：数量必须为正整数，当前值为 {value}");
    }
    Ok(quantity)
}

fn split_thickness_and_quantity(suffix_digits: &str, line_no: usize) -> Option<(f64, usize)> {
    if suffix_digits.len() < 2 {
        return None;
    }
    for split_at in (1..suffix_digits.len()).rev() {
        let thickness_text = &suffix_digits[..split_at];
        let quantity_text = &suffix_digits[split_at..];
        let Ok(thickness_value) = thickness_text.parse::<f64>() else {
            continue;
        };
        let Ok(quantity_value) = quantity_text.parse::<f64>() else {
            continue;
        };
        if thickness_value <= 0.0 || thickness_value >= 100.0 {
            continue;
        }
        let Ok(quantity_int) = as_quantity(quantity_value, line_no) else {
            continue;
        };
        return Some((thickness_value, quantity_int));
    }
    None
}

fn parse_by_x_groups(line: &str, line_no: usize) -> Option<BoardRow> {
    let compact = normalize_line(line).to_lowercase().replace(' ', "");
    let piece_units = piece_units_pattern();

    let re = Regex::new(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)mm(\d+)?$").unwrap();
    if let Some(caps) = re.captures(&compact) {
        return Some(BoardRow {
            length: caps.get(1)?.as_str().parse().ok()?,
            width: caps.get(2)?.as_str().parse().ok()?,
            thickness: caps.get(3)?.as_str().parse().ok()?,
            quantity: caps
                .get(4)
                .map(|m| as_quantity(m.as_str().parse::<f64>().ok()?, line_no).ok())
                .flatten()
                .unwrap_or(1),
            source_line: line_no,
        });
    }

    let re =
        Regex::new(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:mm)?(\d+)\D+$").unwrap();
    if let Some(caps) = re.captures(&compact) {
        return Some(BoardRow {
            length: caps.get(1)?.as_str().parse().ok()?,
            width: caps.get(2)?.as_str().parse().ok()?,
            thickness: caps.get(3)?.as_str().parse().ok()?,
            quantity: as_quantity(caps.get(4)?.as_str().parse::<f64>().ok()?, line_no).ok()?,
            source_line: line_no,
        });
    }

    let re = Regex::new(&format!(
        r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:mm)?(\d+){}$",
        piece_units
    ))
    .unwrap();
    if let Some(caps) = re.captures(&compact) {
        return Some(BoardRow {
            length: caps.get(1)?.as_str().parse().ok()?,
            width: caps.get(2)?.as_str().parse().ok()?,
            thickness: caps.get(3)?.as_str().parse().ok()?,
            quantity: as_quantity(caps.get(4)?.as_str().parse::<f64>().ok()?, line_no).ok()?,
            source_line: line_no,
        });
    }

    let re = Regex::new(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:mm)?$").unwrap();
    if let Some(caps) = re.captures(&compact) {
        return Some(BoardRow {
            length: caps.get(1)?.as_str().parse().ok()?,
            width: caps.get(2)?.as_str().parse().ok()?,
            thickness: caps.get(3)?.as_str().parse().ok()?,
            quantity: 1,
            source_line: line_no,
        });
    }

    let re = Regex::new(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$").unwrap();
    if let Some(caps) = re.captures(&compact) {
        return Some(BoardRow {
            length: caps.get(1)?.as_str().parse().ok()?,
            width: caps.get(2)?.as_str().parse().ok()?,
            thickness: caps.get(3)?.as_str().parse().ok()?,
            quantity: 1,
            source_line: line_no,
        });
    }

    let re = Regex::new(&format!(
        r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)x(\d+){}$",
        piece_units
    ))
    .unwrap();
    if let Some(caps) = re.captures(&compact) {
        if let Some((thickness_value, quantity_int)) =
            split_thickness_and_quantity(caps.get(3)?.as_str(), line_no)
        {
            return Some(BoardRow {
                length: caps.get(1)?.as_str().parse().ok()?,
                width: caps.get(2)?.as_str().parse().ok()?,
                thickness: thickness_value,
                quantity: quantity_int,
                source_line: line_no,
            });
        }
    }

    let re = Regex::new(&format!(
        r"^(?:{}|{}度)(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)$",
        CHAR_THICK, CHAR_THICK
    ))
    .unwrap();
    if let Some(caps) = re.captures(&compact) {
        return Some(BoardRow {
            length: caps.get(2)?.as_str().parse().ok()?,
            width: caps.get(3)?.as_str().parse().ok()?,
            thickness: caps.get(1)?.as_str().parse().ok()?,
            quantity: 1,
            source_line: line_no,
        });
    }

    let re = Regex::new(&format!(
        r"^(?:{}|{}度)(\d+)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?){})$",
        CHAR_THICK, CHAR_THICK, piece_units
    ))
    .ok()?;
    if let Some(caps) = re.captures(&compact) {
        if let Some((thickness_value, quantity_int)) =
            split_thickness_and_quantity(caps.get(1)?.as_str(), line_no)
        {
            return Some(BoardRow {
                length: caps.get(2)?.as_str().parse().ok()?,
                width: caps.get(3)?.as_str().parse().ok()?,
                thickness: thickness_value,
                quantity: quantity_int,
                source_line: line_no,
            });
        }
    }

    let re = Regex::new(&format!(
        r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:{}|{}度)(\d+(?:\.\d+)?)(?:{})?(?:({}))?$",
        CHAR_THICK, CHAR_THICK, CHAR_QTY, piece_units
    ))
    .unwrap();
    if let Some(caps) = re.captures(&compact) {
        let quantity = caps
            .get(4)
            .and_then(|m| m.as_str().parse::<f64>().ok())
            .map(|value| as_quantity(value, line_no))
            .transpose()
            .ok()?
            .unwrap_or(1);
        return Some(BoardRow {
            length: caps.get(1)?.as_str().parse().ok()?,
            width: caps.get(2)?.as_str().parse().ok()?,
            thickness: caps.get(3)?.as_str().parse().ok()?,
            quantity,
            source_line: line_no,
        });
    }

    let qty_regexes = [
        r"(\d+(?:\.\d+)?)(?:pcs|pc)\b".to_string(),
        format!(r"(\d+(?:\.\d+)?){}", piece_units),
        format!(r"{}[:=]?(\d+(?:\.\d+)?)", CHAR_QTY),
    ];
    let mut qty_match: Option<NumberToken> = None;
    for pattern in qty_regexes {
        let re = Regex::new(&pattern).unwrap();
        if let Some(caps) = re.captures(&compact) {
            if let Some(m) = caps.get(1) {
                if let Ok(value) = m.as_str().parse::<f64>() {
                    qty_match = Some(NumberToken {
                        value,
                        start: m.start(),
                        end: m.end(),
                    });
                    break;
                }
            }
        }
    }

    let mut compact_without_qty = compact.clone();
    if let Some(qty) = qty_match {
        compact_without_qty = format!("{}{}", &compact[..qty.start], &compact[qty.end..]);
    }

    let re = Regex::new(&format!(
        r"^(?:{}|{}度)(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)",
        CHAR_THICK, CHAR_THICK
    ))
    .unwrap();
    if let Some(caps) = re.captures(&compact_without_qty) {
        let thickness = caps.get(1)?.as_str().parse().ok()?;
        let length = caps.get(2)?.as_str().parse().ok()?;
        let width = caps.get(3)?.as_str().parse().ok()?;
        let quantity = qty_match
            .map(|token| as_quantity(token.value, line_no).ok())
            .flatten()
            .unwrap_or(1);
        return Some(BoardRow {
            length,
            width,
            thickness,
            quantity,
            source_line: line_no,
        });
    }

    let re = Regex::new(&format!(
        r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:{}|{}度)(\d+(?:\.\d+)?)",
        CHAR_THICK, CHAR_THICK
    ))
    .unwrap();
    if let Some(caps) = re.captures(&compact_without_qty) {
        let thickness = caps.get(3)?.as_str().parse().ok()?;
        let length = caps.get(1)?.as_str().parse().ok()?;
        let width = caps.get(2)?.as_str().parse().ok()?;
        let quantity = qty_match
            .map(|token| as_quantity(token.value, line_no).ok())
            .flatten()
            .unwrap_or(1);
        return Some(BoardRow {
            length,
            width,
            thickness,
            quantity,
            source_line: line_no,
        });
    }

    let re = Regex::new(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(\d+(?:\.\d+)?)").unwrap();
    if let Some(caps) = re.captures(&compact_without_qty) {
        if compact_without_qty.matches('x').count() == 2 {
            let quantity = qty_match
                .map(|token| as_quantity(token.value, line_no).ok())
                .flatten()
                .unwrap_or(1);
            return Some(BoardRow {
                length: caps.get(1)?.as_str().parse().ok()?,
                width: caps.get(2)?.as_str().parse().ok()?,
                thickness: caps.get(3)?.as_str().parse().ok()?,
                quantity,
                source_line: line_no,
            });
        }
    }

    None
}

fn parse_numeric_fallback(numbers: &[NumberToken], line_no: usize) -> Option<BoardRow> {
    let values = numbers.iter().map(|token| token.value).collect::<Vec<_>>();
    if values.len() == 4 {
        let (a, b, c, d) = (values[0], values[1], values[2], values[3]);
        if a < 100.0 && b >= 100.0 && c >= 100.0 && (d - d.round()).abs() < ROUND_EPSILON {
            return Some(BoardRow {
                length: b,
                width: c,
                thickness: a,
                quantity: as_quantity(d, line_no).ok()?,
                source_line: line_no,
            });
        }
        if c < 100.0 && a >= 100.0 && b >= 100.0 && (d - d.round()).abs() < ROUND_EPSILON {
            return Some(BoardRow {
                length: a,
                width: b,
                thickness: c,
                quantity: as_quantity(d, line_no).ok()?,
                source_line: line_no,
            });
        }
    }
    None
}

pub fn parse_board_line(
    line: &str,
    line_no: usize,
    default_thickness: Option<f64>,
) -> Result<Option<BoardRow>> {
    let normalized = normalize_line(line);
    if normalized.is_empty() || normalized.starts_with('#') {
        return Ok(None);
    }

    if let Some(row) = parse_by_x_groups(&normalized, line_no) {
        return Ok(Some(row));
    }

    let numbers = find_numbers(&normalized);
    if numbers.is_empty() {
        return Ok(None);
    }

    if let Some(row) = parse_numeric_fallback(&numbers, line_no) {
        return Ok(Some(row));
    }

    let quantity_token = find_quantity(&normalized);
    let thickness_token = find_thickness(&normalized);
    let remaining = numbers
        .iter()
        .copied()
        .filter(|token| {
            !(quantity_token.is_some_and(|qty| same_span(*token, qty))
                || thickness_token.is_some_and(|thick| same_span(*token, thick)))
        })
        .collect::<Vec<_>>();

    let quantity = quantity_token
        .map(|token| as_quantity(token.value, line_no))
        .transpose()?;
    let thickness = thickness_token.map(|token| token.value);

    let (length, width, thickness, quantity) = if let Some(thickness) = thickness {
        if remaining.len() < 2 {
            bail!("第 {line_no} 行：无法识别长度和宽度：{line}");
        }
        let (length, width) = if thickness_token
            .is_some_and(|token| token.start <= remaining[0].start)
            && remaining.len() >= 2
        {
            (
                remaining[remaining.len() - 2].value,
                remaining[remaining.len() - 1].value,
            )
        } else {
            (remaining[0].value, remaining[1].value)
        };
        (length, width, thickness, quantity.unwrap_or(1))
    } else if let Some(quantity) = quantity {
        if remaining.len() >= 3 {
            (
                remaining[0].value,
                remaining[1].value,
                remaining[2].value,
                quantity,
            )
        } else if remaining.len() >= 2 {
            let Some(default_thickness) = default_thickness else {
                bail!("第 {line_no} 行：无法识别长度、宽度和厚度：{line}");
            };
            (
                remaining[0].value,
                remaining[1].value,
                default_thickness,
                quantity,
            )
        } else {
            bail!("第 {line_no} 行：无法识别长度、宽度和厚度：{line}");
        }
    } else if remaining.len() >= 4 {
        (
            remaining[0].value,
            remaining[1].value,
            remaining[2].value,
            as_quantity(remaining[3].value, line_no)?,
        )
    } else if remaining.len() == 3 {
        (
            remaining[0].value,
            remaining[1].value,
            remaining[2].value,
            1,
        )
    } else if remaining.len() == 2 {
        let Some(default_thickness) = default_thickness else {
            bail!("第 {line_no} 行：无法识别开料尺寸：{line}");
        };
        (remaining[0].value, remaining[1].value, default_thickness, 1)
    } else {
        bail!("第 {line_no} 行：无法识别开料尺寸：{line}");
    };

    if length <= 0.0 || width <= 0.0 || thickness <= 0.0 {
        bail!("第 {line_no} 行：长度、宽度和厚度必须大于 0");
    }

    Ok(Some(BoardRow {
        length,
        width,
        thickness,
        quantity,
        source_line: line_no,
    }))
}

pub fn parse_board_data(raw: &str, default_thickness: Option<f64>) -> Result<Vec<BoardRow>> {
    let mut rows = Vec::new();
    for (line_no, line) in raw.lines().enumerate() {
        if let Some(row) = parse_board_line(line, line_no + 1, default_thickness)? {
            rows.push(row);
        }
    }
    if rows.is_empty() {
        bail!("没有解析到有效的板件数据");
    }
    Ok(rows)
}

pub fn combine_rows(rows: &[BoardRow]) -> Vec<BoardRow> {
    let mut grouped: HashMap<(u64, u64, u64), (usize, usize)> = HashMap::new();
    for row in rows {
        let key = (
            row.length.to_bits(),
            row.width.to_bits(),
            row.thickness.to_bits(),
        );
        let entry = grouped.entry(key).or_insert((0, row.source_line));
        entry.0 += row.quantity;
    }
    let mut combined = grouped
        .into_iter()
        .map(
            |((length_bits, width_bits, thickness_bits), (quantity, first_line))| BoardRow {
                length: f64::from_bits(length_bits),
                width: f64::from_bits(width_bits),
                thickness: f64::from_bits(thickness_bits),
                quantity,
                source_line: first_line,
            },
        )
        .collect::<Vec<_>>();
    combined.sort_by(|left, right| {
        left.thickness
            .partial_cmp(&right.thickness)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                left.length
                    .partial_cmp(&right.length)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| {
                left.width
                    .partial_cmp(&right.width)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
    });
    combined
}

pub fn fmt_number(value: f64) -> String {
    let rounded = value.round();
    if (value - rounded).abs() < ROUND_EPSILON {
        format!("{}", rounded as i64)
    } else {
        format!("{value:.1}")
    }
}

pub fn write_csv(path: &Path, rows: &[BoardRow]) -> Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut writer = WriterBuilder::new()
        .has_headers(true)
        .from_path(path)
        .with_context(|| format!("打开 CSV 写入失败: {}", path.display()))?;
    writer.write_record(["length", "width", "thickness", "quantity"])?;
    for row in rows {
        writer.write_record([
            fmt_number(row.length),
            fmt_number(row.width),
            fmt_number(row.thickness),
            row.quantity.to_string(),
        ])?;
    }
    writer.flush()?;
    Ok(())
}

pub fn convert_board_data_to_csv(
    raw_text: &str,
    filename: &str,
    archive_root: &Path,
    weight_table_path: Option<&Path>,
    customer: Option<&str>,
    archive_date: Option<NaiveDate>,
    default_thickness: Option<f64>,
    combine: bool,
) -> Result<(PathBuf, Vec<BoardRow>, Option<PathBuf>)> {
    let rows = parse_board_data(raw_text, default_thickness)?;
    let weight_table = match weight_table_path {
        Some(path) => Some(load_weight_table(path)?),
        None => None,
    };
    validate_thicknesses(&rows, weight_table.as_ref())?;
    let output_rows = if combine {
        combine_rows(&rows)
    } else {
        rows.clone()
    };
    let normalized_filename = normalize_filename(filename)?;
    let date_value = archive_date.unwrap_or_else(|| Local::now().date_naive());
    let output_path = build_output_path(archive_root, date_value, &normalized_filename, customer)?;
    write_csv(&output_path, &output_rows)?;
    Ok((
        output_path,
        output_rows,
        weight_table_path.map(|path| path.to_path_buf()),
    ))
}

#[derive(Debug, Clone)]
pub struct CsvRow {
    pub length: f64,
    pub width: f64,
    pub thickness: f64,
    pub quantity: usize,
}

fn parse_csv_row(headers: &[String], record: &csv::StringRecord, row_no: usize) -> Result<CsvRow> {
    let mut values: HashMap<&str, String> = HashMap::new();
    for (header, value) in headers.iter().zip(record.iter()) {
        values.insert(header.as_str(), value.trim().to_string());
    }
    let parse_f64 = |field: &str| -> Result<f64> {
        Ok(values
            .get(field)
            .and_then(|value| value.trim().parse::<f64>().ok())
            .with_context(|| format!("第 {row_no} 行：{field} 必须是数字"))?)
    };
    let length = parse_f64("length")?;
    let width = parse_f64("width")?;
    let thickness = parse_f64("thickness")?;
    let quantity = values
        .get("quantity")
        .and_then(|value| value.trim().parse::<usize>().ok())
        .with_context(|| format!("第 {row_no} 行：quantity 必须为正整数"))?;
    if length <= 0.0 || width <= 0.0 || thickness <= 0.0 || quantity == 0 {
        bail!("第 {row_no} 行：数值必须为正");
    }
    Ok(CsvRow {
        length,
        width,
        thickness,
        quantity,
    })
}

fn parse_xlsx_row(headers: &[String], row: &[String], row_no: usize) -> Result<CsvRow> {
    let mut values: HashMap<&str, String> = HashMap::new();
    for (header, value) in headers.iter().zip(row.iter()) {
        values.insert(header.as_str(), value.trim().to_string());
    }
    let parse_f64 = |field: &str| -> Result<f64> {
        Ok(values
            .get(field)
            .and_then(|value| value.trim().parse::<f64>().ok())
            .with_context(|| format!("第 {row_no} 行：{field} 必须是数字"))?)
    };
    let length = parse_f64("length")?;
    let width = parse_f64("width")?;
    let thickness = parse_f64("thickness")?;
    let quantity = values
        .get("quantity")
        .and_then(|value| value.trim().parse::<usize>().ok())
        .with_context(|| format!("第 {row_no} 行：quantity 必须为正整数"))?;
    if length <= 0.0 || width <= 0.0 || thickness <= 0.0 || quantity == 0 {
        bail!("第 {row_no} 行：数值必须为正");
    }
    Ok(CsvRow {
        length,
        width,
        thickness,
        quantity,
    })
}

pub fn read_parts_from_path(path: &Path) -> Result<Vec<Part>> {
    match path
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| ext.to_lowercase())
    {
        Some(ext) if ext == "csv" => read_parts_from_csv(path),
        Some(ext) if ext == "xlsx" => read_parts_from_xlsx(path),
        _ => bail!("输入文件必须是 .csv 或 .xlsx: {}", path.display()),
    }
}

fn read_parts_from_csv(path: &Path) -> Result<Vec<Part>> {
    let mut reader = ReaderBuilder::new()
        .has_headers(true)
        .from_path(path)
        .with_context(|| format!("打开 CSV 失败: {}", path.display()))?;
    let headers = reader
        .headers()
        .with_context(|| format!("CSV 没有表头: {}", path.display()))?
        .iter()
        .map(normalize_header)
        .collect::<Vec<_>>();
    let required = ["length", "width", "thickness", "quantity"];
    for key in required {
        if !headers.iter().any(|header| header == key) {
            bail!("输入缺少必要列: {key}");
        }
    }

    let mut parts = Vec::new();
    let mut next_item_no = 1usize;
    for (index, record) in reader.records().enumerate() {
        let row_no = index + 2;
        let record = record.with_context(|| format!("读取 CSV 第 {row_no} 行失败"))?;
        if record.iter().all(|cell| cell.trim().is_empty()) {
            continue;
        }
        let row = parse_csv_row(&headers, &record, row_no)?;
        for _ in 0..row.quantity {
            parts.push(Part {
                source_row: row_no,
                item_no: next_item_no,
                length: row.length,
                width: row.width,
                thickness: row.thickness,
                area: row.length * row.width,
            });
            next_item_no += 1;
        }
    }
    if parts.is_empty() {
        bail!("输入文件没有任何板件");
    }
    Ok(parts)
}

fn read_parts_from_xlsx(path: &Path) -> Result<Vec<Part>> {
    let mut workbook =
        open_workbook_auto(path).with_context(|| format!("打开 XLSX 失败: {}", path.display()))?;
    let sheet_name = workbook
        .sheet_names()
        .first()
        .cloned()
        .context("XLSX 没有工作表")?;
    let range = workbook
        .worksheet_range(&sheet_name)
        .with_context(|| format!("读取 XLSX 工作表失败: {sheet_name}"))?;
    let mut rows = range.rows();
    let headers = rows
        .next()
        .context("XLSX 文件为空")?
        .iter()
        .map(|value| normalize_header(&value.to_string()))
        .collect::<Vec<_>>();
    let required = ["length", "width", "thickness", "quantity"];
    for key in required {
        if !headers.iter().any(|header| header == key) {
            bail!("输入缺少必要列: {key}");
        }
    }

    let mut parts = Vec::new();
    let mut next_item_no = 1usize;
    for (index, row) in rows.enumerate() {
        let row_no = index + 2;
        let values = row.iter().map(|cell| cell.to_string()).collect::<Vec<_>>();
        if values.iter().all(|cell| cell.trim().is_empty()) {
            continue;
        }
        let row = parse_xlsx_row(&headers, &values, row_no)?;
        for _ in 0..row.quantity {
            parts.push(Part {
                source_row: row_no,
                item_no: next_item_no,
                length: row.length,
                width: row.width,
                thickness: row.thickness,
                area: row.length * row.width,
            });
            next_item_no += 1;
        }
    }
    if parts.is_empty() {
        bail!("输入文件没有任何板件");
    }
    Ok(parts)
}
