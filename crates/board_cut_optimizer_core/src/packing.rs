use crate::parsing::{
    discover_weight_table_path, fmt_number, load_weight_table, read_parts_from_path,
    weight_for_thickness,
};
use crate::report::default_report_png_path;
use crate::types::*;
use anyhow::{Result, bail};
use csv::WriterBuilder;
use std::cmp::Ordering;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub fn ceil_to_tenth(value: f64) -> f64 {
    ((value - ROUND_EPSILON) * 10.0).ceil() / 10.0
}

pub fn orientation_options(part: Part) -> Vec<(f64, f64, bool)> {
    let normal = (part.length, part.width, false);
    let rotated = (part.width, part.length, true);
    if (part.length - part.width).abs() < ROUND_EPSILON {
        vec![normal]
    } else {
        vec![normal, rotated]
    }
}

pub fn fits_in_available_area(part: Part, available_width: f64, available_height: f64) -> bool {
    orientation_options(part)
        .into_iter()
        .any(|(width, height, _)| {
            width <= available_width + ROUND_EPSILON && height <= available_height + ROUND_EPSILON
        })
}

pub fn sort_free_rects(free_rects: impl IntoIterator<Item = FreeRect>) -> Vec<FreeRect> {
    let mut rects = free_rects
        .into_iter()
        .filter(|rect| rect.width > ROUND_EPSILON && rect.height > ROUND_EPSILON)
        .collect::<Vec<_>>();
    rects.sort_by(|left, right| {
        left.y
            .partial_cmp(&right.y)
            .unwrap_or(Ordering::Equal)
            .then_with(|| left.x.partial_cmp(&right.x).unwrap_or(Ordering::Equal))
            .then_with(|| {
                left.height
                    .partial_cmp(&right.height)
                    .unwrap_or(Ordering::Equal)
            })
            .then_with(|| {
                left.width
                    .partial_cmp(&right.width)
                    .unwrap_or(Ordering::Equal)
            })
    });
    rects
}

pub fn prune_free_rects(free_rects: impl IntoIterator<Item = FreeRect>) -> Vec<FreeRect> {
    let cleaned = sort_free_rects(free_rects);
    let mut result = Vec::new();
    for (index, rect) in cleaned.iter().enumerate() {
        let mut contained = false;
        for (other_index, other) in cleaned.iter().enumerate() {
            if index == other_index {
                continue;
            }
            if rect.x >= other.x - ROUND_EPSILON
                && rect.y >= other.y - ROUND_EPSILON
                && rect.x + rect.width <= other.x + other.width + ROUND_EPSILON
                && rect.y + rect.height <= other.y + other.height + ROUND_EPSILON
            {
                contained = true;
                break;
            }
        }
        if !contained {
            result.push(*rect);
        }
    }
    sort_free_rects(result)
}

pub fn split_free_rect(
    rect: FreeRect,
    placed_width: f64,
    placed_height: f64,
    available_width: f64,
    available_height: f64,
) -> Vec<FreeRect> {
    let occupied_width = placed_width + MIN_KERF;
    let occupied_height = placed_height + MIN_KERF;
    let right_width = rect.width - occupied_width;
    let bottom_height = rect.height - occupied_height;

    let mut new_rects = Vec::new();
    if right_width > ROUND_EPSILON {
        new_rects.push(FreeRect {
            x: rect.x + occupied_width,
            y: rect.y,
            width: right_width,
            height: placed_height,
        });
    }
    if bottom_height > ROUND_EPSILON {
        new_rects.push(FreeRect {
            x: rect.x,
            y: rect.y + occupied_height,
            width: rect.width,
            height: bottom_height,
        });
    }

    let mut clipped = Vec::new();
    for candidate in new_rects {
        let max_width = available_width - candidate.x;
        let max_height = available_height - candidate.y;
        clipped.push(FreeRect {
            x: candidate.x,
            y: candidate.y,
            width: candidate.width.min(max_width),
            height: candidate.height.min(max_height),
        });
    }
    clipped
}

