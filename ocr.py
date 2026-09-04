"""스캔 PDF용 OCR — Tesseract(kor+eng)로 단어 좌표를 얻어 engine과 같은 형식으로 반환"""
import os
import pymupdf
import pytesseract
from PIL import Image
import io

# exe로 포장했을 때 함께 넣은 tesseract 경로를 우선 사용
import sys
_base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
for _cand in (os.path.join(_base, "tesseract", "tesseract.exe"),
              os.path.join(_base, "tesseract", "tesseract")):
    if os.path.exists(_cand):
        pytesseract.pytesseract.tesseract_cmd = _cand
        os.environ["TESSDATA_PREFIX"] = os.path.join(os.path.dirname(_cand), "tessdata")
        break
# 포장 안 된 경우: 맥은 brew (tesseract + tesseract-lang), 윈도우는 설치 경로가 PATH에 있어야 함

DPI = 300


def ocr_words(page):
    zoom = DPI / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, lang="kor+eng", output_type=pytesseract.Output.DICT,
                                     config="--psm 6")
    words = []
    for i, t in enumerate(data["text"]):
        t = t.strip()
        if not t or int(data["conf"][i]) < 30:
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        words.append((x / zoom, y / zoom, (x + w) / zoom, (y + h) / zoom, t))
    return words
