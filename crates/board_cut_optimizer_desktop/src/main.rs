#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use anyhow::{Context, Result};
use arboard::{Clipboard, ImageData};
use board_cut_optimizer_core::{
    convert_board_data_to_csv, discover_weight_table_path, generate_layout_outputs,
    parse_archive_date,
};
use iced::widget::image::Handle;
use iced::widget::text_editor::{Action, Content};
use iced::widget::{
    button, column, container, image as iced_image, row, scrollable, text, text_editor, text_input,
};
use iced::{Alignment, Application, Command, Element, Font, Length, Settings, Theme, executor};
use image as image_crate;
use serde::{Deserialize, Serialize};
use std::borrow::Cow;
use std::fs;
use std::path::{Path, PathBuf};

const APP_NAME: &str = "board_cut_optimizer";
const CHINESE_NAME: &str = "板优排";
const VERSION: &str = "V1.2.1";
const AUTHOR: &str = "有钱任性买辣条";
const DEFAULT_BOARD_LENGTH: &str = "1220";
const DEFAULT_BOARD_WIDTH: &str = "2440";
const DEFAULT_ARCHIVE_ROOT: &str = r"D:\Works\电商\海洋板\海洋板订单留档";

fn load_cjk_fonts() -> Vec<Cow<'static, [u8]>> {
    const CANDIDATES: &[&str] = &[
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\NotoSansSC-VF.ttf",
    ];

    CANDIDATES
        .iter()
        .filter_map(|path| fs::read(path).ok().map(Cow::Owned))
        .collect()
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct SettingsData {
    archive_root: Option<PathBuf>,
    weight_table_path: Option<PathBuf>,
    last_board_length: String,
    last_board_width: String,
}

impl SettingsData {
    fn load() -> Self {
        let path = settings_path();
        fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_else(|| SettingsData {
                archive_root: Some(PathBuf::from(DEFAULT_ARCHIVE_ROOT)),
                ..Default::default()
            })
    }

    fn save(&self) -> Result<()> {
        let path = settings_path();
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("创建设置目录失败: {}", parent.display()))?;
        }
        let json = serde_json::to_string_pretty(self)?;
        fs::write(&path, json).with_context(|| format!("保存设置失败: {}", path.display()))?;
        Ok(())
    }
}

fn settings_path() -> PathBuf {
    let base = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("board_cut_optimizer").join("settings.json")
}

#[derive(Debug, Clone)]
struct ThicknessSummaryRow {
    thickness: String,
    sheets: String,
    weight: String,
}

#[derive(Debug, Clone)]
struct GenerationResult {
    csv_path: PathBuf,
    png_path: PathBuf,
    actual_sheets: usize,
    area_equivalent: f64,
    total_weight: f64,
    rows: Vec<ThicknessSummaryRow>,
}

#[derive(Debug, Clone)]
struct GenerateRequest {
    file_name: String,
    board_length: f64,
    board_width: f64,
    raw_data: String,
    archive_root: PathBuf,
    weight_table_path: Option<PathBuf>,
}

#[derive(Debug, Clone)]
enum Message {
    FileNameChanged(String),
    BoardLengthChanged(String),
    BoardWidthChanged(String),
    RawDataAction(Action),
    Generate,
    Generated(Result<GenerationResult, String>),
    ClearRaw,
    OpenSettings,
    OpenAbout,
    ClosePopup,
    ChooseArchiveRoot,
    ChooseWeightTable,
    OpenPreview,
    CopyPreview,
}

struct BoardGuiApp {
    settings: SettingsData,
    file_name: String,
    board_length: String,
    board_width: String,
    raw_content: Content,
    status_text: String,
    output_text: String,
    actual_sheets: usize,
    area_equivalent: f64,
    total_weight: f64,
    thickness_rows: Vec<ThicknessSummaryRow>,
    preview_path: Option<PathBuf>,
    preview_handle: Option<Handle>,
    settings_open: bool,
    about_open: bool,
    generating: bool,
}

impl Default for BoardGuiApp {
    fn default() -> Self {
        let settings = SettingsData::load();
        let last_length = if settings.last_board_length.is_empty() {
            DEFAULT_BOARD_LENGTH.to_string()
        } else {
            settings.last_board_length.clone()
        };
        let last_width = if settings.last_board_width.is_empty() {
            DEFAULT_BOARD_WIDTH.to_string()
        } else {
            settings.last_board_width.clone()
        };

        Self {
            settings,
            file_name: String::new(),
            board_length: last_length,
            board_width: last_width,
            raw_content: Content::new(),
            status_text: "等待设置".to_string(),
            output_text: "未生成文件".to_string(),
            actual_sheets: 0,
            area_equivalent: 0.0,
            total_weight: 0.0,
            thickness_rows: Vec::new(),
            preview_path: None,
            preview_handle: None,
            settings_open: false,
            about_open: false,
            generating: false,
        }
    }
}

