import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
SESION_FILE = BASE / "sesion.json"

def buscar_frame_con_select(page, espera_seg=15):
    fin = time.time() + espera_seg
    while time.time() < fin:
        for fr in page.frames:
            try:
                if fr.query_selector("select"):
                    return fr
            except Exception:
                pass
        time.sleep(1)
    return None

with sync_playwright() as p:
    args_anti_deteccion = ["--disable-blink-features=AutomationControlled"]
    browser = p.chromium.launch(headless=True, args=args_anti_deteccion)
    ctx = browser.new_context(storage_state=str(SESION_FILE), viewport={"width": 1200, "height": 900})
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://script.google.com/macros/s/AKfycby5vAk1YOb9Nv7-8c0c7ZMmk-FrLxKc317l_L3NnPgtaYJMeF2NtdLgIBK4qMKUNNm5pQ/exec", wait_until="domcontentloaded")
    
    frame = buscar_frame_con_select(page)
    if not frame:
        print("No se encontro el select.")
        browser.close()
        exit(1)
    
    opciones = frame.eval_on_selector_all("select option", "els => els.map(e => ({value: e.value, text: e.textContent.trim()}))")
    opciones = [o for o in opciones if o["value"] and "elige" not in o["text"].lower()]
    
    for o in opciones[:1]:
        print("Seleccionando:", o["text"])
        frame.select_option("select", value=o["value"])
        time.sleep(5)
        texto = frame.inner_text("body")
        (BASE / "dump.txt").write_text(texto, encoding="utf-8")
        frame.page.screenshot(path=str(BASE / "curso_screenshot.png"))
    
    browser.close()
