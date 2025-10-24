# Bot VIP Telegram + PIX (Pushin Pay)

## Como rodar no Render (recomendado)
1) Crie um repositório com estes arquivos: `main.py`, `requirements.txt`, `Procfile`, `Dockerfile` (opcional).
2) No Render: New -> Web Service -> conecte o GitHub.
3) Build: `pip install -r requirements.txt`
4) Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5) Add Disk: /data (1–5GB) e crie a variável `DB_PATH=/data/vipbot.db`.
6) Variáveis de ambiente:
   - BOT_TOKEN=...
   - ADMIN_IDS=5865055558,1168076734
   - VIP_CHAT_ID=-1002612168205
   - PREVIEWS_URL=https://t.me/+rhmcwqkG1bRhMmRh
   - PUSHIN_PAY_TOKEN=...
   - BASE_URL=https://SEUAPP.onrender.com
7) No painel da Pushin Pay, configure o webhook para: `BASE_URL/pushin/webhook`.
8) Coloque o bot como admin do canal/grupo VIP.

## Como rodar local (teste)
1) `pip install -r requirements.txt`
2) Crie `.env` (veja `.env.sample`)
3) `uvicorn main:app --host 0.0.0.0 --port 8000`
4) Se precisar de webhook externo, use ngrok e aponte o webhook da Pushin para `https://SEU.ngrok.io/pushin/webhook`.

## Comandos úteis
- `/start` – início do fluxo
- `/setvideo` – envie um vídeo e responda com `/setvideo` para salvar o vídeo de boas-vindas
- `/status` – mostra status atual do usuário

