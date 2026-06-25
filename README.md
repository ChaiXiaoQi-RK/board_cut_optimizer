# 板优排 / board_cut_optimizer

板优排是一个用于板材开料数据整理、厚度校验、自动排版、重量统计与排板图输出的桌面工具。

当前版本：`V1.1.0`

## 目录结构

- `board_gui_app.py`
  桌面 GUI 主程序
- `board_data_to_csv.py`
  原始文本转标准 CSV
- `board_cut_optimizer.py`
  排版计算、CSV 输出、PNG 输出
- `board_gui_app.spec`
  PyInstaller 打包配置
- `assets/`
  软件图标资源
- `samples/`
  示例输入文件
- `release/`
  当前打包好的桌面版发布文件
- `thickness_weight.csv`
  厚度重量表示例

## 运行环境

推荐 Python 3.11+。

依赖见 `requirements.txt`。

## 开发运行

```powershell
python .\board_gui_app.py
```

## 打包

```powershell
python -m PyInstaller --noconfirm --clean .\board_gui_app.spec
```

打包输出默认在：

```text
release/board_gui_app/
```

## 主要功能

- 原始开料文本转 CSV
- 按厚度分组排版
- 校验厚度是否存在于重量表
- 计算整张数量、面积折算张数、总重量
- 输出排板 PNG 长图
- 生成桌面 GUI 工具

