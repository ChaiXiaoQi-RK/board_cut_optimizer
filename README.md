# 板优排 / board_cut_optimizer

板材开料排版、厚度校验、重量统计、CSV 留档和 PNG 预览的一套工具。

当前代码仓库已经重构为 Rust workspace，分成三部分：

- `crates/board-core`：核心解析、排版、报表输出
- `crates/board-cli`：命令行工具
- `crates/board-desktop`：`iced` 桌面程序

## 版本

`V1.2.1`

## 主要功能

- 支持原始文本转 CSV
- 支持 CSV / XLSX 零切清单排版
- 按厚度分组计算，不同厚度不混排
- 自动识别旋转后更容易排下的方向
- 输出整板用量、面积折算和总重量
- 生成排版 PNG 预览图
- 桌面端支持设置、关于、预览和复制图片

## 目录规则

留档目录仍然使用以下结构：

```text
留档根目录\年-月\月-日\文件名\文件名.csv
留档根目录\年-月\月-日\文件名\文件名.png
```

## 输入格式

原始文本支持常见写法，例如：

```text
500*300*18 4块
800 400 18 2
18厚 600x500 3块
500x537x12mm 1
```

数量缺省时默认按 `1` 处理。

## CLI

### 原始文本转 CSV

```powershell
cargo run -p board-cli --bin board_data_to_csv -- --filename 测试文件2 --data "500*300*18 4块"
```

### 排版并输出 PNG

```powershell
cargo run -p board-cli --bin board_cut_optimizer -- --board-length 1220 --board-width 2440 --input .\samples\test_basic.csv
```

## 桌面程序

```powershell
cargo run -p board-desktop --bin board_gui_app
```

## 说明

- `release/` 目录用于打包产物
- `assets/board_gui_icon.png` 和 `assets/board_gui_icon.ico` 为软件图标
- `thickness_weight.csv` 为厚度重量表

