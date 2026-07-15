
import asyncio, logging, os, random, re, secrets
from datetime import datetime, timezone
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB = os.getenv("DATABASE_PATH", "game_bot.sqlite3")
START_BALANCE, DAILY_BONUS = 2500, 500
ADMIN = "some_randomuser"
router = Router()
logging.basicConfig(level=logging.INFO)

RX = {
    "bal": re.compile(r"^(б|баланс)$", re.I),
    "pay": re.compile(r"^(п|перевод)\s+(\d+)$", re.I),
    "bonus": re.compile(r"^(бонус|bonus)$", re.I),
    "mines": re.compile(r"^(мины|mines)\s+(\d+)$", re.I),
    "joker": re.compile(r"^(джокер|joker)\s+(\d+)$", re.I),
    "dice": re.compile(r"^(кости|кубик|dice)\s+(\d+)$", re.I),
    "coin": re.compile(r"^(монета|coin)\s+(\d+)\s+(орел|орёл|решка)$", re.I),
    "roulette": re.compile(r"^(рулетка|roulette)\s+(\d+)\s+(красное|черное|чёрное|зеленое|зелёное|к|ч|з)$", re.I),
    "duel": re.compile(r"^(дуэль|дуел|duel)\s+(\d+)$", re.I),
    "give": re.compile(r"^(выдать|дать)\s+(\d+)$", re.I),
    "take": re.compile(r"^(снять|забрать)\s+(\d+)$", re.I),
    "reset": re.compile(r"^(обнулить|обнулить счет|обнулить счёт)$", re.I),
    "help": re.compile(r"^(хелп|help)$", re.I),
    "adminhelp": re.compile(r"^(админхелп|adminhelp)$", re.I),
}

def uname(u): return f"@{u.username}" if u.username else u.full_name
def admin(u): return bool(u and u.username and u.username.lower() == ADMIN)
def payout_for_steps(bet, steps):
    """Возврат ставки + 25% от исходной ставки за каждый успешный шаг."""
    return bet + (bet * 25 * steps) // 100
def percent_for_steps(steps): return steps * 25

