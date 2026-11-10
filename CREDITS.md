# 第三方内容署名（Credits）

酒馆内置的 5 张**预设角色卡**不是原创内容，来自开源角色卡项目，按
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 许可使用。本文件记录来源、
作者、许可与我们对卡片做的修改，以满足署名（attribution）要求。

## 上游项目

| 项目 | 内容 |
| --- | --- |
| 仓库 | [foreverse-app/character-card-skills](https://github.com/foreverse-app/character-card-skills) |
| 作者 | **Foreverse Team**（[foreverse.app](https://foreverse.app)） |
| 内容许可 | [Creative Commons Attribution 4.0 International（CC BY 4.0）](https://creativecommons.org/licenses/by/4.0/) |
| 使用方式 | 作为酒馆新账号的默认角色卡（种子卡）随应用分发 |

## 修改说明

我们在入库前对卡片做了**两处格式修改**，除此之外**未改动卡片内容**
（名称、设定、开场白、示例对话、世界书等文本均保持上游原样）：

1. **文本格式转换**：上游卡片的文本字段内嵌 HTML/CSS（如 `<div>`、`<style>`、
   字体/排版样式），我们将其转换为**纯文本**，只保留文字与换行，以便在聊天界面
   中直接显示、不把标签原文露给用户。
2. **头像处理**：上游插图被**居中裁切为 512×512 方形缩略图**（`*.png`），
   用于卡库网格与聊天头像。

每张卡的 `data.extensions.provenance` 里也写入了同样的来源、许可、作者与修改说明，
随卡片数据一起保存/导出。

## 逐卡来源

本地资产位于 `app/server/app/cards/presets/`（`<slug>.json` + `<slug>.png`，
清单见 `MANIFEST.json`）。上游均为 CC BY 4.0，源文件 URL：

| 名称 | slug | 上游源文件 | 本地文件 |
| --- | --- | --- | --- |
| 玄净 | `xuanjing` | [cards/zh/xuanjing/xuanjing.v2.json](https://github.com/foreverse-app/character-card-skills/blob/main/cards/zh/xuanjing/xuanjing.v2.json) | `presets/xuanjing.json`、`presets/xuanjing.png` |
| 裴聿 | `peiyu` | [cards/zh/peiyu/peiyu.v2.json](https://github.com/foreverse-app/character-card-skills/blob/main/cards/zh/peiyu/peiyu.v2.json) | `presets/peiyu.json`、`presets/peiyu.png` |
| 奈芙尔 | `naifuer` | [cards/zh/naifuer/naifuer.v2.json](https://github.com/foreverse-app/character-card-skills/blob/main/cards/zh/naifuer/naifuer.v2.json) | `presets/naifuer.json`、`presets/naifuer.png` |
| 沈砚 | `shenyan` | [cards/zh/shenyan/shenyan.v2.json](https://github.com/foreverse-app/character-card-skills/blob/main/cards/zh/shenyan/shenyan.v2.json) | `presets/shenyan.json`、`presets/shenyan.png` |
| 唐团团 | `tangtuantuan` | [cards/zh/tangtuantuan/tangtuantuan.v2.json](https://github.com/foreverse-app/character-card-skills/blob/main/cards/zh/tangtuantuan/tangtuantuan.v2.json) | `presets/tangtuantuan.json`、`presets/tangtuantuan.png` |

所有卡片的内容许可均为
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；许可全文以上游链接为准。

## 复用 / 再分发时的署名建议

如果把这些预设卡（或修改后的版本）再分发，建议保留以下署名，并注明你做的修改：

> Character cards by Foreverse Team (foreverse.app), licensed under CC BY 4.0
> (https://creativecommons.org/licenses/by/4.0/). Source:
> https://github.com/foreverse-app/character-card-skills. Modified: text fields
> converted from HTML/CSS to plain text; avatars center-cropped to 512×512.

CC BY 4.0 不包含商标/代言授权：署名不代表上游认可酒馆或我们的修改。