pub fn find_position(
    sheet: &Sheet,
    part: Part,
    _available_width: f64,
    _available_height: f64,
) -> Option<(usize, f64, f64, bool)> {
    let mut best: Option<(f64, f64, f64, f64, usize, f64, f64, bool)> = None;
    for (rect_index, rect) in sheet.free_rects.iter().enumerate() {
        for (placed_width, placed_height, rotated) in orientation_options(part) {
            if placed_width <= rect.width + ROUND_EPSILON
                && placed_height <= rect.height + ROUND_EPSILON
            {
                let waste = rect.width * rect.height - placed_width * placed_height;
                let short_side_leftover =
                    (rect.width - placed_width).min(rect.height - placed_height);
                let score = (
                    waste,
                    short_side_leftover,
                    rect.y,
                    rect.x,
                    rect_index,
                    placed_width,
                    placed_height,
                    rotated,
                );
                if best.as_ref().map_or(true, |best_score| score < *best_score) {
                    best = Some(score);
                }
            }
        }
    }
    best.map(
        |(_, _, _, _, rect_index, placed_width, placed_height, rotated)| {
            (rect_index, placed_width, placed_height, rotated)
        },
    )
}

pub fn place_on_sheet(
    sheet: &mut Sheet,
    part: Part,
    available_width: f64,
    available_height: f64,
) -> bool {
    let Some((rect_index, placed_width, placed_height, rotated)) =
        find_position(sheet, part, available_width, available_height)
    else {
        return false;
    };

    let rect = sheet.free_rects.remove(rect_index);
    sheet.placements.push(Placement {
        part,
        sheet_no: sheet.sheet_no,
        x: EDGE_MARGIN + rect.x,
        y: EDGE_MARGIN + rect.y,
        length: placed_width,
        width: placed_height,
        rotated,
        row_no: 0,
        x_gap_after: 0.0,
        y_gap_after: 0.0,
    });
    sheet.free_rects.extend(split_free_rect(
        rect,
        placed_width,
        placed_height,
        available_width,
        available_height,
    ));
    sheet.free_rects = prune_free_rects(sheet.free_rects.clone());
    true
}

pub fn pack_ordered_parts(
    thickness: f64,
    ordered_parts: &[Part],
    available_width: f64,
    available_height: f64,
) -> Result<Vec<Sheet>> {
    let mut sheets: Vec<Sheet> = Vec::new();
    for part in ordered_parts {
        let mut placed = false;
        for sheet in &mut sheets {
            if place_on_sheet(sheet, *part, available_width, available_height) {
                placed = true;
                break;
            }
        }
        if !placed {
            let mut sheet = Sheet {
                sheet_no: sheets.len() + 1,
                thickness,
                placements: Vec::new(),
                free_rects: vec![FreeRect {
                    x: 0.0,
                    y: 0.0,
                    width: available_width,
                    height: available_height,
                }],
            };
            if !place_on_sheet(&mut sheet, *part, available_width, available_height) {
                bail!("排版失败：理论上可放下的板件无法放置");
            }
            sheets.push(sheet);
        }
    }
    Ok(sheets)
}

pub fn sorted_part_strategies(parts: &[Part]) -> Vec<Vec<Part>> {
    let strategies = vec![
        {
            let mut items = parts.to_vec();
            items.sort_by(|a, b| {
                (
                    b.area,
                    b.length.max(b.width),
                    b.length.min(b.width),
                    -(b.item_no as isize),
                )
                    .partial_cmp(&(
                        a.area,
                        a.length.max(a.width),
                        a.length.min(a.width),
                        -(a.item_no as isize),
                    ))
                    .unwrap_or(Ordering::Equal)
            });
            items
        },
        {
            let mut items = parts.to_vec();
            items.sort_by(|a, b| {
                (
                    b.length.max(b.width),
                    b.area,
                    b.length.min(b.width),
                    -(b.item_no as isize),
                )
                    .partial_cmp(&(
                        a.length.max(a.width),
                        a.area,
                        a.length.min(a.width),
                        -(a.item_no as isize),
                    ))
                    .unwrap_or(Ordering::Equal)
            });
            items
        },
        {
            let mut items = parts.to_vec();
            items.sort_by(|a, b| {
                (
                    b.length.min(b.width),
                    b.area,
                    b.length.max(b.width),
                    -(b.item_no as isize),
                )
                    .partial_cmp(&(
                        a.length.min(a.width),
                        a.area,
                        a.length.max(a.width),
                        -(a.item_no as isize),
                    ))
                    .unwrap_or(Ordering::Equal)
            });
            items
        },
        {
            let mut items = parts.to_vec();
            items.sort_by(|a, b| {
                (b.length, b.width, b.area, -(b.item_no as isize))
                    .partial_cmp(&(a.length, a.width, a.area, -(a.item_no as isize)))
                    .unwrap_or(Ordering::Equal)
            });
            items
        },
        {
            let mut items = parts.to_vec();
            items.sort_by(|a, b| {
                (b.width, b.length, b.area, -(b.item_no as isize))
                    .partial_cmp(&(a.width, a.length, a.area, -(a.item_no as isize)))
                    .unwrap_or(Ordering::Equal)
            });
            items
        },
    ];

    let mut unique = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for strategy in strategies {
        let signature = strategy.iter().map(|part| part.item_no).collect::<Vec<_>>();
        if seen.insert(signature) {
            unique.push(strategy);
        }
    }
    unique
}

