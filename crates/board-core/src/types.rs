use serde::{Deserialize, Serialize};

pub const EDGE_MARGIN: f64 = 10.0;
pub const MIN_KERF: f64 = 4.0;
pub const MAX_KERF: f64 = 10.0;
pub const ROUND_EPSILON: f64 = 1e-9;

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct BoardRow {
    pub length: f64,
    pub width: f64,
    pub thickness: f64,
    pub quantity: usize,
    pub source_line: usize,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Part {
    pub source_row: usize,
    pub item_no: usize,
    pub length: f64,
    pub width: f64,
    pub thickness: f64,
    pub area: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FreeRect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Placement {
    pub part: Part,
    pub sheet_no: usize,
    pub x: f64,
    pub y: f64,
    pub length: f64,
    pub width: f64,
    pub rotated: bool,
    pub row_no: usize,
    pub x_gap_after: f64,
    pub y_gap_after: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Sheet {
    pub sheet_no: usize,
    pub thickness: f64,
    pub placements: Vec<Placement>,
    pub free_rects: Vec<FreeRect>,
}

impl Sheet {
    pub fn used_area(&self) -> f64 {
        self.placements
            .iter()
            .map(|placement| placement.length * placement.width)
            .sum()
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct ThicknessResult {
    pub thickness: f64,
    pub sheets: Vec<Sheet>,
    pub sheet_equivalent: f64,
    pub integer_sheets: usize,
    pub total_weight_kg: f64,
}

pub type WeightTable = Vec<(f64, f64)>;
