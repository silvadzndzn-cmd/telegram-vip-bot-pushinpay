"""
Bot VIP com PIX (Pushin Pay) – Telegram (Aiogram v3 + FastAPI)
Resumo:
- /start com vídeo de entrada (configurável via /setvideo)
- Boas-vindas + botões [Desbloquear VIP agora] e [Ver prévias grátis]
- Planos: Semanal R$17,99 (7d), Mensal R$24,99 (30d), Vitalício R$37,90 (∞)
- Pagamento com PIX copia-e-cola + botões “Copiar código”(texto), “EFETUEI O PAGAMENTO” e “Qr code”
- Webhook Pushin Pay (x-www-form-urlencoded): id, status=paid, value (centavos), end_to_end_id, payer_name, payer_national_registration
- Ao aprovar: link único (member_limit=1, TTL=1h), envia ao usuário, notifica admins, revoga após entrar
- /status (ATIVA/INATIVA) e expiração automática
"""
import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import httpx
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ChatInviteLink,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

import qrcode
from io import BytesIO
import base64

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
VIP_CHAT_ID = int(os.getenv("VIP_CHAT_ID", "0"))
PREVIEWS_URL = os.getenv("PREVIEWS_URL", "")
PUSHIN_PAY_TOKEN = os.getenv("PUSHIN_PAY_TOKEN", "")
BASE_URL = os.getenv("BASE_URL", "")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN ausente no .env")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

# Use /data no Render para persistir entre deploys (ajuste se local)
DB_PATH = os.getenv("DB_PATH", "vipbot.db")

# Planos em centavos
PLANS = {
    "WEEK": {"title": "SEMENAL", "label": "🔷 SEMANAL - R$ 17.99", "amount": 1799, "days": 7},
    "MONTH": {"title": "MENSAL", "label": "🟧 MENSAL - R$ 24.99", "amount": 2499, "days": 30},
    "LIFE": {"title": "VITALÍCIO (Paga só 1 vez!) - R$ 37.90", "label": "💎 VITALÍCIO (Paga só 1 vez!) - R$ 37.90", "amount": 3790, "days": 36500},
}

WELCOME_TEXT = (
    "👋 Bem-vindo ao MELHORES VAZADOS 😱\n\n"
    "🔥 MILHARES de vídeos [ocultados] em um só lugar!\n\n"
    "🚨 OFERTA POR TEMPO LIMITADO\n"
    "📥 Conteúdos exclusivos +18, organizados e atualizados:\n\n"
    "🔹 LOMOTIFS + AMADORAS\n"
    "🔹 VAZAMENTOS RECENTES (ficção/encenado)\n"
    "🔹 TEMÁTICOS / FUNK\n"
    "🔹 CARNAVAL, BBB e muito mais\n\n"
    "⚠️ Só pra maiores de 18!\n"
    "Suporte: @OnlySuporte\n\n"
    "👇 Escolha uma opção:"
)