pub fn repack_sheet_with_even_gaps(sheet: &Sheet, board_length: f64, board_width: f64) -> Sheet {
    let mut rows: HashMap<u64, Vec<Placement>> = HashMap::new();
    for placement in &sheet.placements {
        rows.entry(placement.y.to_bits())
            .or_default()
            .push(*placement);
    }

    let mut sorted_rows = rows
        .into_iter()
        .map(|(_, placements)| {
            let y = placements
                .first()
                .map(|placement| placement.y)
                .unwrap_or(0.0);
            (y, placements)
        })
        .collect::<Vec<_>>();
    sorted_rows.sort_by(|left, right| left.0.partial_cmp(&right.0).unwrap_or(Ordering::Equal));

    let row_heights = sorted_rows
        .iter()
        .map(|(_, placements)| {
            placements
                .iter()
                .map(|placement| placement.width)
                .fold(0.0, f64::max)
        })
        .collect::<Vec<_>>();
    let used_height_with_min_kerf =
        row_heights.iter().sum::<f64>() + MIN_KERF * row_heights.len().saturating_sub(1) as f64;
    let available_height = board_width - EDGE_MARGIN * 2.0;
    let extra_y_gap = if row_heights.len() > 1 {
        ((available_height - used_height_with_min_kerf).max(0.0)) / (row_heights.len() - 1) as f64
    } else {
        0.0
    };

    let mut new_placements = Vec::new();
    let mut cursor_y = EDGE_MARGIN;
    for (row_index, (_, placements)) in sorted_rows.iter().enumerate() {
        let mut row = placements.clone();
        row.sort_by(|a, b| a.x.partial_cmp(&b.x).unwrap_or(Ordering::Equal));
        let row_len = row.len();
        let row_width = row.iter().map(|placement| placement.length).sum::<f64>();
        let gaps = row_len.saturating_sub(1);
        let used_width_with_min_kerf = row_width + MIN_KERF * gaps as f64;
        let available_width = board_length - EDGE_MARGIN * 2.0;
        let extra_x_gap = if gaps > 0 {
            ((available_width - used_width_with_min_kerf).max(0.0)) / gaps as f64
        } else {
            0.0
        };

        let mut cursor_x = EDGE_MARGIN;
        let actual_x_gap = if gaps > 0 {
            (MIN_KERF + extra_x_gap).min(MAX_KERF)
        } else {
            0.0
        };
        let actual_y_gap = if row_index < row_heights.len().saturating_sub(1) {
            (MIN_KERF + extra_y_gap).min(MAX_KERF)
        } else {
            0.0
        };

        for (placement_index, placement) in row.into_iter().enumerate() {
            new_placements.push(Placement {
                row_no: row_index + 1,
                x_gap_after: if placement_index + 1 < row_len {
                    actual_x_gap
                } else {
                    0.0
                },
                y_gap_after: actual_y_gap,
                x: cursor_x,
                y: cursor_y,
                ..placement
            });
            cursor_x += placement.length + actual_x_gap;
        }
        cursor_y += row_heights[row_index] + actual_y_gap;
    }

    let mut placements = new_placements;
    placements.sort_by(|a, b| {
        a.row_no
            .cmp(&b.row_no)
            .then_with(|| a.x.partial_cmp(&b.x).unwrap_or(Ordering::Equal))
            .then_with(|| a.part.item_no.cmp(&b.part.item_no))
    });
    Sheet {
        sheet_no: sheet.sheet_no,
        thickness: sheet.thickness,
        placements,
        free_rects: Vec::new(),
    }
}

