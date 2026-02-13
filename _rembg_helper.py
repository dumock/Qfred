"""rembg 헬퍼 - PyQt6 DLL 충돌 방지를 위해 별도 프로세스로 실행"""
import sys

if len(sys.argv) != 3:
    print("Usage: _rembg_helper.py <input_path> <output_path>", file=sys.stderr)
    sys.exit(1)

input_path, output_path = sys.argv[1], sys.argv[2]

try:
    from rembg import remove
    from PIL import Image

    img = Image.open(input_path).convert("RGBA")
    result = remove(img)
    result.save(output_path, "PNG")
    print("OK")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
