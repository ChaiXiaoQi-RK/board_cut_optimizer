use crate::packing::ceil_to_tenth;
use crate::parsing::fmt_number;
use crate::types::{EDGE_MARGIN, MAX_KERF, MIN_KERF, Placement, ThicknessResult};
use ab_glyph::FontArc;
use anyhow::{Context, Result, bail};
use image::{ImageBuffer, Rgba, RgbaImage};
use imageproc::drawing::{draw_filled_rect_mut, draw_hollow_rect_mut, draw_text_mut};
use imageproc::rect::Rect;
use std::fs;
use std::path::{Path, PathBuf};

const BG: Rgba<u8> = Rgba([243, 244, 246, 255]);
const CARD_BG: Rgba<u8> = Rgba([255, 255, 255, 255]);
const CARD_BORDER: Rgba<u8> = Rgba([209, 213, 219, 255]);
const TEXT_DARK: Rgba<u8> = Rgba([17, 24, 39, 255]);
const TEXT_MID: Rgba<u8> = Rgba([75, 85, 99, 255]);
const TEXT_LIGHT: Rgba<u8> = Rgba([107, 114, 128, 255]);
const LINE: Rgba<u8> = Rgba([229, 231, 235, 255]);
const BOARD_BORDER: Rgba<u8> = Rgba([100, 116, 139, 255]);
const BOARD_BG: Rgba<u8> = Rgba([248, 250, 252, 255]);

const PALETTE: [Rgba<u8>; 7] = [
    Rgba([37, 99, 235, 230]),
    Rgba([5, 150, 105, 230]),
    Rgba([220, 38, 38, 230]),
    Rgba([124, 58, 237, 230]),
    Rgba([234, 88, 12, 230]),
    Rgba([8, 145, 178, 230]),
    Rgba([190, 18, 60, 230]),
];

#[derive(Clone)]
struct Fonts {
    regular: FontArc,
    bold: FontArc,
}

fn load_font_from_candidates(candidates: &[&str]) -> Result<FontArc> {
    for candidate in candidates {
        if let Ok(bytes) = fs::read(candidate) {
            if let Ok(font) = FontArc::try_from_vec(bytes) {
                return Ok(font);
            }
        }
    }
    bail!("未找到可用的 Windows 中文字体，请安装或检查系统字体");
}

fn load_fonts() -> Result<Fonts> {
    let regular = load_font_from_candidates(&[
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ])?;
    let bold = load_font_from_candidates(&[
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ])
    .unwrap_or_else(|_| regular.clone());
    Ok(Fonts { regular, bold })
}

pub fn default_report_png_path(input_path: &Path) -> PathBuf {
    input_path.with_extension("png")
}

fn text_width(text: &str, size: f32) -> f32 {
    text.chars()
        .map(|ch| {
            if ch.is_ascii() {
                size * 0.56
            } else {
                size * 0.98
            }
        })
        .sum()
}

fn draw_text(
    image: Option<&mut RgbaImage>,
    font: &FontArc,
    size: f32,
    x: i32,
    y: i32,
    color: Rgba<u8>,
    text: impl AsRef<str>,
) {
    if let Some(image) = image {
        draw_text_mut(image, color, x, y, size, font, text.as_ref());
    }
}

fn draw_rect(
    image: Option<&mut RgbaImage>,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    fill: Rgba<u8>,
    outline: Option<Rgba<u8>>,
) {
    if let Some(image) = image {
        let rect = Rect::at(x, y).of_size(width, height);
        draw_filled_rect_mut(image, rect, fill);
        if let Some(outline) = outline {
            draw_hollow_rect_mut(image, rect, outline);
        }
    }
}