pub fn pack_parts_for_thickness(
    thickness: f64,
    parts: &[Part],
    board_length: f64,
    board_width: f64,
    weight_table: Option<&WeightTable>,
) -> Result<ThicknessResult> {
    let available_width = board_length - EDGE_MARGIN * 2.0;
    let available_height = board_width - EDGE_MARGIN * 2.0;
    if available_width <= 0.0 || available_height <= 0.0 {
        bail!("整板尺寸必须大于四周 10mm 留边");
    }

    for part in parts {
        if !fits_in_available_area(*part, available_width, available_height) {
            bail!(
                "板件无法放入可用区域：第 {} 行，#{}，{}x{}x{}mm，可用区域 {}x{}mm",
                part.source_row,
                part.item_no,
                fmt_number(part.length),
                fmt_number(part.width),
                fmt_number(part.thickness),
                fmt_number(available_width),
                fmt_number(available_height)
            );
        }
    }

    let mut candidates = Vec::new();
    for ordered_parts in sorted_part_strategies(parts) {
        candidates.push(pack_ordered_parts(
            thickness,
            &ordered_parts,
            available_width,
            available_height,
        )?);
    }
    let sheets = candidates
        .into_iter()
        .min_by(|left, right| {
            let left_key = (
                left.len(),
                left.last().map(|sheet| sheet.used_area()).unwrap_or(0.0),
                left.iter().map(|sheet| sheet.used_area()).sum::<f64>(),
            );
            let right_key = (
                right.len(),
                right.last().map(|sheet| sheet.used_area()).unwrap_or(0.0),
                right.iter().map(|sheet| sheet.used_area()).sum::<f64>(),
            );
            left_key.partial_cmp(&right_key).unwrap_or(Ordering::Equal)
        })
        .unwrap_or_default();

    let final_sheets = sheets
        .iter()
        .map(|sheet| repack_sheet_with_even_gaps(sheet, board_length, board_width))
        .collect::<Vec<_>>();
    let sheet_area = board_length * board_width;
    let equivalent = if final_sheets.len() == 1 {
        0.1f64.max(ceil_to_tenth(final_sheets[0].used_area() / sheet_area))
    } else {
        let full_sheets = final_sheets.len() - 1;
        let last_sheet_fraction = 0.1f64.max(ceil_to_tenth(
            final_sheets.last().unwrap().used_area() / sheet_area,
        ));
        full_sheets as f64 + last_sheet_fraction
    };

    let total_weight_kg = weight_for_thickness(thickness, weight_table)
        .map(|full_sheet_weight_kg| equivalent * full_sheet_weight_kg)
        .unwrap_or(0.0);

    Ok(ThicknessResult {
        thickness,
        sheets: final_sheets,
        sheet_equivalent: equivalent,
        integer_sheets: sheets.len(),
        total_weight_kg,
    })
}

pub fn group_by_thickness(parts: &[Part]) -> Vec<(f64, Vec<Part>)> {
    let mut grouped: HashMap<u64, (f64, Vec<Part>)> = HashMap::new();
    for part in parts {
        grouped
            .entry(part.thickness.to_bits())
            .or_insert_with(|| (part.thickness, Vec::new()))
            .1
            .push(*part);
    }
    let mut groups = grouped.into_values().collect::<Vec<_>>();
    groups.sort_by(|left, right| left.0.partial_cmp(&right.0).unwrap_or(Ordering::Equal));
    groups
}

pub fn write_output_csv(path: &Path, results: &[ThicknessResult]) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut writer = WriterBuilder::new()
        .has_headers(true)
        .from_path(path)
        .map_err(|err| anyhow::anyhow!("打开输出 CSV 失败: {err}"))?;
    writer.write_record([
        "thickness",
        "sheet_no",
        "row_no",
        "item_no",
        "source_row",
        "x_mm",
        "y_mm",
        "length_mm",
        "width_mm",
        "rotated",
        "x_gap_after_mm",
        "y_gap_after_mm",
    ])?;
    for result in results {
        for sheet in &result.sheets {
            for placement in &sheet.placements {
                writer.write_record([
                    fmt_number(result.thickness),
                    sheet.sheet_no.to_string(),
                    placement.row_no.to_string(),
                    placement.part.item_no.to_string(),
                    placement.part.source_row.to_string(),
                    format!("{:.1}", placement.x),
                    format!("{:.1}", placement.y),
                    format!("{:.1}", placement.length),
                    format!("{:.1}", placement.width),
                    if placement.rotated {
                        "yes".into()
                    } else {
                        "no".into()
                    },
                    format!("{:.1}", placement.x_gap_after),
                    format!("{:.1}", placement.y_gap_after),
                ])?;
            }
        }
    }
    writer.flush()?;
    Ok(())
}