impl BoardGuiApp {
    fn ready_to_generate(&self) -> bool {
        let weight_table_ready = self
            .settings
            .weight_table_path
            .as_ref()
            .map(|path| path.is_file())
            .unwrap_or_else(|| discover_weight_table_path().is_some());
        let raw_data = self.raw_data_text();
        self.parse_board_length().is_ok()
            && self.parse_board_width().is_ok()
            && !self.file_name.trim().is_empty()
            && !raw_data.trim().is_empty()
            && self
                .settings
                .archive_root
                .as_ref()
                .map_or(false, |path| path.is_dir())
            && weight_table_ready
            && !self.generating
    }

    fn parse_board_length(&self) -> Result<f64> {
        self.board_length
            .trim()
            .parse::<f64>()
            .with_context(|| "整板长度必须是数字")
    }

    fn parse_board_width(&self) -> Result<f64> {
        self.board_width
            .trim()
            .parse::<f64>()
            .with_context(|| "整板宽度必须是数字")
    }

    fn preview_image(&self) -> Option<Element<'_, Message>> {
        let handle = self.preview_handle.clone()?;
        let image_widget = iced_image::Image::new(handle)
            .width(Length::Fill)
            .height(Length::Shrink);
        Some(image_widget.into())
    }

    fn raw_data_text(&self) -> String {
        self.raw_content.text()
    }

    fn save_settings(&self) {
        let _ = self.settings.save();
    }

    fn set_preview_path(&mut self, path: PathBuf) {
        self.preview_handle = Some(Handle::from_path(&path));
        self.preview_path = Some(path);
    }

    fn open_preview_in_viewer(path: &Path) -> Result<()> {
        std::process::Command::new("cmd")
            .args(["/C", "start", "", &path.display().to_string()])
            .spawn()
            .context("打开预览失败")?;
        Ok(())
    }

    fn copy_preview_to_clipboard(path: &Path) -> Result<()> {
        let image = image_crate::open(path)
            .with_context(|| format!("读取图片失败: {}", path.display()))?
            .to_rgba8();
        let (width, height) = image.dimensions();
        let mut clipboard = Clipboard::new().context("初始化剪贴板失败")?;
        clipboard
            .set_image(ImageData {
                width: width as usize,
                height: height as usize,
                bytes: Cow::Owned(image.into_raw()),
            })
            .context("复制图片到剪贴板失败")?;
        Ok(())
    }

    fn status_ready_text(&self) -> String {
        if self.generating {
            "正在生成".to_string()
        } else if self.preview_path.is_some() {
            "已完成".to_string()
        } else if self
            .settings
            .archive_root
            .as_ref()
            .map_or(false, |path| path.is_dir())
            && self
                .settings
                .weight_table_path
                .as_ref()
                .map_or(false, |path| path.is_file())
        {
            "待生成".to_string()
        } else {
            "等待设置".to_string()
        }
    }

    fn open_settings_dialog(&mut self) {
        self.settings_open = true;
        self.about_open = false;
    }

    fn open_about_dialog(&mut self) {
        self.about_open = true;
        self.settings_open = false;
    }

    fn dialog_header(title: &str) -> Element<'_, Message> {
        container(
            column![
                text(title).size(28),
                text(format!("{}  {}", CHINESE_NAME, VERSION)).size(18),
            ]
            .spacing(4),
        )
        .width(Length::Fill)
        .padding(16)
        .into()
    }

    fn metric_card(title: &str, value: String) -> Element<'_, Message> {
        container(column![text(title).size(16), text(value).size(28)].spacing(8))
            .padding(16)
            .width(Length::Fill)
            .into()
    }

    fn build_main_view(&self) -> Element<'_, Message> {
        let top_menu = container(
            row![
                button(text("设置")).on_press(Message::OpenSettings),
                button(text("其它")).on_press(Message::OpenAbout),
                button(text("清空")).on_press(Message::ClearRaw),
            ]
            .spacing(10)
            .align_items(Alignment::Center),
        )
        .padding([0, 0, 8, 0])
        .width(Length::Fill);

        let summary_cards = row![
            Self::metric_card("实际整张", format!("{} 张", self.actual_sheets)),
            Self::metric_card("面积折算", format!("{:.1} 张", self.area_equivalent)),
            Self::metric_card("总重量", format!("{:.1} kg", self.total_weight)),
            Self::metric_card("输出状态", self.status_ready_text()),
        ]
        .spacing(16);

        let settings_block = column![
            text("订单参数").size(22),
            text(""),
            text("文件名").size(16),
            text_input("文件名", &self.file_name)
                .on_input(Message::FileNameChanged)
                .padding(10)
                .width(Length::Fill),
            row![
                column![
                    text("整板长度 (mm)").size(16),
                    text_input("1220", &self.board_length)
                        .on_input(Message::BoardLengthChanged)
                        .padding(10)
                        .width(Length::Fill),
                ]
                .spacing(8)
                .width(Length::Fill),
                column![
                    text("整板宽度 (mm)").size(16),
                    text_input("2440", &self.board_width)
                        .on_input(Message::BoardWidthChanged)
                        .padding(10)
                        .width(Length::Fill),
                ]
                .spacing(8)
                .width(Length::Fill),
            ]
            .spacing(12),
            column![
                text("当前配置").size(22),
                text(format!(
                    "留档根目录: {}",
                    self.settings
                        .archive_root
                        .as_ref()
                        .map(|path| path.display().to_string())
                        .unwrap_or_else(|| "未设置".to_string())
                )),
                text(format!(
                    "重量表: {}",
                    self.settings
                        .weight_table_path
                        .as_ref()
                        .map(|path| path.display().to_string())
                        .unwrap_or_else(|| "未设置".to_string())
                )),
                button(text("留档根目录")).on_press(Message::ChooseArchiveRoot),
                button(text("重量表")).on_press(Message::ChooseWeightTable),
            ]
            .spacing(10),
            {
                let label = if self.generating {
                    "生成中"
                } else {
                    "生成 CSV 和 PNG"
                };
                if self.ready_to_generate() {
                    button(text(label)).on_press(Message::Generate)
                } else {
                    button(text(label))
                }
            },
            button(text("清空原始数据")).on_press(Message::ClearRaw),
        ]
        .spacing(14)
        .width(Length::Fill);

        let raw_editor = column![
            text("原始数据").size(22),
            text_editor(&self.raw_content)
                .on_action(Message::RawDataAction)
                .padding(12)
                .height(Length::Fill),
        ]
        .spacing(12)
        .width(Length::Fill);

        let result_summary = column![
            text("结果摘要").size(22),
            text(&self.output_text),
            text(format!(
                "CSV: {}",
                self.preview_path
                    .as_ref()
                    .map(|path| path.with_extension("csv").display().to_string())
                    .unwrap_or_else(|| "-".to_string())
            )),
            text(format!(
                "PNG: {}",
                self.preview_path
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "-".to_string())
            )),
            scrollable(
                column![
                    text("板材总明细").size(18),
                    row![
                        text("厚度").width(Length::FillPortion(2)),
                        text("用量(张)").width(Length::FillPortion(1)),
                        text("重量(kg)").width(Length::FillPortion(1)),
                    ]
                    .spacing(12),
                    column(self.thickness_rows.iter().map(|row| {
                        row![
                            text(&row.thickness).width(Length::FillPortion(2)),
                            text(&row.sheets).width(Length::FillPortion(1)),
                            text(&row.weight).width(Length::FillPortion(1)),
                        ]
                        .spacing(12)
                        .into()
                    }))
                    .spacing(8),
                ]
                .spacing(10)
            )
            .height(Length::FillPortion(1)),
        ]
        .spacing(12)
        .width(Length::Fill);

        let preview_panel = column![
            text("排板图预览").size(22),
            {
                let preview_widget: Element<'_, Message> = if let Some(image) = self.preview_image()
                {
                    button(image).on_press(Message::OpenPreview).into()
                } else {
                    container(text("")).height(Length::Fixed(400.0)).into()
                };
                preview_widget
            },
            {
                let open_button = if self.preview_path.is_some() {
                    button(text("打开")).on_press(Message::OpenPreview)
                } else {
                    button(text("打开"))
                };
                let copy_button = if self.preview_path.is_some() {
                    button(text("复制")).on_press(Message::CopyPreview)
                } else {
                    button(text("复制"))
                };
                row![open_button, copy_button].spacing(10)
            },
        ]
        .spacing(12)
        .width(Length::Fill);

        let content = column![
            top_menu,
            summary_cards,
            row![
                container(settings_block)
                    .padding(16)
                    .width(Length::FillPortion(2)),
                container(raw_editor)
                    .padding(16)
                    .width(Length::FillPortion(3)),
                container(column![result_summary, preview_panel].spacing(16))
                    .padding(16)
                    .width(Length::FillPortion(3)),
            ]
            .spacing(16)
            .height(Length::Fill),
            text(&self.status_text).size(16),
        ]
        .spacing(16)
        .padding(16);

        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .into()
    }

    fn build_settings_view(&self) -> Element<'_, Message> {
        let content = column![
            Self::dialog_header("设置"),
            text(format!(
                "留档根目录: {}",
                self.settings
                    .archive_root
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "未设置".to_string())
            )),
            text(format!(
                "重量表: {}",
                self.settings
                    .weight_table_path
                    .as_ref()
                    .map(|path| path.display().to_string())
                    .unwrap_or_else(|| "未设置".to_string())
            )),
            button(text("留档根目录")).on_press(Message::ChooseArchiveRoot),
            button(text("重量表")).on_press(Message::ChooseWeightTable),
            button(text("关闭")).on_press(Message::ClosePopup),
        ]
        .spacing(12)
        .width(Length::Fill);

        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x()
            .center_y()
            .into()
    }

    fn build_about_view(&self) -> Element<'_, Message> {
        let content = column![
            Self::dialog_header("关于"),
            text(CHINESE_NAME).size(30),
            text(APP_NAME).size(20),
            text(VERSION).size(20),
            text(format!("作者: {AUTHOR}")).size(18),
            text("板材排版、重量统计、CSV 与 PNG 生成").size(18),
            button(text("关闭")).on_press(Message::ClosePopup),
        ]
        .spacing(12)
        .width(Length::Fill);

        container(content)
            .width(Length::Fill)
            .height(Length::Fill)
            .center_x()
            .center_y()
            .into()
    }

    async fn generate(job: GenerateRequest) -> Result<GenerationResult, String> {
        let archive_date = parse_archive_date(None).map_err(|err| err.to_string())?;
        let weight_table_path = job.weight_table_path.or_else(discover_weight_table_path);
        let weight_table_path_ref = weight_table_path.as_deref();
        let (csv_path, _rows, _effective_weight_table) = convert_board_data_to_csv(
            &job.raw_data,
            &job.file_name,
            &job.archive_root,
            weight_table_path_ref,
            None,
            Some(archive_date),
            None,
            true,
        )
        .map_err(|err| err.to_string())?;

        let (results, _weight_table, png_path) = generate_layout_outputs(
            &csv_path,
            job.board_length,
            job.board_width,
            weight_table_path_ref,
            None,
            None,
            960,
        )
        .map_err(|err| err.to_string())?;

        let actual_sheets = results
            .iter()
            .map(|result| result.integer_sheets)
            .sum::<usize>();
        let area_equivalent = results
            .iter()
            .map(|result| result.sheet_equivalent)
            .sum::<f64>();
        let total_weight = results
            .iter()
            .map(|result| result.total_weight_kg)
            .sum::<f64>();
        let rows = results
            .iter()
            .map(|result| ThicknessSummaryRow {
                thickness: format!(
                    "{} mm",
                    board_cut_optimizer_core::fmt_number(result.thickness)
                ),
                sheets: format!("{:.1}", result.sheet_equivalent),
                weight: if result.total_weight_kg > 0.0 {
                    format!("{:.1}", result.total_weight_kg)
                } else {
                    "-".to_string()
                },
            })
            .collect::<Vec<_>>();

        Ok(GenerationResult {
            csv_path,
            png_path,
            actual_sheets,
            area_equivalent,
            total_weight,
            rows,
        })
    }
}