fn measure_aggregate_parts(placements: &[Placement]) -> Vec<(f64, f64, usize, f64)> {
    let mut grouped = std::collections::HashMap::<(u64, u64), (usize, f64)>::new();
    for placement in placements {
        let key = (
            placement.part.length.to_bits(),
            placement.part.width.to_bits(),
        );
        let entry = grouped.entry(key).or_insert((0, 0.0));
        entry.0 += 1;
        entry.1 += placement.part.area;
    }
    let mut rows = grouped
        .into_iter()
        .map(|((length_bits, width_bits), (quantity, area))| {
            (
                f64::from_bits(length_bits),
                f64::from_bits(width_bits),
                quantity,
                area,
            )
        })
        .collect::<Vec<_>>();
    rows.sort_by(|left, right| {
        right
            .0
            .partial_cmp(&left.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                right
                    .1
                    .partial_cmp(&left.1)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
    });
    rows
}

fn compute_diagram_geometry(
    board_length: f64,
    board_width: f64,
    max_width: f32,
    max_height: f32,
) -> (f32, f32, f32) {
    let scale = (max_width / board_length as f32).min(max_height / board_width as f32);
    (
        board_length as f32 * scale,
        board_width as f32 * scale,
        scale,
    )
}

fn split_placements_for_side_columns(placements: &[Placement]) -> (Vec<Placement>, Vec<Placement>) {
    let midpoint = (placements.len() + 1) / 2;
    (
        placements[..midpoint].to_vec(),
        placements[midpoint..].to_vec(),
    )
}

fn draw_board_part_label(
    image: Option<&mut RgbaImage>,
    fonts: &Fonts,
    part: &Placement,
    x: f32,
    y: f32,
    width: f32,
    height: f32,
) {
    let label = format!("#{}", part.part.item_no);
    let font_size = if width >= 80.0 && height >= 46.0 {
        21.0
    } else if width >= 44.0 && height >= 24.0 {
        18.0
    } else {
        0.0
    };
    if font_size <= 0.0 {
        return;
    }
    let text_width = text_width(&label, font_size);
    let tx = (x + width / 2.0 - text_width / 2.0).round() as i32;
    let ty = (y + height / 2.0 - font_size * 0.75).round() as i32;
    draw_text(
        image,
        &fonts.bold,
        font_size,
        tx,
        ty,
        Rgba([255, 255, 255, 255]),
        label,
    );
}

fn draw_sheet_card(
    mut image: Option<&mut RgbaImage>,
    x: f32,
    y: f32,
    result: &ThicknessResult,
    board_length: f64,
    board_width: f64,
    card_width: f32,
    fonts: &Fonts,
) -> f32 {
    let mut local_y = y + 34.0;
    let part_count = result
        .sheets
        .iter()
        .map(|sheet| sheet.placements.len())
        .sum::<usize>();
    let title = format!("厚度 {} mm", fmt_number(result.thickness));
    let subtitle = format!(
        "{}块，实际 {} 张整板，面积折算 {:.1} 张",
        part_count, result.integer_sheets, result.sheet_equivalent
    );

    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        34.0,
        (x + 28.0) as i32,
        local_y as i32,
        TEXT_DARK,
        title,
    );
    local_y += 44.0;
    draw_text(
        image.as_deref_mut(),
        &fonts.regular,
        24.0,
        (x + 28.0) as i32,
        local_y as i32,
        TEXT_LIGHT,
        subtitle,
    );
    local_y += 54.0;
    if result.total_weight_kg > 0.0 {
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            24.0,
            (x + 28.0) as i32,
            local_y as i32,
            TEXT_DARK,
            format!("总重量 {:.1} kg", result.total_weight_kg),
        );
        local_y += 38.0;
    }

    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        28.0,
        (x + 28.0) as i32,
        local_y as i32,
        TEXT_DARK,
        "零切清单汇总",
    );
    local_y += 42.0;
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        22.0,
        (x + 28.0) as i32,
        local_y as i32,
        TEXT_MID,
        "尺寸(mm)",
    );
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        22.0,
        (x + 280.0) as i32,
        local_y as i32,
        TEXT_MID,
        "数量",
    );
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        22.0,
        (x + 430.0) as i32,
        local_y as i32,
        TEXT_MID,
        "面积(mm²)",
    );
    local_y += 34.0;
    draw_rect(
        image.as_deref_mut(),
        (x + 24.0) as i32,
        local_y as i32,
        (card_width - 48.0) as u32,
        1,
        LINE,
        None,
    );
    local_y += 18.0;

    let placements = result
        .sheets
        .iter()
        .flat_map(|sheet| sheet.placements.iter().copied())
        .collect::<Vec<_>>();
    for (length, width, quantity, area) in measure_aggregate_parts(&placements) {
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            22.0,
            (x + 28.0) as i32,
            local_y as i32,
            TEXT_DARK,
            format!("{} x {}", fmt_number(length), fmt_number(width)),
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            22.0,
            (x + 280.0) as i32,
            local_y as i32,
            TEXT_DARK,
            quantity.to_string(),
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            22.0,
            (x + 430.0) as i32,
            local_y as i32,
            TEXT_DARK,
            format!("{area:.1}"),
        );
        local_y += 36.0;
    }

    let side_panel_width = 150.0;
    let side_gap = 14.0;
    let max_diagram_width = card_width - 56.0 - side_panel_width * 2.0 - side_gap * 2.0;
    let max_diagram_height = 680.0;
    for sheet in &result.sheets {
        local_y += 22.0;
        let fraction = ceil_to_tenth(sheet.used_area() / (board_length * board_width));
        draw_text(
            image.as_deref_mut(),
            &fonts.bold,
            28.0,
            (x + 28.0) as i32,
            local_y as i32,
            TEXT_DARK,
            format!(
                "整板 {}：{}块，面积折算 {:.1}张",
                sheet.sheet_no,
                sheet.placements.len(),
                fraction
            ),
        );
        local_y += 48.0;

        let (diagram_width, diagram_height, scale) = compute_diagram_geometry(
            board_length,
            board_width,
            max_diagram_width,
            max_diagram_height,
        );
        let diagram_x =
            x + 28.0 + side_panel_width + side_gap + (max_diagram_width - diagram_width) / 2.0;
        let diagram_y = local_y;
        let left_x = x + 28.0;
        let right_x = diagram_x + diagram_width + side_gap;
        let (left_items, right_items) = split_placements_for_side_columns(&sheet.placements);
        let row_height = 24.0;
        let header_height = 24.0;
        let list_height =
            header_height + left_items.len().max(right_items.len()) as f32 * row_height;
        let section_height = diagram_height.max(list_height);

        draw_rect(
            image.as_deref_mut(),
            diagram_x.round() as i32,
            diagram_y.round() as i32,
            diagram_width.round() as u32,
            diagram_height.round() as u32,
            BOARD_BG,
            Some(BOARD_BORDER),
        );
        draw_rect(
            image.as_deref_mut(),
            (diagram_x + EDGE_MARGIN as f32 * scale).round() as i32,
            (diagram_y + EDGE_MARGIN as f32 * scale).round() as i32,
            ((board_length as f32 - EDGE_MARGIN as f32 * 2.0) * scale).round() as u32,
            ((board_width as f32 - EDGE_MARGIN as f32 * 2.0) * scale).round() as u32,
            Rgba([0, 0, 0, 0]),
            Some(BOARD_BORDER),
        );

        draw_text(
            image.as_deref_mut(),
            &fonts.bold,
            16.0,
            left_x as i32,
            diagram_y as i32,
            TEXT_MID,
            "编号",
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.bold,
            16.0,
            (left_x + 52.0) as i32,
            diagram_y as i32,
            TEXT_MID,
            "原尺寸",
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.bold,
            16.0,
            right_x as i32,
            diagram_y as i32,
            TEXT_MID,
            "编号",
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.bold,
            16.0,
            (right_x + 52.0) as i32,
            diagram_y as i32,
            TEXT_MID,
            "原尺寸",
        );

        let mut list_y = diagram_y + header_height;
        for placement in &left_items {
            draw_text(
                image.as_deref_mut(),
                &fonts.regular,
                15.0,
                left_x as i32,
                list_y as i32,
                TEXT_DARK,
                format!("#{}", placement.part.item_no),
            );
            draw_text(
                image.as_deref_mut(),
                &fonts.regular,
                15.0,
                (left_x + 52.0) as i32,
                list_y as i32,
                TEXT_DARK,
                format!(
                    "{}x{}",
                    fmt_number(placement.part.length),
                    fmt_number(placement.part.width)
                ),
            );
            list_y += row_height;
        }
        list_y = diagram_y + header_height;
        for placement in &right_items {
            draw_text(
                image.as_deref_mut(),
                &fonts.regular,
                15.0,
                right_x as i32,
                list_y as i32,
                TEXT_DARK,
                format!("#{}", placement.part.item_no),
            );
            draw_text(
                image.as_deref_mut(),
                &fonts.regular,
                15.0,
                (right_x + 52.0) as i32,
                list_y as i32,
                TEXT_DARK,
                format!(
                    "{}x{}",
                    fmt_number(placement.part.length),
                    fmt_number(placement.part.width)
                ),
            );
            list_y += row_height;
        }

        for placement in &sheet.placements {
            let px = diagram_x + placement.x as f32 * scale;
            let py = diagram_y + placement.y as f32 * scale;
            let pw = placement.length as f32 * scale;
            let ph = placement.width as f32 * scale;
            let color = PALETTE[(placement.part.item_no - 1) % PALETTE.len()];
            draw_rect(
                image.as_deref_mut(),
                px.round() as i32,
                py.round() as i32,
                pw.max(1.0).round() as u32,
                ph.max(1.0).round() as u32,
                color,
                Some(Rgba([255, 255, 255, 255])),
            );
            draw_board_part_label(image.as_deref_mut(), fonts, placement, px, py, pw, ph);
        }

        local_y += section_height + 34.0;
    }

    local_y + 28.0
}