class Store:
    def __init__(self, path): self.path, self.lock = path, asyncio.Lock()

    async def init(self):
        async with aiosqlite.connect(self.path) as d:
            await d.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(
              user_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
              balance INTEGER NOT NULL DEFAULT 2500, last_bonus TEXT
            );
            CREATE TABLE IF NOT EXISTS games(
              game_id TEXT PRIMARY KEY, chat_id INTEGER, message_id INTEGER,
              user_id INTEGER, opponent_id INTEGER DEFAULT 0, type TEXT,
              bet INTEGER, payout INTEGER, danger TEXT DEFAULT '',
              opened TEXT DEFAULT '', status TEXT DEFAULT 'active', meta TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            INSERT OR IGNORE INTO settings VALUES('paused','0');
            """)
            for q in (
                "ALTER TABLE users ADD COLUMN last_bonus TEXT",
                "ALTER TABLE games ADD COLUMN opponent_id INTEGER DEFAULT 0",
                "ALTER TABLE games ADD COLUMN meta TEXT DEFAULT ''",
            ):
                try: await d.execute(q)
                except aiosqlite.OperationalError: pass
            await d.commit()

    async def ensure(self, u):
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            await d.execute("""INSERT INTO users(user_id,username,full_name,balance)
              VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,full_name=excluded.full_name""",
              (u.id,u.username,u.full_name,START_BALANCE))
            r=await (await d.execute("SELECT balance FROM users WHERE user_id=?",(u.id,))).fetchone()
            await d.commit(); return int(r[0])

    async def balance(self, uid):
        async with aiosqlite.connect(self.path) as d:
            r=await (await d.execute("SELECT balance FROM users WHERE user_id=?",(uid,))).fetchone()
            return int(r[0]) if r else START_BALANCE

    async def delta(self, uid, amount):
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            r=await (await d.execute("SELECT balance FROM users WHERE user_id=?",(uid,))).fetchone()
            if not r: await d.rollback(); return False,0
            new=int(r[0])+amount
            if new<0: await d.rollback(); return False,int(r[0])
            await d.execute("UPDATE users SET balance=? WHERE user_id=?",(new,uid))
            await d.commit(); return True,new

    async def transfer(self, a, b, n):
        await self.ensure(a); await self.ensure(b)
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            x=int((await (await d.execute("SELECT balance FROM users WHERE user_id=?",(a.id,))).fetchone())[0])
            if x<n: await d.rollback(); return False,x,await self.balance(b.id)
            await d.execute("UPDATE users SET balance=balance-? WHERE user_id=?",(n,a.id))
            await d.execute("UPDATE users SET balance=balance+? WHERE user_id=?",(n,b.id))
            aa=int((await (await d.execute("SELECT balance FROM users WHERE user_id=?",(a.id,))).fetchone())[0])
            bb=int((await (await d.execute("SELECT balance FROM users WHERE user_id=?",(b.id,))).fetchone())[0])
            await d.commit(); return True,aa,bb

    async def bonus(self,u):
        await self.ensure(u); today=datetime.now(timezone.utc).date().isoformat()
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            bal,last=await (await d.execute("SELECT balance,last_bonus FROM users WHERE user_id=?",(u.id,))).fetchone()
            if last==today: await d.rollback(); return False,int(bal)
            bal=int(bal)+DAILY_BONUS
            await d.execute("UPDATE users SET balance=?,last_bonus=? WHERE user_id=?",(bal,today,u.id))
            await d.commit(); return True,bal

    async def paused(self):
        async with aiosqlite.connect(self.path) as d:
            r=await (await d.execute("SELECT value FROM settings WHERE key='paused'")).fetchone()
            return r and r[0]=='1'

    async def set_pause(self,v):
        async with aiosqlite.connect(self.path) as d:
            await d.execute("UPDATE settings SET value=? WHERE key='paused'",('1' if v else '0',)); await d.commit()

    async def bet(self,u,bet,payout):
        await self.ensure(u)
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            bal=int((await (await d.execute("SELECT balance FROM users WHERE user_id=?",(u.id,))).fetchone())[0])
            if bal<bet: await d.rollback(); return False,bal
            bal=bal-bet+payout
            await d.execute("UPDATE users SET balance=? WHERE user_id=?",(bal,u.id)); await d.commit()
            return True,bal

    async def new_game(self,gid,msg,u,typ,bet,payout,danger="",opp=0):
        await self.ensure(u)
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            bal=int((await (await d.execute("SELECT balance FROM users WHERE user_id=?",(u.id,))).fetchone())[0])
            if bal<bet: await d.rollback(); return False,bal
            await d.execute("UPDATE users SET balance=balance-? WHERE user_id=?",(bet,u.id))
            await d.execute("""INSERT INTO games VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (gid,msg.chat.id,msg.message_id,u.id,opp,typ,bet,payout,danger,"","active",""))
            await d.commit(); return True,bal-bet

    async def game(self,gid):
        async with aiosqlite.connect(self.path) as d:
            r=await (await d.execute("SELECT * FROM games WHERE game_id=?",(gid,))).fetchone()
            return r

    async def update_game(self,gid,**kw):
        if not kw:return
        async with aiosqlite.connect(self.path) as d:
            q="UPDATE games SET "+",".join(f"{k}=?" for k in kw)+" WHERE game_id=?"
            await d.execute(q,(*kw.values(),gid)); await d.commit()

    async def stop_all(self):
        async with self.lock, aiosqlite.connect(self.path) as d:
            await d.execute("BEGIN IMMEDIATE")
            rows=await (await d.execute("SELECT user_id,bet FROM games WHERE status='active'")).fetchall()
            for uid,bet in rows: await d.execute("UPDATE users SET balance=balance+? WHERE user_id=?",(bet,uid))
            await d.execute("UPDATE games SET status='cancelled' WHERE status='active'")
            await d.commit(); return len(rows),sum(x[1] for x in rows)

store=Store(DB)

async def available(m):
    if await store.paused():
        await m.reply("⏸ Игры временно приостановлены администратором."); return False
    return True

