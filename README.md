<div align="center">

# ♟️ Chesa Auto

**Telegram auto-play bot for the [@GameFactoryBot](https://t.me/GameFactoryBot) Chesa (Chess) webapp**

Opens the chess webapp inside Telegram and plays **every move automatically** using an expert-level alpha-beta minimax engine — built to win every round.

</div>

---

## ✨ Features

- **Full chess engine** (legal moves, castling, en passant, promotion, checkmate/stalemate/draw detection)
- **Alpha-beta minimax** search with move ordering and material + piece-square-table evaluation
- **Adjustable depth** (Fast=2 · Balanced=3 · Strong=4) from the webapp UI
- **Auto-play loop** — reads the live board, thinks, applies the move, repeats until the game ends
- **Telegram bot** (pyrogram) with `/start`, `/play`, `/stats`, `/ping`, `/broadcast`, force-sub to your update channel, and MongoDB user tracking
- **Heroku one-click deploy** button + `app.json` config
- **Logging** to a Telegram log channel

## 🚀 Deploy to Heroku

[![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/oyevipthoma88/Chesa_Auto)

1. Click **Deploy to Heroku** above.
2. Fill in the config vars (see below).
3. Deploy — the web process runs the bot and serves the webapp on your Heroku URL.
4. Open Telegram → your bot → `/start` → tap **Play Chesa (Auto)**.

### Required Config Vars

| Var | Where to get it |
|-----|-----------------|
| `API_ID` | https://my.telegram.org → API Development Tools |
| `API_HASH` | same as above |
| `BOT_TOKEN` | @BotFather → /newbot |
| `OWNER_ID` | your numeric Telegram ID (@userinfobot) |
| `MONGO_URI` | MongoDB Atlas connection string |
| `LOG_CHANNEL` | numeric chat ID of your log chat (add the bot as admin) |
| `HEROKU_APP_NAME` | your Heroku app name (e.g. `chesa-auto`) |

Optional: `LOG_CHANNEL`, `WEBAPP_URL`, `UPDATE_CHANNEL`, `UPDATE_CHANNEL_LINK`, `MONGO_DB`, `AUTOPLAY_DEPTH`.

> **WEBAPP_URL** — new Heroku apps get a random domain suffix (e.g. `chesa-auto-1a2b3c4d5e6f.herokuapp.com`). Copy the real domain from Heroku → Settings → Domains and set `WEBAPP_URL=https://<domain>/webapp/`. Telegram only accepts **https** webapp links.

> **MongoDB Atlas** — allow `0.0.0.0/0` in Network Access, otherwise Heroku can't connect.

## 🛠️ Run locally

```bash
git clone https://github.com/oyevipthoma88/Chesa_Auto
cd Chesa_Auto
cp config.env.example config.env   # fill in your values
pip install -r requirements.txt
python -m chesa_auto.main
```

The webapp is served at `http://localhost:8080/webapp/`.

## 🔐 User session

`/login` (owner, private chat) signs in your user account. The session is stored in MongoDB, so it survives Heroku dyno restarts and is restored automatically on boot. `/logout` revokes and deletes it.

## 📦 Stack

- Python 3.12
- [Kurigram](https://pypi.org/project/kurigram/) 2.2 — maintained drop-in fork of Pyrogram (imports stay `from pyrogram import …`)
- PyMongo 4.20 native async client (Motor removed — it was deprecated and caused the `_QUERY_OPTIONS` import crash)

## 📁 Project structure

```
Chesa_Auto/
├── chesa_auto/
│   ├── __init__.py
│   ├── main.py          # entry point: HTTP server + bot
│   ├── bot.py           # pyrogram Telegram bot
│   ├── config.py        # env-var config
│   ├── database.py      # MongoDB layer (PyMongo async client)
│   ├── engine.py        # Python chess engine (used server-side)
│   ├── logger.py
│   └── webapp/
│       ├── index.html   # the Telegram webapp UI
│       ├── style.css
│       ├── engine.js    # browser chess engine (alpha-beta)
│       └── autoplay.js  # autoplay controller
├── Procfile             # Heroku web process
├── app.json             # Heroku one-click deploy config
├── .python-version   # Python 3.12 for Heroku
├── requirements.txt
└── config.env.example
```

## 🧠 How the engine plays to win

The engine evaluates every legal move with **minimax + alpha-beta pruning**:

- **Material** — standard piece values (P=100, N=320, B=330, R=500, Q=900, K=20000)
- **Position** — piece-square tables reward good placement (center pawns, developed knights, safe king)
- **Endgame awareness** — switches the king table when material is low
- **Move ordering** — captures and promotions are searched first for faster pruning
- **Search depth** — 3 plies by default; bump to 4 for stronger (slower) play

It checks for checkmate, stalemate, and insufficient-material draws after every move, so it never walks into a loss silently.

## ⚠️ Notes

- Telegram webapps run in an in-app browser. The autoplay reads the board from the live webapp DOM and dispatches human-style pointer events so the game accepts the moves.
- The engine is deterministic; at equal depth it always picks the same best move. Use depth 4 for the strongest play (slightly slower per move).
- Keep `API_ID` / `API_HASH` / `BOT_TOKEN` / `MONGO_URI` secret — never commit `config.env`.

## 📜 License

MIT — free to use, modify, and deploy.