fn render_report(
    mut image: Option<&mut RgbaImage>,
    results: &[ThicknessResult],
    board_length: f64,
    board_width: f64,
    image_width: u32,
    fonts: &Fonts,
) -> u32 {
    let card_width = image_width as f32 - 48.0;
    let mut y = 44.0;
    let total_integer = results
        .iter()
        .map(|result| result.integer_sheets)
        .sum::<usize>();
    let total_equivalent = ceil_to_tenth(
        results
            .iter()
            .map(|result| result.sheet_equivalent)
            .sum::<f64>(),
    );
    let total_weight_kg = results
        .iter()
        .map(|result| result.total_weight_kg)
        .sum::<f64>();

    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        44.0,
        34,
        y as i32,
        TEXT_DARK,
        "板材排板结果",
    );
    y += 58.0;
    draw_text(
        image.as_deref_mut(),
        &fonts.regular,
        24.0,
        36,
        y as i32,
        TEXT_MID,
        format!(
            "整板 {} x {} mm，四周留边 {} mm，刀缝 {}-{} mm",
            fmt_number(board_length),
            fmt_number(board_width),
            fmt_number(EDGE_MARGIN),
            fmt_number(MIN_KERF),
            fmt_number(MAX_KERF)
        ),
    );
    y += 38.0;
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        26.0,
        36,
        y as i32,
        TEXT_DARK,
        format!(
            "实际整张：{}张    面积折算：{total_equivalent:.1}张",
            total_integer
        ),
    );
    y += 54.0;
    if total_weight_kg > 0.0 {
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            24.0,
            36,
            y as i32,
            TEXT_DARK,
            format!("总重量：{total_weight_kg:.1} kg"),
        );
        y += 40.0;
    }

    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        28.0,
        36,
        y as i32,
        TEXT_DARK,
        "板材总明细",
    );
    y += 34.0;
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        22.0,
        36,
        y as i32,
        TEXT_MID,
        "厚度",
    );
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        22.0,
        220,
        y as i32,
        TEXT_MID,
        "用量(张)",
    );
    draw_text(
        image.as_deref_mut(),
        &fonts.bold,
        22.0,
        430,
        y as i32,
        TEXT_MID,
        "重量(kg)",
    );
    y += 28.0;
    draw_rect(
        image.as_deref_mut(),
        32,
        y as i32,
        image_width.saturating_sub(64),
        1,
        LINE,
        None,
    );
    y += 18.0;

    for result in results {
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            22.0,
            36,
            y as i32,
            TEXT_DARK,
            format!("{} mm", fmt_number(result.thickness)),
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            22.0,
            220,
            y as i32,
            TEXT_DARK,
            format!("{:.1}", result.sheet_equivalent),
        );
        draw_text(
            image.as_deref_mut(),
            &fonts.regular,
            22.0,
            430,
            y as i32,
            TEXT_DARK,
            if result.total_weight_kg > 0.0 {
                format!("{:.1}", result.total_weight_kg)
            } else {
                "-".to_string()
            },
        );
        y += 32.0;
    }
    y += 14.0;

    for result in results {
        y += 18.0;
        let card_height = draw_sheet_card(
            None,
            24.0,
            y,
            result,
            board_length,
            board_width,
            card_width,
            fonts,
        );
        draw_rect(
            image.as_deref_mut(),
            24,
            y as i32,
            card_width.round() as u32,
            card_height.round() as u32,
            CARD_BG,
            Some(CARD_BORDER),
        );
        draw_sheet_card(
            image.as_deref_mut(),
            24.0,
            y,
            result,
            board_length,
            board_width,
            card_width,
            fonts,
        );
        y += card_height + 18.0;
    }

    y.ceil() as u32 + 34
}

pub fn write_report_png(
    path: &Path,
    results: &[ThicknessResult],
    board_length: f64,
    board_width: f64,
    image_width: u32,
) -> Result<()> {
    if image_width < 760 {
        bail!("图片宽度至少需要 760 像素");
    }

    let fonts = load_fonts()?;
    let height = render_report(
        None,
        results,
        board_length,
        board_width,
        image_width,
        &fonts,
    );
    let mut image: RgbaImage = ImageBuffer::from_pixel(image_width, height, BG);
    render_report(
        Some(&mut image),
        results,
        board_length,
        board_width,
        image_width,
        &fonts,
    );
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)
            .with_context(|| format!("创建图片目录失败: {}", parent.display()))?;
    }
    image
        .save(path)
        .with_context(|| format!("保存 PNG 失败: {}", path.display()))?;
    Ok(())
}
