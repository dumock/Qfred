"""인페인팅 헬퍼 - PyQt6 프로세스와 분리하여 실행"""
import sys

if len(sys.argv) != 4:
    print("Usage: _inpaint_helper.py <image_path> <mask_path> <output_path>", file=sys.stderr)
    sys.exit(1)

image_path, mask_path, output_path = sys.argv[1], sys.argv[2], sys.argv[3]

try:
    import cv2
    import numpy as np

    # 한글 경로 지원: np.fromfile + imdecode
    def imread_safe(path, flags=cv2.IMREAD_COLOR):
        buf = np.fromfile(path, dtype=np.uint8)
        return cv2.imdecode(buf, flags)

    def imwrite_safe(path, img):
        _, buf = cv2.imencode('.png', img)
        buf.tofile(path)

    img = imread_safe(image_path, cv2.IMREAD_COLOR)
    if img is None:
        print(f"ERROR: cannot read image", file=sys.stderr)
        sys.exit(1)

    # 마스크: RGBA PNG에서 알파 채널 추출 (alpha > 0 = 칠한 영역)
    mask_rgba = imread_safe(mask_path, cv2.IMREAD_UNCHANGED)
    if mask_rgba is None:
        print(f"ERROR: cannot read mask", file=sys.stderr)
        sys.exit(1)

    if mask_rgba.ndim == 3 and mask_rgba.shape[2] == 4:
        mask = mask_rgba[:, :, 3]
    elif mask_rgba.ndim == 3 and mask_rgba.shape[2] == 3:
        mask = cv2.cvtColor(mask_rgba, cv2.COLOR_BGR2GRAY)
    else:
        mask = mask_rgba

    # 마스크 크기 맞추기
    if mask.shape[:2] != img.shape[:2]:
        mask = cv2.resize(mask, (img.shape[1], img.shape[0]))

    # 이진화 (>10 → 흰색)
    _, mask_bin = cv2.threshold(mask, 10, 255, cv2.THRESH_BINARY)

    # 마스크 팽창 (자막 테두리 잔상 제거)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask_dilated = cv2.dilate(mask_bin, kernel, iterations=2)

    # 인페인팅 (Telea 알고리즘, 반경 7)
    result = cv2.inpaint(img, mask_dilated, inpaintRadius=7, flags=cv2.INPAINT_TELEA)

    imwrite_safe(output_path, result)
    print("OK")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
