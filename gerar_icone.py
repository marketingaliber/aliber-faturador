# Converte logo.png (se existir nesta pasta) no ícone aliber.ico usado no .exe.
import os
from PIL import Image

aqui = os.path.dirname(os.path.abspath(__file__))
logo = next((os.path.join(aqui, n) for n in ("logo.png", "logo.jpg", "logo.jpeg") if os.path.exists(os.path.join(aqui, n))), None)
if logo:
    img = Image.open(logo).convert("RGBA")
    bbox = img.getbbox()                       # corta bordas transparentes
    if bbox:
        img = img.crop(bbox)
    lado = max(img.size)
    quadrado = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    quadrado.paste(img, ((lado - img.width) // 2, (lado - img.height) // 2))
    quadrado.save(os.path.join(aqui, "aliber.ico"),
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icone gerado a partir de", os.path.basename(logo))
else:
    print("logo.png nao encontrado: usando o icone provisorio")
