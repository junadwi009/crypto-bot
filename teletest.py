import asyncio
import httpx

TOKEN = "7703278423:AAGwWZb_MeLeh4MYJIp9MPoA4ZxjSH1MzZc"
BASE  = "https://api.telegram.org/bot" + TOKEN

async def test():
    async with httpx.AsyncClient() as client:

        # Cek bot valid
        r    = await client.get(BASE + "/getMe")
        data = r.json()

        if data.get("ok"):
            bot      = data["result"]
            username = bot["username"]
            name     = bot["first_name"]
            print("Bot OK:", name, "(@" + username + ")")
        else:
            print("Bot INVALID:", data)
            return

        # Ambil chat ID dari pesan yang masuk
        r2      = await client.get(BASE + "/getUpdates")
        updates = r2.json()

        if updates.get("result") and len(updates["result"]) > 0:
            print("\nChat ID yang ditemukan:")
            seen = set()
            for u in updates["result"]:
                msg  = u.get("message", {})
                chat = msg.get("chat", {})
                cid  = chat.get("id")
                if cid and cid not in seen:
                    seen.add(cid)
                    fname = chat.get("first_name", "")
                    lname = chat.get("last_name", "")
                    print("  ID:", cid, " | Nama:", fname, lname)
            print("\nSalin ID di atas ke TELEGRAM_CHAT_ID di .env kamu")
        else:
            print("\nBelum ada update dari bot.")
            print("Langkah:")
            print("  1. Buka Telegram di HP")
            print("  2. Cari bot kamu (nama dari BotFather)")
            print("  3. Klik START atau ketik /start")
            print("  4. Jalankan script ini lagi")

asyncio.run(test())