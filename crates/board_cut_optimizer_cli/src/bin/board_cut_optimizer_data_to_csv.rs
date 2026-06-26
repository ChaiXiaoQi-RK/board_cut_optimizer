use anyhow::Result;
use board_cut_optimizer_core::{
    convert_board_data_to_csv, discover_weight_table_path, parse_archive_date,
    read_raw_data_from_args,
};
use clap::Parser;
use std::path::PathBuf;

const DEFAULT_ARCHIVE_ROOT: &str = r"D:\Works\电商\海洋板\海洋板订单留档";

#[derive(Debug, Parser)]
#[command(
    name = "board_cut_optimizer_data_to_csv",
    version,
    about = "将原始开料文本转成可用的 CSV 文件"
)]
struct Args {
    #[arg(long)]
    filename: String,

    #[arg(long)]
    input: Option<PathBuf>,

    #[arg(long)]
    data: Option<String>,

    #[arg(long)]
    customer: Option<String>,

    #[arg(long, default_value = DEFAULT_ARCHIVE_ROOT)]
    archive_root: PathBuf,

    #[arg(long)]
    date: Option<String>,

    #[arg(long)]
    default_thickness: Option<f64>,

    #[arg(long)]
    no_combine: bool,

    #[arg(long)]
    weight_table: Option<PathBuf>,
}

fn run() -> Result<()> {
    let args = Args::parse();
    let raw_text = read_raw_data_from_args(args.data.as_deref(), args.input.as_deref())?;
    let archive_date = parse_archive_date(args.date.as_deref())?;
    let weight_table_path = args.weight_table.or_else(discover_weight_table_path);
    let (output_path, _rows, effective_weight_table) = convert_board_data_to_csv(
        &raw_text,
        &args.filename,
        &args.archive_root,
        weight_table_path.as_deref(),
        args.customer.as_deref(),
        Some(archive_date),
        args.default_thickness,
        !args.no_combine,
    )?;

    println!("CSV 已生成: {}", output_path.display());
    if let Some(weight_table) = effective_weight_table {
        println!("重量表: {}", weight_table.display());
    }
    Ok(())
}

fn main() {
    if let Err(err) = run() {
        eprintln!("Error: {err:?}");
        std::process::exit(1);
    }
}