def mines_kb(gid,opened=set(),danger=set(),done=False,payout=0):
    rows=[]
    for r in range(0,16,4):
        row=[]
        for c in range(r,r+4):
            if done: t="💣" if c in danger else ("✅" if c in opened else "▫️"); data="x"
            elif c in opened: t,data="✅","x"
            else: t,data="▫️",f"m:{gid}:{c}"
            row.append(InlineKeyboardButton(text=t,callback_data=data))
        rows.append(row)
    if not done: rows.append([InlineKeyboardButton(text=f"💰 Забрать {payout}",callback_data=f"cash:{gid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def joker_kb(gid,danger=set(),opened=set(),done=False,payout=0):
    rows=[]
    stages=max(1, (max(danger | opened) // 3 + 1) if (danger or opened) else 1)
    active_stage=stages-1

    for stage in range(stages):
        row=[]
        stage_cells=range(stage*3, stage*3+3)
        selected=next((c for c in stage_cells if c in opened), None)

        for cell in stage_cells:
            if done:
                if cell in opened:
                    text="💀" if cell in danger else "🃏"
                else:
                    text="🂠"
                data="x"
            elif stage < active_stage:
                text=("💀" if cell in danger else "🃏") if cell == selected else "🂠"
                data="x"
            else:
                text="🂠"
                data=f"j:{gid}:{cell}"

            row.append(InlineKeyboardButton(text=text,callback_data=data))
        rows.append(row)

    if not done:
        rows.append([InlineKeyboardButton(text=f"💰 Забрать {payout}",callback_data=f"jcash:{gid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

PLAYER_HELP=f"""🎮 <b>Команды игроков</b>

<code>б</code> — показать баланс
Ответ на сообщение + <code>п 500</code> — перевести баллы
<code>бонус</code> — ежедневный бонус
<code>мины 100</code> — начать «Мины»
<code>джокер 100</code> — начать «Джокер»
<code>кости 100</code> — бросить кубик
<code>монета 100 орёл</code> — орёл или решка
<code>рулетка 100 красное</code> — ставка на цвет
Ответ на сообщение + <code>дуэль 100</code> — вызвать игрока

<code>хелп</code> — показать этот список"""

ADMIN_HELP="""🛡 <b>Команды администратора</b>

Ответ на сообщение + <code>выдать 500</code>
Ответ на сообщение + <code>снять 500</code>
Ответ на сообщение + <code>обнулить</code>
<code>пауза</code> — остановить запуск новых игр
<code>продолжить</code> — снова разрешить игры
<code>стопигры</code> — завершить все активные игры и вернуть ставки

Единственный администратор: @Some_RaNdOmuser"""

@router.message(CommandStart())
@router.message(Command("help"))
async def start(m:Message):
    await store.ensure(m.from_user)
    await m.answer(PLAYER_HELP,parse_mode="HTML")

@router.message(F.text.regexp(RX["help"]))
async def player_help(m:Message):
    await m.answer(PLAYER_HELP,parse_mode="HTML")

@router.message(Command("adminhelp"))
@router.message(F.text.regexp(RX["adminhelp"]))
async def admin_help(m:Message):
    if not admin(m.from_user):
        return await m.reply("⛔ Эта команда доступна только администратору.")
    await m.answer(ADMIN_HELP,parse_mode="HTML")

@router.message(F.text.regexp(RX["bal"]))
async def bal(m): await m.reply(f"💰 Баланс: {await store.ensure(m.from_user)}")

@router.message(F.text.regexp(RX["bonus"]))
async def bonus(m):
    ok,b=await store.bonus(m.from_user)
    await m.reply(f"{'🎁 Получено '+str(DAILY_BONUS) if ok else '⏳ Сегодня бонус уже получен'}. Баланс: {b}")

@router.message(F.text.regexp(RX["pay"]))
async def pay(m):
    if not m.reply_to_message or not m.reply_to_message.from_user: return await m.reply("Ответь на сообщение игрока.")
    n=int(RX["pay"].match(m.text).group(2)); t=m.reply_to_message.from_user
    if t.id==m.from_user.id or t.is_bot:return await m.reply("Нельзя перевести этому пользователю.")
    ok,a,b=await store.transfer(m.from_user,t,n)
    await m.reply(f"{'✅ Переведено' if ok else '❌ Недостаточно баллов'}. Твой баланс: {a}"+(f"\nБаланс получателя: {b}" if ok else ""))

@router.message(F.text.regexp(RX["give"]))
async def give(m):
    if not admin(m.from_user):return
    if not m.reply_to_message:return await m.reply("Ответь на сообщение.")
    t=m.reply_to_message.from_user;n=int(RX["give"].match(m.text).group(2));await store.ensure(t)
    _,b=await store.delta(t.id,n);await m.reply(f"✅ Выдано {n}. Баланс {uname(t)}: {b}")

@router.message(F.text.regexp(RX["take"]))
async def take(m):
    if not admin(m.from_user):return
    if not m.reply_to_message:return await m.reply("Ответь на сообщение.")
    t=m.reply_to_message.from_user;n=int(RX["take"].match(m.text).group(2));await store.ensure(t)
    ok,b=await store.delta(t.id,-n);await m.reply(f"{'✅ Снято' if ok else '❌ Недостаточно на балансе'}: {b}")

@router.message(F.text.regexp(RX["reset"]))
async def reset_balance(m):
    if not admin(m.from_user): return
    if not m.reply_to_message or not m.reply_to_message.from_user:
        return await m.reply("Ответь на сообщение игрока, чей баланс нужно обнулить.")
    t = m.reply_to_message.from_user
    await store.ensure(t)
    current = await store.balance(t.id)
    if current > 0:
        await store.delta(t.id, -current)
    await m.reply(f"🧹 Баланс игрока {uname(t)} обнулён.")

@router.message(F.text.lower()=="пауза")
async def pause(m):
    if admin(m.from_user):await store.set_pause(True);await m.reply("⏸ Новые игры приостановлены.")

@router.message(F.text.lower()=="продолжить")
async def resume(m):
    if admin(m.from_user):await store.set_pause(False);await m.reply("▶️ Игры возобновлены.")

@router.message(F.text.lower()=="стопигры")
async def stopgames(m):
    if admin(m.from_user):
        c,s=await store.stop_all();await m.reply(f"🛑 Завершено игр: {c}. Возвращено ставок: {s}.")

@router.message(F.text.regexp(RX["dice"]))
async def dice(m):
    if not await available(m):return
    bet=int(RX["dice"].match(m.text).group(2));x=random.randint(1,6);p=payout_for_steps(bet,1) if x>=4 else 0
    ok,b=await store.bet(m.from_user,bet,p)
    await m.reply(f"{'🎲 Выпало '+str(x)+'. Плюс 25%. Начислено '+str(p) if ok and p else '❌ Проигрыш' if ok else '❌ Недостаточно баллов'}. Баланс: {b}")

@router.message(F.text.regexp(RX["coin"]))
async def coin(m):
    if not await available(m):return
    q=RX["coin"].match(m.text);bet=int(q.group(2));choice=q.group(3).lower().replace("ё","е")
    x=random.choice(["орел","решка"]);p=payout_for_steps(bet,1) if choice==x else 0;ok,b=await store.bet(m.from_user,bet,p)
    await m.reply(f"🪙 Выпало: {x}. {'Плюс 25%. Начислено '+str(p) if ok and p else 'Проигрыш' if ok else 'Недостаточно баллов'}. Баланс: {b}")

@router.message(F.text.regexp(RX["roulette"]))
async def roulette(m):
    if not await available(m):return
    q=RX["roulette"].match(m.text);bet=int(q.group(2));c=q.group(3).lower().replace("ё","е")
    c="красное" if c in ("к","красное") else "черное" if c in ("ч","черное") else "зеленое"
    n=random.randint(0,36);x="зеленое" if n==0 else ("красное" if n%2 else "черное")
    p=payout_for_steps(bet,1) if c==x else 0;ok,b=await store.bet(m.from_user,bet,p)
    await m.reply(f"🎡 {n}, {x}. {'Плюс 25%. Начислено '+str(p) if ok and p else 'Проигрыш' if ok else 'Недостаточно баллов'}. Баланс: {b}")

@router.message(F.text.regexp(RX["mines"]))
async def mines(m):
    if not await available(m):return
    bet=int(RX["mines"].match(m.text).group(2));gid=secrets.token_hex(4);danger=set(random.sample(range(16),random.randint(4,6)))
    p=await m.reply("Создаю поле…");ok,b=await store.new_game(gid,p,m.from_user,"mines",bet,bet,",".join(map(str,danger)))
    if not ok:return await p.edit_text(f"❌ Недостаточно баллов. Баланс: {b}")
    await p.edit_text(f"💣 Мины 4×4\nСтавка: {bet}\nМин: {len(danger)}\nБаланс: {b}",reply_markup=mines_kb(gid,payout=bet))

@router.message(F.text.regexp(RX["joker"]))
async def joker(m):
    if not await available(m):return
    bet=int(RX["joker"].match(m.text).group(2))
    gid=secrets.token_hex(4)

    first_skull=random.randrange(3)
    danger={first_skull}

    p=await m.reply("Перемешиваю карты…")
    ok,b=await store.new_game(
        gid,p,m.from_user,"joker",bet,bet,",".join(map(str,danger))
    )
    if not ok:
        return await p.edit_text(f"❌ Недостаточно баллов. Баланс: {b}")

    await p.edit_text(
        f"🃏 Джокер\n"
        f"Ставка: {bet}\n"
        f"Выбери одну из трёх карт.\n"
        f"Текущий выигрыш: {bet}\n"
        f"Баланс: {b}",
        reply_markup=joker_kb(gid,danger,set(),False,bet)
    )

@router.message(F.text.regexp(RX["duel"]))
async def duel(m):
    if not await available(m):return
    if not m.reply_to_message or not m.reply_to_message.from_user:return await m.reply("Ответь на сообщение соперника.")
    t=m.reply_to_message.from_user
    if t.id==m.from_user.id or t.is_bot:return await m.reply("Нельзя вызвать этого пользователя.")
    bet=int(RX["duel"].match(m.text).group(2));gid=secrets.token_hex(4);p=await m.reply("Создаю дуэль…")
    ok,b=await store.new_game(gid,p,m.from_user,"duel",bet,bet*2,"",t.id)
    if not ok:return await p.edit_text(f"❌ Недостаточно баллов. Баланс: {b}")
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚔️ Принять",callback_data=f"d:{gid}"),InlineKeyboardButton(text="❌ Отказ",callback_data=f"r:{gid}")]])
    await p.edit_text(f"⚔️ {uname(m.from_user)} вызывает {uname(t)}\nСтавка каждого: {bet}",reply_markup=kb)

@router.callback_query(F.data.startswith("m:"))
async def mclick(c):
    _,gid,s=c.data.split(":");g=await store.game(gid)
    if not g or g[10]!="active":return await c.answer("Игра завершена.",show_alert=True)
    if c.from_user.id!=g[3]:return await c.answer("Это игра другого игрока.",show_alert=True)
    danger=set(map(int,g[8].split(",")));opened=set(map(int,g[9].split(","))) if g[9] else set();cell=int(s)
    if cell in opened:return await c.answer("Уже открыто.")
    opened.add(cell)
    if cell in danger:
        await store.update_game(gid,opened=",".join(map(str,opened)),status="lost")
        return await c.message.edit_text(f"💥 Мина! Баланс: {await store.balance(g[3])}",reply_markup=mines_kb(gid,opened,danger,True))
    steps=len(opened);payout=payout_for_steps(g[6],steps);await store.update_game(gid,opened=",".join(map(str,opened)),payout=payout)
    await c.message.edit_text(f"✅ Безопасно. Плюс {percent_for_steps(steps)}%\nТекущий выигрыш: {payout}",reply_markup=mines_kb(gid,opened,danger,False,payout))

@router.callback_query(F.data.startswith("cash:"))
async def cash(c):
    gid=c.data.split(":")[1];g=await store.game(gid)
    if not g or g[10]!="active" or c.from_user.id!=g[3]:return await c.answer("Недоступно.",show_alert=True)
    await store.update_game(gid,status="won");_,b=await store.delta(g[3],g[7])
    danger=set(map(int,g[8].split(",")));opened=set(map(int,g[9].split(","))) if g[9] else set()
    await c.message.edit_text(f"💰 Начислено {g[7]}. Баланс: {b}",reply_markup=mines_kb(gid,opened,danger,True))

@router.callback_query(F.data.startswith("j:"))
async def jclick(c):
    _,gid,s=c.data.split(":")
    g=await store.game(gid)

    if not g or g[10]!="active" or c.from_user.id!=g[3]:
        return await c.answer("Недоступно.",show_alert=True)

    cell=int(s)
    danger=set(map(int,g[8].split(","))) if g[8] else set()
    opened=set(map(int,g[9].split(","))) if g[9] else set()

    current_stage=cell//3
    expected_stage=len(opened)
    if current_stage != expected_stage:
        return await c.answer("Этот ряд уже неактивен.",show_alert=True)

    opened.add(cell)

    if cell in danger:
        await store.update_game(
            gid,
            status="lost",
            opened=",".join(map(str,sorted(opened)))
        )
        b=await store.balance(g[3])
        return await c.message.edit_text(
            f"💀 Череп! Ставка и незабранный выигрыш потеряны.\n"
            f"Баланс: {b}",
            reply_markup=joker_kb(gid,danger,opened,True,0)
        )

    steps=len(opened)
    payout=payout_for_steps(g[6],steps)

    next_stage=steps
    next_cells=list(range(next_stage*3,next_stage*3+3))
    skull_count=2 if steps>=4 else 1
    danger.update(random.sample(next_cells,skull_count))

    await store.update_game(
        gid,
        opened=",".join(map(str,sorted(opened))),
        danger=",".join(map(str,sorted(danger))),
        payout=payout
    )

    difficulty="1 джокер и 2 черепа" if steps>=4 else "2 джокера и 1 череп"
    await c.message.edit_text(
        f"🃏 Джокер! Плюс {percent_for_steps(steps)}%\n"
        f"Текущий выигрыш: {payout}\n"
        f"Новый ряд: {difficulty}.",
        reply_markup=joker_kb(gid,danger,opened,False,payout)
    )
    await c.answer(f"Плюс {percent_for_steps(steps)}%")

@router.callback_query(F.data.startswith("jcash:"))
async def joker_cash(c):
    gid=c.data.split(":")[1];g=await store.game(gid)
    if not g or g[10]!="active" or c.from_user.id!=g[3]:
        return await c.answer("Недоступно.",show_alert=True)
    danger=set(map(int,g[8].split(","))) if g[8] else set()
    opened=set(map(int,g[9].split(","))) if g[9] else set()
    await store.update_game(gid,status="won")
    _,b=await store.delta(g[3],g[7])
    await c.message.edit_text(
        f"💰 Выигрыш забран: {g[7]}\nБаланс: {b}",
        reply_markup=joker_kb(gid,danger,opened,True,g[7])
    )

@router.callback_query(F.data.startswith("d:"))
async def daccept(c):
    gid=c.data.split(":")[1];g=await store.game(gid)
    if not g or g[10]!="active":return await c.answer("Дуэль завершена.",show_alert=True)
    if c.from_user.id!=g[4]:return await c.answer("Принять может только вызванный игрок.",show_alert=True)
    await store.ensure(c.from_user)
    if await store.balance(c.from_user.id)<g[6]:return await c.answer("Недостаточно баллов.",show_alert=True)
    await store.delta(c.from_user.id,-g[6])
    a=b=0
    while a==b:a,b=random.randint(1,6),random.randint(1,6)
    winner=g[3] if a>b else g[4];_,bal=await store.delta(winner,g[6]*2)
    await store.update_game(gid,status="won",meta=f"{a},{b},{winner}")
    await c.message.edit_text(f"⚔️ Дуэль: {a} против {b}\nПобедитель получает {g[6]*2}. Его баланс: {bal}")

@router.callback_query(F.data.startswith("r:"))
async def decline(c):
    gid=c.data.split(":")[1];g=await store.game(gid)
    if not g or g[10]!="active" or c.from_user.id!=g[4]:return await c.answer("Недоступно.",show_alert=True)
    await store.update_game(gid,status="cancelled");await store.delta(g[3],g[6])
    await c.message.edit_text("❌ Дуэль отклонена. Ставка возвращена.")

@router.callback_query(F.data=="x")
async def x(c): await c.answer("Кнопка неактивна.")

async def main():
    if not TOKEN: raise RuntimeError("Не найден BOT_TOKEN")
    await store.init()
    bot=Bot(TOKEN);dp=Dispatcher();dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start",description="Запустить"),
        BotCommand(command="help",description="Команды игроков"),
        BotCommand(command="adminhelp",description="Команды администратора"),
    ])
    await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
