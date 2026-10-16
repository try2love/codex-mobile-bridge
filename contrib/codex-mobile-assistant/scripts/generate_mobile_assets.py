"""Regenerate native mobile artwork from the existing desktop icon (requires Pillow)."""
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "mobile/android/app/src/main/res"
IOS = ROOT / "mobile/ios/CodexMobile/Assets.xcassets"


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    icon = Image.open(ROOT / "assets/codex-mobile.png").convert("RGBA")
    drawable = ANDROID / "drawable-nodpi"
    drawable.mkdir(parents=True, exist_ok=True)
    icon.resize((256, 256), Image.Resampling.LANCZOS).save(drawable / "brand.png")
    foreground = Image.new("RGBA", (432, 432), (0, 0, 0, 0))
    foreground.alpha_composite(icon.resize((288, 288), Image.Resampling.LANCZOS), (72, 72))
    foreground.save(drawable / "launcher_foreground.png")

    metadata = {"author": "xcode", "version": 1}
    save_json(IOS / "Contents.json", {"info": metadata})
    brand = IOS / "Brand.imageset"
    brand.mkdir(parents=True, exist_ok=True)
    icon.save(brand / "brand.png")
    save_json(brand / "Contents.json", {
        "images": [{"filename": "brand.png", "idiom": "universal", "scale": "2x"}],
        "info": metadata,
    })
    app_icon = IOS / "AppIcon.appiconset"
    app_icon.mkdir(parents=True, exist_ok=True)
    opaque = Image.new("RGB", (1024, 1024), "#181818")
    resized = icon.resize((1024, 1024), Image.Resampling.LANCZOS)
    opaque.paste(resized, mask=resized.getchannel("A"))
    opaque.save(app_icon / "AppIcon.png")
    save_json(app_icon / "Contents.json", {
        "images": [{"filename": "AppIcon.png", "idiom": "universal",
                    "platform": "ios", "size": "1024x1024"}],
        "info": metadata,
    })


if __name__ == "__main__":
    main()
