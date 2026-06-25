use anyhow::Result;
use board_core::{
    discover_weight_table_path, generate_layout_outputs, parse_archive_date, print_summary,
};
use clap::Parser;
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(
    name = "board_cut_optimizer",
    version,
    about = "板材排版计算与报表生成"
)]
struct Args {
    #[arg(long)]
    board_length: f64,

    #[arg(long)]
    board_width: f64,

    #[arg(long)]
    input: PathBuf,

    #[arg(long)]
    output: Option<PathBuf>,

    #[arg(long)]
    image_output: Option<PathBuf>,

    #[arg(long, default_value_t = 960)]
    image_width: u32,

    #[arg(long)]
    weight_table: Option<PathBuf>,

    #[arg(long)]
    date: Option<String>,
}

fn run() -> Result<()> {
    let args = Args::parse();
    if args.board_length <= 20.0 || args.board_width <= 20.0 {
        anyhow::bail!("整板长宽必须大于 20mm");
    }
    let _date = parse_archive_date(args.date.as_deref())?;
    let weight_table_path = args.weight_table.or_else(discover_weight_table_path);
    let (results, effective_weight_table, png_path) = generate_layout_outputs(
        &args.input,
        args.board_length,
        args.board_width,
        weight_table_path.as_deref(),
        args.output.as_deref(),
        args.image_output.as_deref(),
        args.image_width,
    )?;

    print_summary(&results, args.board_length, args.board_width);
    if let Some(weight_table) = effective_weight_table {
        println!("重量表: {}", weight_table.display());
    }
    if let Some(output) = args.output {
        println!("详细 CSV: {}", output.display());
    }
    println!("PNG 报表: {}", png_path.display());
    Ok(())
}

fn main() {
    if let Err(err) = run() {
        eprintln!("Error: {err:?}");
        std::process::exit(1);
    }
}
