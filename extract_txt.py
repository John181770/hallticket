import re
import pytesseract
from PIL import Image, ImageOps, ImageFilter

def read_captcha_text(image_path, tesseract_cmd=None):
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    img = Image.open(image_path).convert("L")
    img = img.resize((img.width * 4, img.height * 4))
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.MedianFilter(size=3))
    img = img.point(lambda x: 255 if x > 145 else 0, mode="1")

    raw = pytesseract.image_to_string(
        img,
        config="--oem 3 --psm 8 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    )
    return re.sub(r"[^A-Z0-9]", "", raw.upper())

# Example:
# text = read_captcha_text("captcha.png", "/opt/homebrew/bin/tesseract")
# print(text)