INIT_SQL = """
CREATE TABLE IF NOT EXISTS users(
  user_id INTEGER PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  last_name TEXT
);

CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  plan_id TEXT,
  amount_cents INTEGER,
  pushin_id TEXT,
  status TEXT,
  created_at INTEGER
);

CREATE TABLE IF NOT EXISTS subscriptions(
  user_id INTEGER PRIMARY KEY,
  plan_id TEXT,
  start_ts INTEGER,
  end_ts INTEGER,
  active INTEGER
);

CREATE TABLE IF NOT EXISTS invite_links(
  link TEXT PRIMARY KEY,
  user_id INTEGER,
  created_ts INTEGER,
  revoked INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

# ---------- helpers ----------
def now_ts() -> int:
    return int(time.time())

async def init_db():
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.executescript(INIT_SQL)
        await conn.commit()

async def ensure_user(u):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO users(user_id, username, first_name, last_name) VALUES(?,?,?,?)",
            (u.id, u.username, u.first_name, u.last_name),
        )
        await conn.commit()

async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))
        await conn.commit()

async def get_setting(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_subscription(user_id:int, plan_id:str, days:int):
    start = now_ts()
    end = start + days*24*3600
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO subscriptions(user_id, plan_id, start_ts, end_ts, active) VALUES(?,?,?,?,1)\n"
            "ON CONFLICT(user_id) DO UPDATE SET plan_id=excluded.plan_id, start_ts=excluded.start_ts, end_ts=excluded.end_ts, active=1",
            (user_id, plan_id, start, end)
        )
        await conn.commit()
    return end

async def get_subscription(user_id:int):
    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT plan_id, start_ts, end_ts, active FROM subscriptions WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone()

async def deactivate_subscription(user_id:int):
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE subscriptions SET active=0 WHERE user_id=?", (user_id,))
        await conn.commit()

async def notify_admins(text:str):
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, text)
        except Exception:
            pass

async def generate_one_time_invite(user_id:int, ttl_hours:int=1) -> ChatInviteLink:
    expire_date = int(time.time()) + ttl_hours*3600
    inv = await bot.create_chat_invite_link(chat_id=VIP_CHAT_ID, member_limit=1, expire_date=expire_date)
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO invite_links(link, user_id, created_ts, revoked) VALUES(?,?,?,0)",
            (inv.invite_link, user_id, now_ts())
        )
        await conn.commit()
    return inv

async def revoke_link(link:str):
    try:
        await bot.revoke_chat_invite_link(VIP_CHAT_ID, link)
    except Exception:
        pass
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE invite_links SET revoked=1 WHERE link=?", (link,))
        await conn.commit()

async def kick_from_vip(user_id:int):
    try:
        await bot.ban_chat_member(VIP_CHAT_ID, user_id)
        await bot.unban_chat_member(VIP_CHAT_ID, user_id)
    except Exception:
        pass

# ---------- UI builders ----------
def home_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="💎 Desbloquear VIP agora", callback_data="unlock")
    kb.button(text="👀 Ver prévias grátis", url=PREVIEWS_URL or "https://t.me")
    kb.adjust(1)
    return kb.as_markup()

def plans_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔷 SEMANAL - R$ 17.99", callback_data="buy:WEEK")
    kb.button(text="🟧 MENSAL - R$ 24.99", callback_data="buy:MONTH")
    kb.button(text="💎 VITALÍCIO (Paga só 1 vez!) - R$ 37.90", callback_data="buy:LIFE")
    kb.adjust(1)
    return kb.as_markup()

def payment_kb(qr_page_url:str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="EFETUEI O PAGAMENTO", callback_data="paid_check")],
        [InlineKeyboardButton(text="Qr code", url=qr_page_url)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ---------- Bot handlers ----------
@dp.message(CommandStart())
async def cmd_start(m: Message):
    await ensure_user(m.from_user)
    # envia vídeo se configurado
    video_id = await get_setting("welcome_video")
    if video_id:
        try:
            await m.answer_video(video=video_id)
        except Exception:
            pass
    await m.answer(WELCOME_TEXT, reply_markup=home_kb())

@dp.message(Command("setvideo"))
async def cmd_setvideo(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return await m.answer("Somente admins.")
    if not m.reply_to_message or not (m.reply_to_message.video or m.reply_to_message.animation):
        return await m.answer("Responda a um VÍDEO com /setvideo para definir o vídeo de boas-vindas.")
    file_id = (m.reply_to_message.video or m.reply_to_message.animation).file_id
    await set_setting("welcome_video", file_id)
    await m.answer("Vídeo inicial atualizado ✅")

@dp.callback_query(F.data == "unlock")
async def on_unlock(cb: CallbackQuery):
    await cb.message.edit_text("Escolha uma oferta abaixo:", reply_markup=plans_kb())
    await cb.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def on_buy(cb: CallbackQuery):
    plan_id = cb.data.split(":",1)[1]
    plan = PLANS[plan_id]

    # cria cobrança
    charge = await create_pushin_charge(plan["amount"])  # retorna dict com id e qr_code (copia e cola)
    pushin_id = charge.get("id")
    pix_code = charge.get("qr_code")

    # registra
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute(
            "INSERT INTO payments(user_id, plan_id, amount_cents, pushin_id, status, created_at) VALUES(?,?,?,?,?,?)",
            (cb.from_user.id, plan_id, plan["amount"], pushin_id, "created", now_ts()),
        )
        await conn.commit()

    qr_url = f"{BASE_URL}/qrcode/{pushin_id}"

    text = (
        "Aguarde um momento enquanto preparamos tudo :)\n\n"
        "Para efetuar o pagamento, utilize a opção 'Pagar' > 'PIX copia e Cola' no aplicativo do seu banco.\n\n"
        "Copie o código abaixo:\n"
    )
    await cb.message.edit_text(text)
    await cb.message.answer(f"`{pix_code}`", parse_mode="Markdown")
    await cb.message.answer("Após efetuar o pagamento, clique no botão abaixo ⤵️", reply_markup=payment_kb(qr_url))
    await cb.answer()

@dp.callback_query(F.data == "paid_check")
async def paid_check(cb: CallbackQuery):
    await cb.message.answer("Aguarde um momento enquanto preparamos tudo :)")
    await cb.message.answer("Ops, parece que ainda não conseguimos identificar o seu pagamento :(  Se você já realizou o pagamento, clique aqui --> /status")
    await cb.answer()

@dp.message(Command("status"))
async def cmd_status(m: Message):
    sub = await get_subscription(m.from_user.id)
    if not sub or not sub[3]:
        return await m.answer(
            "Você não possui nenhuma assinatura atualmente",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Exibir planos", callback_data="unlock")]]
            )
        )
    plan_id, start_ts, end_ts, active = sub
    if not active:
        return await m.answer(
            "Você não possui nenhuma assinatura atualmente",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Exibir planos", callback_data="unlock")]]
            )
        )
    dt = datetime.fromtimestamp(end_ts, tz=timezone.utc).astimezone()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Exibir planos", callback_data="unlock")],
        [InlineKeyboardButton(text="Acessar grupo", url="https://t.me/+PLACEHOLDER")],
    ])
    await m.answer(f"Status atual da sua assinatura - ATIVA\nExpira em: {dt:%d/%m/%Y}\nClique no botão abaixo para acessar", reply_markup=kb)

@dp.chat_member()
async def on_member(update: ChatMemberUpdated):
    if update.chat.id != VIP_CHAT_ID:
        return
    if update.new_chat_member.status == ChatMemberStatus.MEMBER:
        async with aiosqlite.connect(DB_PATH) as conn:
            async with conn.execute("SELECT link FROM invite_links WHERE user_id=? AND revoked=0", (update.new_chat_member.user.id,)) as cur:
                rows = await cur.fetchall()
        for (link,) in rows:
            await revoke_link(link)

# ---------- Pushin Pay ----------
async def create_pushin_charge(amount_cents:int) -> dict:
    url = "https://api.pushinpay.com.br/api/pix/cashIn"
    headers = {
        "Authorization": f"Bearer {PUSHIN_PAY_TOKEN}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "value": amount_cents,
        "webhook_url": f"{BASE_URL}/pushin/webhook",
        "split_rules": []
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
        return r.json()

@app.post("/pushin/webhook")
async def pushin_webhook(request: Request):
    form = await request.form()  # x-www-form-urlencoded
    status = form.get("status")            # 'paid'
    pushin_id = form.get("id")             # id da cobrança
    value = form.get("value")              # centavos
    end_to_end_id = form.get("end_to_end_id")
    payer_name = form.get("payer_name")

    if status != "paid" or not pushin_id:
        return JSONResponse({"ok": True, "ignored": True})

    async with aiosqlite.connect(DB_PATH) as conn:
        async with conn.execute("SELECT user_id, plan_id, amount_cents FROM payments WHERE pushin_id=?", (pushin_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "payment_not_found"})

    user_id, plan_id, amount_cents = row
    plan = PLANS.get(plan_id)

    end_ts = await set_subscription(user_id, plan_id, plan["days"])
    dt = datetime.fromtimestamp(end_ts, tz=timezone.utc).astimezone()

    inv = await generate_one_time_invite(user_id, ttl_hours=1)

    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute("UPDATE payments SET status=? WHERE pushin_id=?", ("paid", pushin_id))
        await conn.commit()

    try:
        await bot.send_message(user_id, "Olá usuário. Seu pagamento acabou de ser aprovado!")
        await bot.send_message(
            user_id,
            "Acessar grupo:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Acessar grupo", url=inv.invite_link)
            ],[
                InlineKeyboardButton(text="Exibir planos", callback_data="unlock")
            ]])
        )
    except Exception:
        pass

    try:
        user = await bot.get_chat(user_id)
        username = user.username or ""
    except Exception:
        username = ""
    at = f"@{username}" if username else "@"
    valor_reais = (amount_cents or 0)/100
    admin_text = (
        "✅ Pagamento aprovado!\n"
        f"🆔 Clientid: {user_id}\n"
        f"👤 User: {at}\n"
        f"📝 Nome: {payer_name or '—'}\n"
        f"💵 Valor: R$ {valor_reais:.2f}\n"
        f"📦 Tipo: assinatura\n"
        f"🔗 Plano: {plan['title'] if plan else plan_id}"
    )
    await notify_admins(admin_text)

    return JSONResponse({"ok": True})

# Página simples do QR (para o botão "Qr code")
@app.get("/qrcode/{pushin_id}", response_class=HTMLResponse)
async def qrcode_page(pushin_id: str):
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"https://api.pushinpay.com.br/api/pix/cashIn/{pushin_id}", headers={"Authorization": f"Bearer {PUSHIN_PAY_TOKEN}"})
            r.raise_for_status()
            data = r.json()
            pix_code = data.get("qr_code") or ""
    except Exception:
        pix_code = ""

    if not pix_code:
        return HTMLResponse("<html><body><h3>QR Code indisponível. Tente novamente.</h3></body></html>")

    img = qrcode.make(pix_code)
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    html = f"""
    <html><head><meta charset='utf-8'><title>Pagamento PIX</title></head>
    <body style='font-family:sans-serif;background:#111;color:#eee;display:flex;flex-direction:column;align-items:center;gap:16px;padding:24px;'>
      <h2>Pagamento selecionado: PIX</h2>
      <img src='data:image/png;base64,{b64}' width='260' height='260' />
      <div style='max-width:640px;word-wrap:break-word;background:#222;padding:12px;border-radius:8px'>{pix_code}</div>
      <small>Escaneie o QR Code ou copie e cole o código no seu banco</small>
    </body></html>
    """
    return HTMLResponse(html)

# Expiração automática
async def expire_watcher():
    while True:
        try:
            ts = now_ts()
            async with aiosqlite.connect(DB_PATH) as conn:
                async with conn.execute("SELECT user_id FROM subscriptions WHERE active=1 AND end_ts<?", (ts,)) as cur:
                    rows = await cur.fetchall()
            for (uid,) in rows:
                await kick_from_vip(uid)
                await deactivate_subscription(uid)
                try:
                    await bot.send_message(
                        uid,
                        "Olá usuário, sua assinatura para o bot acabou de expirar.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Exibir Planos", callback_data="unlock")]])
                    )
                except Exception:
                    pass
        except Exception:
            pass
        await asyncio.sleep(60)

@app.on_event("startup")
async def on_startup():
    await init_db()
    asyncio.create_task(expire_watcher())
    asyncio.create_task(dp.start_polling(bot))

@app.get("/")
async def root():
    return {"ok": True}