impl Application for BoardGuiApp {
    type Executor = executor::Default;
    type Message = Message;
    type Theme = Theme;
    type Flags = ();

    fn new(_flags: ()) -> (Self, Command<Self::Message>) {
        (BoardGuiApp::default(), Command::none())
    }

    fn title(&self) -> String {
        format!("{} {} {} {}", CHINESE_NAME, APP_NAME, VERSION, AUTHOR)
    }

    fn theme(&self) -> Self::Theme {
        Theme::Light
    }

    fn update(&mut self, message: Message) -> Command<Self::Message> {
        match message {
            Message::FileNameChanged(value) => {
                self.file_name = value;
            }
            Message::BoardLengthChanged(value) => {
                self.board_length = value;
                self.settings.last_board_length = self.board_length.clone();
                self.save_settings();
                self.status_text = self.status_ready_text();
            }
            Message::BoardWidthChanged(value) => {
                self.board_width = value;
                self.settings.last_board_width = self.board_width.clone();
                self.save_settings();
                self.status_text = self.status_ready_text();
            }
            Message::RawDataAction(action) => {
                self.raw_content.perform(action);
            }
            Message::ClearRaw => {
                self.raw_content = Content::new();
                self.output_text = "未生成文件".to_string();
                self.status_text = self.status_ready_text();
                self.preview_path = None;
                self.preview_handle = None;
                self.thickness_rows.clear();
                self.actual_sheets = 0;
                self.area_equivalent = 0.0;
                self.total_weight = 0.0;
            }
            Message::OpenSettings => {
                self.open_settings_dialog();
            }
            Message::OpenAbout => {
                self.open_about_dialog();
            }
            Message::ClosePopup => {
                self.settings_open = false;
                self.about_open = false;
            }
            Message::ChooseArchiveRoot => {
                if let Some(folder) = rfd::FileDialog::new().pick_folder() {
                    self.settings.archive_root = Some(folder);
                    self.save_settings();
                    self.status_text = self.status_ready_text();
                }
            }
            Message::ChooseWeightTable => {
                if let Some(file) = rfd::FileDialog::new()
                    .add_filter("CSV", &["csv"])
                    .pick_file()
                {
                    self.settings.weight_table_path = Some(file);
                    self.save_settings();
                    self.status_text = self.status_ready_text();
                }
            }
            Message::Generate => {
                let archive_root = self
                    .settings
                    .archive_root
                    .clone()
                    .unwrap_or_else(|| PathBuf::from(DEFAULT_ARCHIVE_ROOT));
                let weight_table_path = self.settings.weight_table_path.clone();
                let job = GenerateRequest {
                    file_name: self.file_name.clone(),
                    board_length: self.parse_board_length().unwrap_or(0.0),
                    board_width: self.parse_board_width().unwrap_or(0.0),
                    raw_data: self.raw_data_text(),
                    archive_root,
                    weight_table_path,
                };
                self.generating = true;
                self.status_text = "正在生成".to_string();
                return Command::perform(Self::generate(job), Message::Generated);
            }
            Message::Generated(result) => {
                self.generating = false;
                match result {
                    Ok(output) => {
                        self.output_text = format!(
                            "CSV: {}\nPNG: {}",
                            output.csv_path.display(),
                            output.png_path.display()
                        );
                        self.actual_sheets = output.actual_sheets;
                        self.area_equivalent = output.area_equivalent;
                        self.total_weight = output.total_weight;
                        self.thickness_rows = output.rows;
                        self.status_text = format!(
                            "已生成: {} | {:.1} 张 | {:.1} kg",
                            output.png_path.display(),
                            output.area_equivalent,
                            output.total_weight
                        );
                        self.set_preview_path(output.png_path);
                    }
                    Err(err) => {
                        self.status_text = format!("生成失败: {err}");
                        self.output_text = err.clone();
                        let _ = rfd::MessageDialog::new()
                            .set_title("生成失败")
                            .set_description(&err)
                            .set_buttons(rfd::MessageButtons::Ok)
                            .show();
                    }
                }
            }
            Message::OpenPreview => {
                if let Some(path) = &self.preview_path {
                    let _ = Self::open_preview_in_viewer(path);
                }
            }
            Message::CopyPreview => {
                if let Some(path) = &self.preview_path {
                    let _ = Self::copy_preview_to_clipboard(path);
                }
            }
        }
        Command::none()
    }

    fn view(&self) -> Element<'_, Self::Message> {
        if self.settings_open {
            return self.build_settings_view();
        }
        if self.about_open {
            return self.build_about_view();
        }
        self.build_main_view()
    }
}

fn main() -> iced::Result {
    let mut settings = Settings {
        window: iced::window::Settings {
            size: iced::Size::new(1560.0, 980.0),
            ..Default::default()
        },
        ..Settings::default()
    };
    settings.fonts = load_cjk_fonts();
    settings.default_font = Font::with_name("Microsoft YaHei");

    BoardGuiApp::run(settings)
}
