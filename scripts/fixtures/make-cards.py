#!/usr/bin/env python3
"""生成「角色卡导入 + 单卡聊天」E2E 用的 fixture 卡（中文，三张）。

产出目录默认 /tmp/tavern-verify/cards：
  v2-png.png     —— SillyTavern v2 角色卡塞进 PNG tEXt chunk（keyword=chara，值=base64(JSON)）
  v2-json.json   —— `{"spec":"chara_card_v2","spec_version":"2.0","data":{...}}`
  v1-json.json   —— v1 扁平结构
  v2-lore.json   —— B 步（--space）用：带 `data.character_book.entries` 世界书条目的 v2 卡
  manifest.json  —— 供 verify.mjs --cards/--space 读取：cards=[3 张基础卡]，space.lorebook 单独列出

用法：
  python3 scripts/fixtures/make-cards.py                 # 写默认目录
  python3 scripts/fixtures/make-cards.py --out /tmp/x    # 自定义目录

依赖 Pillow（本机 /usr/lib/python3/dist-packages/PIL）；没有 Pillow 时 PNG 卡无法生成，
但两张 JSON 卡仍会生成，脚本以退出码 1 提示。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CARDS = [
    {
        "file": "v2-png.png",
        "format": "png-v2",
        "name": "林小满",
        "description": "江南小镇临河茶馆的掌柜，十五岁接手铺子，记得每一位客人爱喝什么茶。",
        "personality": "温软、细心、嘴硬心软；说话慢，喜欢用茶点招呼人；对旧事记得很清楚。",
        "scenario": "梅雨时节的午后，你推门进了「满记茶馆」，檐水正滴在青石阶上。",
        "first_mes": "林小满把温好的茶推到你面前，抬眼笑了笑：「外头雨大，先坐。今天想喝点什么？」",
        "mes_example": "<START>\n{{user}}: 你这儿有安静点的位子吗？\n{{char}}: 靠窗那桌，雨声听得清，人也少。茶给你温着。",
        "tags": ["茶馆", "江南", "日常", "治愈"],
        "creator": "verifier-fixture",
        "character_version": "1.0",
    },
    {
        "file": "v2-json.json",
        "format": "json-v2",
        "name": "顾清和",
        "description": "旧书店「清和斋」的店主，寡言，能一眼看出旧书里夹着的秘密。",
        "personality": "安静、克制、观察入微；不主动打听，却总在你卡住时递来恰好的一页。",
        "scenario": "城南旧书市收摊前，你在清和斋最里排书架前停住，一本没有书名的册子露了一角。",
        "first_mes": "顾清和从梯子上下来，拍了拍袖口的灰：「那本不卖。不过你想看，可以坐这儿看。」",
        "mes_example": "<START>\n{{user}}: 你怎么知道我在找这一本？\n{{char}}: 你在这排来回走了三趟，眼睛只往一个地方落。",
        "tags": ["旧书", "悬疑", "慢热"],
        "creator": "verifier-fixture",
        "character_version": "1.0",
    },
    {
        "file": "v1-json.json",
        "format": "json-v1",
        "name": "沈夜舟",
        "description": "夜航船上的说书人，故事真假参半，专挑睡不着的人讲。",
        "personality": "散漫、会逗人、记性极好；讲到要紧处就停，非要你猜。",
        "scenario": "夜里搭的乌篷船，船头挂一盏旧灯，沈夜舟敲了敲醒木，说今晚只讲一个故事。",
        "first_mes": "沈夜舟把灯芯挑亮，笑：「客官来得巧，今晚这个故事，还差一个听的人。」",
        "mes_example": "<START>\n{{user}}: 你讲的故事是真的吗？\n{{char}}: 半真半假。真的是那条河，假的是河里的东西。",
        "tags": ["说书", "夜航", "志怪"],
        "creator": "verifier-fixture",
        "character_version": "1.0",
    },
]

# B 步（角色卡空间 / --space）专用：带世界书条目的 v2 卡。
# H 组（--cards）不会导入这张，避免改变既有 59 项基线；只有 S 组按 manifest.space.lorebook 使用。
LORE_CARD = {
    "file": "v2-lore.json",
    "format": "json-v2-lore",
    "name": "陆三更",
    "description": "在渡口支茶摊的说书人，专挑雨天讲古，茶钱随意。",
    "personality": "慢条斯理、爱卖关子；对茶和雨声格外讲究。",
    "scenario": "雨落在渡口茶棚上，你收起伞，在长凳末端坐下。",
    "first_mes": "陆三更把粗瓷碗推过来：「先暖暖手。雨停之前，正好讲一段。」",
    "mes_example": "<START>\n{{user}}: 你这茶有什么讲究？\n{{char}}: 第三盏要留给赶路的人，这是老规矩。",
    "tags": ["茶馆", "说书", "世界书"],
    "creator": "verifier-fixture",
    "character_version": "1.0",
    "character_book": {
        "name": "渡口茶话",
        "entries": [
            {
                "keys": ["茶", "茶博士"],
                "content": "【E2E-LORE-茶】茶博士只在下雨天讲古；雨后第三盏茶要留给赶路的人，碗底压一枚旧铜钱。",
                "enabled": True,
                "insertion_order": 10,
            },
            {
                "keys": ["雨", "渡口"],
                "content": "【E2E-LORE-雨】渡口的雨声能盖住低声说的话；说书人常在雨最大时讲最真的故事。",
                "enabled": True,
                "insertion_order": 20,
            },
        ],
    },
}


def v2_payload(card: dict) -> dict:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": card["name"],
            "description": card["description"],
            "personality": card["personality"],
            "scenario": card["scenario"],
            "first_mes": card["first_mes"],
            "mes_example": card["mes_example"],
            "creator_notes": "E2E fixture（verifier 生成，可删）",
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": [],
            "tags": card["tags"],
            "creator": card["creator"],
            "character_version": card["character_version"],
            "extensions": {},
            **({"character_book": card["character_book"]} if card.get("character_book") else {}),
        },
    }


def v1_payload(card: dict) -> dict:
    return {
        "name": card["name"],
        "description": card["description"],
        "personality": card["personality"],
        "scenario": card["scenario"],
        "first_mes": card["first_mes"],
        "mes_example": card["mes_example"],
        "creatorcomment": "E2E fixture（verifier 生成，可删）",
        "tags": card["tags"],
        "creator": card["creator"],
        "character_version": card["character_version"],
    }


def find_font() -> str | None:
    for candidate in (
        "/root/.fonts/NotoSansSC_700Bold.ttf",
        "/root/.fonts/NotoSansSC_400Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def draw_avatar(path: Path, name: str, palette: tuple[str, str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    size = 512
    image = Image.new("RGB", (size, size), palette[0])
    draw = ImageDraw.Draw(image)
    for y in range(size):
        ratio = y / size
        color = tuple(
            int(round(int(palette[0][i : i + 2], 16) * (1 - ratio) + int(palette[1][i : i + 2], 16) * ratio))
            for i in (1, 3, 5)
        )
        draw.line([(0, y), (size, y)], fill=color)
    # 装饰圆环，避免纯色头像看起来像加载失败
    draw.ellipse((56, 56, size - 56, size - 56), outline=(255, 255, 255), width=4)
    font_path = find_font()
    if font_path:
        font = ImageFont.truetype(font_path, 160)
        initial = name[0]
        box = draw.textbbox((0, 0), initial, font=font)
        draw.text(
            ((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
            initial,
            font=font,
            fill=(255, 255, 255),
        )
    image.save(path, format="PNG")


def write_png_card(out_dir: Path, card: dict, index: int) -> Path:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    path = out_dir / card["file"]
    palettes = [("#3f6f5f", "#9fc3b0"), ("#5a4a72", "#b9a7d6"), ("#6b4a35", "#d8b48f")]
    draw_avatar(path, card["name"], palettes[index % len(palettes)])
    payload = base64.b64encode(json.dumps(v2_payload(card), ensure_ascii=False).encode("utf-8")).decode("ascii")
    info = PngInfo()
    info.add_text("chara", payload)
    with Image.open(path) as image:
        image.save(path, format="PNG", pnginfo=info)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成角色卡 E2E fixture")
    parser.add_argument("--out", default="/tmp/tavern-verify/cards", help="输出目录（默认 /tmp/tavern-verify/cards）")
    options = parser.parse_args()
    out_dir = Path(options.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"generatedAt": datetime.now(timezone.utc).isoformat(), "cards": []}
    failures: list[str] = []
    for index, card in enumerate(CARDS):
        if card["format"] == "png-v2":
            try:
                write_png_card(out_dir, card, index)
            except ImportError:
                failures.append(f"{card['file']}：缺少 Pillow，没生成 PNG 卡")
                continue
            except Exception as error:  # noqa: BLE001 - fixture 脚本，报清楚即可
                failures.append(f"{card['file']}：{error}")
                continue
        elif card["format"] == "json-v2":
            (out_dir / card["file"]).write_text(
                json.dumps(v2_payload(card), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        else:
            (out_dir / card["file"]).write_text(
                json.dumps(v1_payload(card), ensure_ascii=False, indent=2), encoding="utf-8"
            )
        manifest["cards"].append({"file": card["file"], "name": card["name"], "format": card["format"]})

    # B 步世界书卡（--space 用）：单独生成，不进 cards 列表
    lore_path = out_dir / LORE_CARD["file"]
    lore_path.write_text(json.dumps(v2_payload(LORE_CARD), ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["space"] = {
        "lorebook": {
            "file": LORE_CARD["file"],
            "name": LORE_CARD["name"],
            "format": LORE_CARD["format"],
            "keys": ["茶", "雨"],
            "markers": ["【E2E-LORE-茶】", "【E2E-LORE-雨】"],
        }
    }

    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"fixtures 写入 {out_dir}")
    for item in manifest["cards"]:
        print(f"  {item['file']:<16} {item['format']:<8} {item['name']}")
    print(f"  {LORE_CARD['file']:<16} {LORE_CARD['format']:<8} {LORE_CARD['name']}  (--space 世界书卡)")
    if failures:
        for failure in failures:
            print(f"WARN  {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