pub fn build_layout_results(
    input_path: &Path,
    board_length: f64,
    board_width: f64,
    weight_table_path: Option<&Path>,
) -> Result<(Vec<ThicknessResult>, Option<PathBuf>)> {
    let parts = read_parts_from_path(input_path)?;
    let effective_weight_table_path = match weight_table_path {
        Some(path) => Some(path.to_path_buf()),
        None => discover_weight_table_path(),
    };
    let weight_table = match effective_weight_table_path.as_deref() {
        Some(path) => Some(load_weight_table(path)?),
        None => None,
    };
    let mut results = Vec::new();
    for (thickness, group) in group_by_thickness(&parts) {
        results.push(pack_parts_for_thickness(
            thickness,
            &group,
            board_length,
            board_width,
            weight_table.as_ref(),
        )?);
    }
    Ok((results, effective_weight_table_path))
}

pub fn generate_layout_outputs(
    input_path: &Path,
    board_length: f64,
    board_width: f64,
    weight_table_path: Option<&Path>,
    output_csv_path: Option<&Path>,
    image_output_path: Option<&Path>,
    image_width: u32,
) -> Result<(Vec<ThicknessResult>, Option<PathBuf>, PathBuf)> {
    let (results, effective_weight_table_path) =
        build_layout_results(input_path, board_length, board_width, weight_table_path)?;
    if let Some(output_csv_path) = output_csv_path {
        write_output_csv(output_csv_path, &results)?;
    }
    let final_image_output_path = image_output_path
        .map(|path| path.to_path_buf())
        .unwrap_or_else(|| default_report_png_path(input_path));
    crate::report::write_report_png(
        &final_image_output_path,
        &results,
        board_length,
        board_width,
        image_width,
    )?;
    Ok((
        results,
        effective_weight_table_path,
        final_image_output_path,
    ))
}

pub fn print_summary(results: &[ThicknessResult], board_length: f64, board_width: f64) {
    println!("板材排版结果");
    println!(
        "整板 {} x {} mm，四周留边 {} mm，刀缝 {}-{} mm",
        fmt_number(board_length),
        fmt_number(board_width),
        fmt_number(EDGE_MARGIN),
        fmt_number(MIN_KERF),
        fmt_number(MAX_KERF)
    );
    println!();

    let mut total_equivalent = 0.0;
    let mut total_integer_sheets = 0usize;
    for result in results {
        total_equivalent += result.sheet_equivalent;
        total_integer_sheets += result.integer_sheets;
        let part_count = result
            .sheets
            .iter()
            .map(|sheet| sheet.placements.len())
            .sum::<usize>();
        println!(
            "厚度 {} mm: {} 块，实际 {} 张整板，面积折算 {:.1} 张",
            fmt_number(result.thickness),
            part_count,
            result.integer_sheets,
            result.sheet_equivalent
        );
        if result.total_weight_kg > 0.0 {
            println!("  总重量: {:.1} kg", result.total_weight_kg);
        }
        for sheet in &result.sheets {
            let fraction = ceil_to_tenth(sheet.used_area() / (board_length * board_width));
            println!(
                "  整板 {}: {} 块，已用面积 {:.1} mm²，面积折算 {:.1} 张",
                sheet.sheet_no,
                sheet.placements.len(),
                sheet.used_area(),
                fraction
            );
        }
    }
    println!();
    println!("总整张数: {}", total_integer_sheets);
    println!("总面积折算: {:.1}", ceil_to_tenth(total_equivalent));
    let total_weight_kg = results
        .iter()
        .map(|result| result.total_weight_kg)
        .sum::<f64>();
    if total_weight_kg > 0.0 {
        println!("总重量: {:.1} kg", total_weight_kg);
    }
}
