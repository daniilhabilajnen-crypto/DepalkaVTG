
import asyncio
import json
import logging
import math
import os
import random
import re
import secrets
from datetime import datetime, timezone

import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

START_BALANCE = 2500
DAILY_BONUS = 500
ADMIN_USERNAME = "some_randomuser"
GOLDEN_MINES_CHANCE = 0.005

router = Router()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

RX = {
    "balance": re.compile(r"^(б|баланс)$", re.I),
    "profile": re.compile(r"^(профиль|profile)$", re.I),
    "transfer": re.compile(r"^(п|перевод)\s+(\d+)$", re.I),
    "bonus": re.compile(r"^(бонус|bonus)$", re.I),
    "quests": re.compile(r"^(задания|квесты|quests)$", re.I),
    "mines": re.compile(r"^(мины|mines)\s+(\d+)$", re.I),
    "joker": re.compile(r"^(джокер|joker)\s+(\d+)$", re.I),
    "dice": re.compile(r"^(кости|кубик|dice)\s+(\d+)$", re.I),
    "coin": re.compile(r"^(монета|coin)\s+(\d+)\s+(орел|орёл|решка)$", re.I),
    "roulette": re.compile(
        r"^(рулетка|roulette)\s+(\d+)\s+"
        r"(красное|черное|чёрное|зеленое|зелёное|к|ч|з)$",
        re.I,
    ),
    "duel": re.compile(r"^(дуэль|дуел|duel)\s+(\d+)$", re.I),
    "give": re.compile(r"^(выдать|дать)\s+(\d+)$", re.I),
    "take": re.compile(r"^(снять|забрать)\s+(\d+)$", re.I),
    "reset": re.compile(r"^(обнулить|обнулить счет|обнулить счёт)$", re.I),
    "give_level": re.compile(r"^выдать\s+уров(?:ень|ня)\s+(\d+)$", re.I),
    "take_level": re.compile(r"^забрать\s+уров(?:ень|ня)\s+(\d+)$", re.I),
    "set_level": re.compile(r"^установить\s+уровень\s+(\d+)$", re.I),
    "help": re.compile(r"^(хелп|help)$", re.I),
    "adminhelp": re.compile(r"^(админхелп|adminhelp)$", re.I),
}

GAME_UNLOCK = {
    "mines": 1,
    "joker": 2,
    "dice": 3,
    "coin": 4,
    "roulette": 5,
    "duel": 6,
}

GAME_NAMES = {
    "mines": "💣 Мины",
    "joker": "🃏 Джокер",
    "dice": "🎲 Кости",
    "coin": "🪙 Монета",
    "roulette": "🎡 Рулетка",
    "duel": "⚔️ Дуэль",
}


def is_admin(user) -> bool:
    return bool(
        user
        and user.username
        and user.username.lower() == ADMIN_USERNAME
    )


def display_name(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


def max_bet(level: int) -> int:
    return 1000 + max(0, level - 1) * 250


def xp_required(level: int) -> int:
    return 50 + max(0, level - 1) * 30


def rank_icon(level: int) -> str:
    if level >= 50:
        return "👑"
    if level >= 30:
        return "💎"
    if level >= 20:
        return "🥇"
    if level >= 10:
        return "🥈"
    return "🥉"


def payout_25(bet: int, steps: int = 1) -> int:
    return bet + (bet * 25 * steps) // 100


def xp_for_win(bet: int, profit: int) -> int:
    return max(8, min(300, 8 + bet // 80 + max(0, profit) // 50))


def utc_date():
    return datetime.now(timezone.utc).date()


def daily_quests() -> list[dict]:
    roll = random.random()
    if roll < 0.55:
        spend_target = random.randint(100, 1000)
    elif roll < 0.82:
        spend_target = random.randint(1001, 3000)
    elif roll < 0.95:
        spend_target = random.randint(3001, 6000)
    else:
        spend_target = random.randint(6001, 10000)

    return [
        {
            "id": "play",
            "title": "Сыграй 5 игр",
            "target": 5,
            "progress": 0,
            "reward": 250,
            "done": False,
        },
        {
            "id": "win",
            "title": "Выиграй 3 раза",
            "target": 3,
            "progress": 0,
            "reward": 350,
            "done": False,
        },
        {
            "id": "transfer",
            "title": "Переведи баллы другу",
            "target": 1,
            "progress": 0,
            "reward": 200,
            "done": False,
        },
        {
            "id": "spend",
            "title": f"Потрать {spend_target} баллов на ставки",
            "target": spend_target,
            "progress": 0,
            "reward": 100 + spend_target // 4,
            "done": False,
        },
    ]


class Store:
    def __init__(self, url: str):
        self.url = url
        self.pool: asyncpg.Pool | None = None

    def require_pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL pool is not initialized")
        return self.pool

    async def init(self) -> None:
        self.pool = await asyncpg.create_pool(
            self.url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    balance BIGINT NOT NULL DEFAULT 2500,
                    last_bonus DATE,
                    level INTEGER NOT NULL DEFAULT 1,
                    xp INTEGER NOT NULL DEFAULT 0,
                    games_played INTEGER NOT NULL DEFAULT 0,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    biggest_win BIGINT NOT NULL DEFAULT 0,
                    biggest_loss BIGINT NOT NULL DEFAULT 0,
                    win_streak INTEGER NOT NULL DEFAULT 0,
                    loss_streak INTEGER NOT NULL DEFAULT 0,
                    best_win_streak INTEGER NOT NULL DEFAULT 0,
                    level_rewards_claimed JSONB NOT NULL DEFAULT '[]'::jsonb,
                    quest_date DATE,
                    quests_json JSONB
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    message_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    opponent_id BIGINT NOT NULL DEFAULT 0,
                    game_type TEXT NOT NULL,
                    bet BIGINT NOT NULL,
                    payout BIGINT NOT NULL,
                    danger JSONB NOT NULL DEFAULT '[]'::jsonb,
                    opened JSONB NOT NULL DEFAULT '[]'::jsonb,
                    status TEXT NOT NULL DEFAULT 'active',
                    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            await conn.execute("""
                INSERT INTO settings(key, value)
                VALUES ('paused', '0')
                ON CONFLICT (key) DO NOTHING
            """)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def ensure_user(self, user) -> None:
        pool = self.require_pool()
        await pool.execute("""
            INSERT INTO users(user_id, username, full_name, balance)
            VALUES($1, $2, $3, $4)
            ON CONFLICT(user_id) DO UPDATE SET
                username = EXCLUDED.username,
                full_name = EXCLUDED.full_name
        """, user.id, user.username, user.full_name, START_BALANCE)

    async def get_user(self, user_id: int):
        return await self.require_pool().fetchrow(
            "SELECT * FROM users WHERE user_id=$1",
            user_id,
        )

    async def get_balance(self, user_id: int) -> int:
        value = await self.require_pool().fetchval(
            "SELECT balance FROM users WHERE user_id=$1",
            user_id,
        )
        return int(value or 0)

    async def change_balance(
        self,
        user_id: int,
        delta: int,
    ) -> tuple[bool, int]:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchval(
                    "SELECT balance FROM users WHERE user_id=$1 FOR UPDATE",
                    user_id,
                )
                if current is None:
                    return False, 0
                new_balance = int(current) + delta
                if new_balance < 0:
                    return False, int(current)
                await conn.execute(
                    "UPDATE users SET balance=$1 WHERE user_id=$2",
                    new_balance,
                    user_id,
                )
                return True, new_balance

    async def set_balance_zero(self, user_id: int) -> None:
        await self.require_pool().execute(
            "UPDATE users SET balance=0 WHERE user_id=$1",
            user_id,
        )

    async def change_level(
        self,
        user_id: int,
        delta: int,
    ) -> tuple[int, int]:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT level FROM users WHERE user_id=$1 FOR UPDATE",
                    user_id,
                )
                current = int(row["level"])
                new_level = max(1, current + delta)
                await conn.execute(
                    "UPDATE users SET level=$1, xp=0 WHERE user_id=$2",
                    new_level,
                    user_id,
                )
                return current, new_level

    async def set_level(
        self,
        user_id: int,
        level: int,
    ) -> tuple[int, int]:
        level = max(1, min(level, 10000))
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                old = int(await conn.fetchval(
                    "SELECT level FROM users WHERE user_id=$1 FOR UPDATE",
                    user_id,
                ))
                await conn.execute(
                    "UPDATE users SET level=$1, xp=0 WHERE user_id=$2",
                    level,
                    user_id,
                )
                return old, level

    async def is_paused(self) -> bool:
        value = await self.require_pool().fetchval(
            "SELECT value FROM settings WHERE key='paused'"
        )
        return value == "1"

    async def set_paused(self, paused: bool) -> None:
        await self.require_pool().execute(
            "UPDATE settings SET value=$1 WHERE key='paused'",
            "1" if paused else "0",
        )

    async def claim_bonus(self, user) -> tuple[bool, int]:
        await self.ensure_user(user)
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT balance,last_bonus FROM users "
                    "WHERE user_id=$1 FOR UPDATE",
                    user.id,
                )
                if row["last_bonus"] == utc_date():
                    return False, int(row["balance"])
                new_balance = int(row["balance"]) + DAILY_BONUS
                await conn.execute(
                    "UPDATE users SET balance=$1,last_bonus=$2 "
                    "WHERE user_id=$3",
                    new_balance,
                    utc_date(),
                    user.id,
                )
                return True, new_balance

    async def transfer(
        self,
        sender,
        recipient,
        amount: int,
    ) -> tuple[bool, int, int]:
        await self.ensure_user(sender)
        await self.ensure_user(recipient)
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                sender_balance = await conn.fetchval(
                    "SELECT balance FROM users "
                    "WHERE user_id=$1 FOR UPDATE",
                    sender.id,
                )
                if int(sender_balance) < amount:
                    recipient_balance = await conn.fetchval(
                        "SELECT balance FROM users WHERE user_id=$1",
                        recipient.id,
                    )
                    return False, int(sender_balance), int(recipient_balance)

                await conn.execute(
                    "UPDATE users SET balance=balance-$1 WHERE user_id=$2",
                    amount,
                    sender.id,
                )
                await conn.execute(
                    "UPDATE users SET balance=balance+$1 WHERE user_id=$2",
                    amount,
                    recipient.id,
                )
                new_sender = int(sender_balance) - amount
                new_recipient = await conn.fetchval(
                    "SELECT balance FROM users WHERE user_id=$1",
                    recipient.id,
                )

        await self.quest_progress(sender.id, "transfer", 1)
        return True, new_sender, int(new_recipient)

    async def ensure_quests(self, user_id: int) -> list[dict]:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT quest_date,quests_json FROM users "
                    "WHERE user_id=$1 FOR UPDATE",
                    user_id,
                )
                if row["quest_date"] != utc_date() or row["quests_json"] is None:
                    quests = daily_quests()
                    await conn.execute(
                        "UPDATE users SET quest_date=$1,quests_json=$2::jsonb "
                        "WHERE user_id=$3",
                        utc_date(),
                        json.dumps(quests, ensure_ascii=False),
                        user_id,
                    )
                    return quests
                return list(row["quests_json"])

    async def quest_progress(
        self,
        user_id: int,
        quest_id: str,
        amount: int,
    ) -> int:
        quests = await self.ensure_quests(user_id)
        reward = 0
        changed = False

        for quest in quests:
            if quest["id"] == quest_id and not quest["done"]:
                quest["progress"] = min(
                    quest["target"],
                    quest["progress"] + amount,
                )
                changed = True
                if quest["progress"] >= quest["target"]:
                    quest["done"] = True
                    reward += quest["reward"]

        if changed:
            await self.require_pool().execute(
                "UPDATE users SET quests_json=$1::jsonb,"
                "balance=balance+$2 WHERE user_id=$3",
                json.dumps(quests, ensure_ascii=False),
                reward,
                user_id,
            )
        return reward

    async def validate_bet(
        self,
        user,
        game_type: str,
        bet: int,
    ) -> tuple[bool, str]:
        await self.ensure_user(user)
        row = await self.get_user(user.id)
        level = int(row["level"])

        required_level = GAME_UNLOCK[game_type]
        if level < required_level:
            return False, (
                f"🔒 {GAME_NAMES[game_type]} открывается "
                f"на {required_level} уровне."
            )
        if bet < 1:
            return False, "Минимальная ставка: 1."
        if bet > max_bet(level):
            return False, (
                "❌ Ставка выше лимита уровня.\n"
                f"{rank_icon(level)} Уровень: {level}\n"
                f"💰 Максимальная ставка: {max_bet(level)}"
            )
        if int(row["balance"]) < bet:
            return False, (
                f"❌ Недостаточно баллов. Баланс: {row['balance']}"
            )
        return True, ""

    async def simple_game(
        self,
        user,
        game_type: str,
        bet: int,
        payout: int,
    ) -> tuple[bool, str]:
        ok, error = await self.validate_bet(user, game_type, bet)
        if not ok:
            return False, error

        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                current = await conn.fetchval(
                    "SELECT balance FROM users "
                    "WHERE user_id=$1 FOR UPDATE",
                    user.id,
                )
                new_balance = int(current) - bet + payout
                await conn.execute(
                    "UPDATE users SET balance=$1 WHERE user_id=$2",
                    new_balance,
                    user.id,
                )

        await self.quest_progress(user.id, "play", 1)
        await self.quest_progress(user.id, "spend", bet)

        if payout:
            await self.record_result(user.id, True, bet, payout)
            await self.quest_progress(user.id, "win", 1)
        else:
            await self.record_result(user.id, False, bet, 0)
        return True, ""

    async def create_game(
        self,
        game_id: str,
        message: Message,
        user,
        game_type: str,
        bet: int,
        payout: int,
        danger: set[int],
        opponent_id: int = 0,
        meta: dict | None = None,
    ) -> tuple[bool, str]:
        ok, error = await self.validate_bet(user, game_type, bet)
        if not ok:
            return False, error

        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                balance = await conn.fetchval(
                    "SELECT balance FROM users "
                    "WHERE user_id=$1 FOR UPDATE",
                    user.id,
                )
                if int(balance) < bet:
                    return False, "Недостаточно баллов."
                await conn.execute(
                    "UPDATE users SET balance=balance-$1 WHERE user_id=$2",
                    bet,
                    user.id,
                )
                await conn.execute("""
                    INSERT INTO games(
                        game_id,chat_id,message_id,user_id,opponent_id,
                        game_type,bet,payout,danger,opened,status,meta
                    )
                    VALUES(
                        $1,$2,$3,$4,$5,$6,$7,$8,
                        $9::jsonb,'[]'::jsonb,'active',$10::jsonb
                    )
                """,
                    game_id,
                    message.chat.id,
                    message.message_id,
                    user.id,
                    opponent_id,
                    game_type,
                    bet,
                    payout,
                    json.dumps(sorted(danger)),
                    json.dumps(meta or {}),
                )

        await self.quest_progress(user.id, "play", 1)
        await self.quest_progress(user.id, "spend", bet)
        return True, ""

    async def get_game(self, game_id: str):
        return await self.require_pool().fetchrow(
            "SELECT * FROM games WHERE game_id=$1",
            game_id,
        )

    async def update_game(self, game_id: str, **fields) -> None:
        if not fields:
            return
        values = []
        assignments = []
        index = 1
        json_fields = {"danger", "opened", "meta"}

        for name, value in fields.items():
            if name in json_fields:
                assignments.append(f"{name}=${index}::jsonb")
                value = json.dumps(value)
            else:
                assignments.append(f"{name}=${index}")
            values.append(value)
            index += 1

        values.append(game_id)
        await self.require_pool().execute(
            f"UPDATE games SET {','.join(assignments)} "
            f"WHERE game_id=${index}",
            *values,
        )

    async def record_result(
        self,
        user_id: int,
        won: bool,
        bet: int,
        payout: int,
    ) -> int:
        profit = max(0, payout - bet)
        xp_gain = xp_for_win(bet, profit) if won else 0
        pool = self.require_pool()

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT * FROM users WHERE user_id=$1 FOR UPDATE",
                    user_id,
                )

                level = int(row["level"])
                xp = int(row["xp"]) + xp_gain
                claimed = list(row["level_rewards_claimed"] or [])
                level_reward = 0

                while xp >= xp_required(level):
                    xp -= xp_required(level)
                    level += 1
                    if level % 5 == 0 and level not in claimed:
                        reward = 500 + ((level // 5) - 1) * 100
                        level_reward += reward
                        claimed.append(level)

                if won:
                    wins = int(row["wins"]) + 1
                    losses = int(row["losses"])
                    biggest_win = max(int(row["biggest_win"]), profit)
                    biggest_loss = int(row["biggest_loss"])
                    win_streak = int(row["win_streak"]) + 1
                    loss_streak = 0
                    best_streak = max(
                        int(row["best_win_streak"]),
                        win_streak,
                    )
                else:
                    wins = int(row["wins"])
                    losses = int(row["losses"]) + 1
                    biggest_win = int(row["biggest_win"])
                    biggest_loss = max(int(row["biggest_loss"]), bet)
                    win_streak = 0
                    loss_streak = int(row["loss_streak"]) + 1
                    best_streak = int(row["best_win_streak"])

                await conn.execute("""
                    UPDATE users SET
                        balance=balance+$1,
                        level=$2,
                        xp=$3,
                        games_played=games_played+1,
                        wins=$4,
                        losses=$5,
                        biggest_win=$6,
                        biggest_loss=$7,
                        win_streak=$8,
                        loss_streak=$9,
                        best_win_streak=$10,
                        level_rewards_claimed=$11::jsonb
                    WHERE user_id=$12
                """,
                    level_reward,
                    level,
                    xp,
                    wins,
                    losses,
                    biggest_win,
                    biggest_loss,
                    win_streak,
                    loss_streak,
                    best_streak,
                    json.dumps(claimed),
                    user_id,
                )
                return level_reward

    async def stop_user_games(
        self,
        user_id: int,
    ) -> tuple[int, int]:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    "SELECT bet FROM games "
                    "WHERE user_id=$1 AND status='active' FOR UPDATE",
                    user_id,
                )
                refund = sum(int(row["bet"]) for row in rows)
                if refund:
                    await conn.execute(
                        "UPDATE users SET balance=balance+$1 "
                        "WHERE user_id=$2",
                        refund,
                        user_id,
                    )
                    await conn.execute(
                        "UPDATE games SET status='cancelled' "
                        "WHERE user_id=$1 AND status='active'",
                        user_id,
                    )
                return len(rows), refund

    async def stop_all_games(self) -> tuple[int, int]:
        pool = self.require_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    "SELECT user_id,bet FROM games "
                    "WHERE status='active' FOR UPDATE"
                )
                for row in rows:
                    await conn.execute(
                        "UPDATE users SET balance=balance+$1 "
                        "WHERE user_id=$2",
                        int(row["bet"]),
                        int(row["user_id"]),
                    )
                await conn.execute(
                    "UPDATE games SET status='cancelled' "
                    "WHERE status='active'"
                )
                return len(rows), sum(int(row["bet"]) for row in rows)


store = Store(DATABASE_URL)


async def games_available(message: Message) -> bool:
    if await store.is_paused():
        await message.reply(
            "⏸ Игры временно приостановлены администратором."
        )
        return False
    return True


def profile_text(row) -> str:
    level = int(row["level"])
    return (
        "╔══════════════╗\n"
        "👤 <b>ПРОФИЛЬ ИГРОКА</b>\n"
        "╚══════════════╝\n\n"
        f"{rank_icon(level)} Уровень: <b>{level}</b>\n"
        f"⭐ Опыт: <b>{row['xp']} / {xp_required(level)}</b>\n"
        f"💰 Баланс: <b>{row['balance']}</b>\n"
        f"🎯 Максимальная ставка: <b>{max_bet(level)}</b>\n\n"
        f"🎮 Сыграно: <b>{row['games_played']}</b>\n"
        f"🏆 Побед: <b>{row['wins']}</b>\n"
        f"💀 Поражений: <b>{row['losses']}</b>\n"
        f"📈 Самый большой выигрыш: <b>+{row['biggest_win']}</b>\n"
        f"📉 Самый большой проигрыш: <b>-{row['biggest_loss']}</b>\n"
        f"🔥 Лучшая серия побед: <b>{row['best_win_streak']}</b>"
    )


PLAYER_HELP = """╔══════════════╗
🎮 <b>КОМАНДЫ ИГРОКОВ</b>
╚══════════════╝

<code>профиль</code> — профиль игрока
<code>б</code> — баланс
Ответ + <code>п 500</code> — перевод
<code>бонус</code> — ежедневный бонус
<code>задания</code> — ежедневные задания
<code>мины 100</code> — уровень 1
<code>джокер 100</code> — уровень 2
<code>кости 100</code> — уровень 3
<code>монета 100 орёл</code> — уровень 4
<code>рулетка 100 красное</code> — уровень 5
Ответ + <code>дуэль 100</code> — уровень 6
<code>стопигры</code> — остановить свои игры

Максимальная ставка на 1 уровне: 1000.
За каждый уровень лимит увеличивается на 250."""

ADMIN_HELP = """🛡 <b>КОМАНДЫ АДМИНИСТРАТОРА</b>

Ответ + <code>выдать 500</code>
Ответ + <code>снять 500</code>
Ответ + <code>обнулить</code>

Управление уровнями:
Ответ + <code>выдать уровень 3</code>
Ответ + <code>забрать уровень 2</code>
Ответ + <code>установить уровень 10</code>

Управление играми:
<code>пауза</code>
<code>продолжить</code>
<code>стопигры</code> — остановить игры всех игроков"""


def mines_keyboard(
    game_id: str,
    opened: set[int],
    danger: set[int],
    payout: int,
    done: bool = False,
    golden: bool = False,
):
    rows = []
    for start in range(0, 16, 4):
        row = []
        for cell in range(start, start + 4):
            if done:
                if cell in danger:
                    text = "💥"
                elif cell in opened:
                    text = "✨" if golden else "✅"
                else:
                    text = "▫️"
                data = "noop"
            elif cell in opened:
                text = "✨" if golden else "✅"
                data = "noop"
            else:
                text = "🟨" if golden else "▫️"
                data = f"mine:{game_id}:{cell}"
            row.append(
                InlineKeyboardButton(text=text, callback_data=data)
            )
        rows.append(row)

    if not done:
        rows.append([
            InlineKeyboardButton(
                text=f"💰 Забрать {payout}",
                callback_data=f"cash:{game_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def joker_keyboard(
    game_id: str,
    danger: set[int],
    opened: set[int],
    payout: int,
    done: bool = False,
):
    rows = []
    stages = max(
        1,
        (max(danger | opened) // 3 + 1)
        if danger or opened
        else 1,
    )
    active_stage = stages - 1

    for stage in range(stages):
        row = []
        cells = range(stage * 3, stage * 3 + 3)
        selected = next((cell for cell in cells if cell in opened), None)

        for cell in cells:
            if done:
                if cell in opened and cell in danger:
                    text = "💀"
                elif cell in opened:
                    text = "🃏"
                else:
                    text = "🂠"
                data = "noop"
            elif stage < active_stage:
                text = "🃏" if cell == selected else "🂠"
                data = "noop"
            else:
                text = "🂠"
                data = f"joker:{game_id}:{cell}"

            row.append(
                InlineKeyboardButton(text=text, callback_data=data)
            )
        rows.append(row)

    if not done:
        rows.append([
            InlineKeyboardButton(
                text=f"💰 Забрать {payout}",
                callback_data=f"jcash:{game_id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
@router.message(Command("help"))
@router.message(F.text.regexp(RX["help"]))
async def help_handler(message: Message):
    await store.ensure_user(message.from_user)
    await message.answer(PLAYER_HELP, parse_mode="HTML")


@router.message(Command("adminhelp"))
@router.message(F.text.regexp(RX["adminhelp"]))
async def admin_help_handler(message: Message):
    if not is_admin(message.from_user):
        return await message.reply(
            "⛔ Команда доступна только администратору."
        )
    await message.answer(ADMIN_HELP, parse_mode="HTML")


@router.message(F.text.regexp(RX["profile"]))
async def profile_handler(message: Message):
    await store.ensure_user(message.from_user)
    row = await store.get_user(message.from_user.id)
    await message.reply(profile_text(row), parse_mode="HTML")


@router.message(F.text.regexp(RX["balance"]))
async def balance_handler(message: Message):
    await store.ensure_user(message.from_user)
    balance = await store.get_balance(message.from_user.id)
    await message.reply(
        f"💰 Баланс: <b>{balance}</b>",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(RX["bonus"]))
async def bonus_handler(message: Message):
    ok, balance = await store.claim_bonus(message.from_user)
    text = (
        "🎁 Получено 500 баллов."
        if ok
        else "⏳ Сегодня бонус уже получен."
    )
    await message.reply(f"{text}\n💰 Баланс: {balance}")


@router.message(F.text.regexp(RX["quests"]))
async def quests_handler(message: Message):
    await store.ensure_user(message.from_user)
    quests = await store.ensure_quests(message.from_user.id)
    lines = [
        "╔══════════════╗",
        "📅 <b>ЕЖЕДНЕВНЫЕ ЗАДАНИЯ</b>",
        "╚══════════════╝",
        "",
    ]
    for quest in quests:
        icon = "✅" if quest["done"] else "▫️"
        lines.append(f"{icon} {quest['title']}")
        lines.append(
            f"   {quest['progress']} / {quest['target']} · "
            f"награда {quest['reward']}"
        )
    await message.reply("\n".join(lines), parse_mode="HTML")


@router.message(F.text.regexp(RX["transfer"]))
async def transfer_handler(message: Message):
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await message.reply("Ответь на сообщение игрока.")

    target = message.reply_to_message.from_user
    if target.is_bot or target.id == message.from_user.id:
        return await message.reply(
            "Нельзя перевести этому пользователю."
        )

    amount = int(RX["transfer"].match(message.text).group(2))
    ok, sender_balance, recipient_balance = await store.transfer(
        message.from_user,
        target,
        amount,
    )

    if not ok:
        return await message.reply(
            f"❌ Недостаточно баллов.\n"
            f"Твой баланс: {sender_balance}"
        )

    await message.reply(
        f"✅ Переведено: {amount}\n"
        f"Твой баланс: {sender_balance}\n"
        f"Баланс получателя: {recipient_balance}"
    )


async def get_admin_target(message: Message):
    if not is_admin(message.from_user):
        return None
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("Ответь на сообщение игрока.")
        return None
    target = message.reply_to_message.from_user
    await store.ensure_user(target)
    return target


@router.message(F.text.regexp(RX["give_level"]))
async def give_level_handler(message: Message):
    target = await get_admin_target(message)
    if not target:
        return
    amount = int(RX["give_level"].match(message.text).group(1))
    old, new = await store.change_level(target.id, amount)
    await message.reply(
        f"⬆️ Уровень игрока {display_name(target)} изменён.\n"
        f"{old} → {new}\n"
        f"Максимальная ставка: {max_bet(new)}"
    )


@router.message(F.text.regexp(RX["take_level"]))
async def take_level_handler(message: Message):
    target = await get_admin_target(message)
    if not target:
        return
    amount = int(RX["take_level"].match(message.text).group(1))
    old, new = await store.change_level(target.id, -amount)
    await message.reply(
        f"⬇️ Уровень игрока {display_name(target)} изменён.\n"
        f"{old} → {new}\n"
        f"Максимальная ставка: {max_bet(new)}"
    )


@router.message(F.text.regexp(RX["set_level"]))
async def set_level_handler(message: Message):
    target = await get_admin_target(message)
    if not target:
        return
    level = int(RX["set_level"].match(message.text).group(1))
    old, new = await store.set_level(target.id, level)
    await message.reply(
        f"🛠 Уровень игрока {display_name(target)} установлен.\n"
        f"{old} → {new}\n"
        f"Максимальная ставка: {max_bet(new)}"
    )


@router.message(F.text.regexp(RX["give"]))
async def give_balance_handler(message: Message):
    target = await get_admin_target(message)
    if not target:
        return
    amount = int(RX["give"].match(message.text).group(2))
    _, balance = await store.change_balance(target.id, amount)
    await message.reply(
        f"✅ Выдано {amount}.\n"
        f"Баланс {display_name(target)}: {balance}"
    )


@router.message(F.text.regexp(RX["take"]))
async def take_balance_handler(message: Message):
    target = await get_admin_target(message)
    if not target:
        return
    amount = int(RX["take"].match(message.text).group(2))
    ok, balance = await store.change_balance(target.id, -amount)
    if not ok:
        return await message.reply(
            f"❌ Недостаточно баллов.\nБаланс: {balance}"
        )
    await message.reply(
        f"✅ Снято {amount}.\n"
        f"Баланс {display_name(target)}: {balance}"
    )


@router.message(F.text.regexp(RX["reset"]))
async def reset_balance_handler(message: Message):
    target = await get_admin_target(message)
    if not target:
        return
    await store.set_balance_zero(target.id)
    await message.reply(
        f"🧹 Баланс {display_name(target)} обнулён."
    )


@router.message(F.text.lower() == "пауза")
async def pause_handler(message: Message):
    if is_admin(message.from_user):
        await store.set_paused(True)
        await message.reply("⏸ Игры приостановлены.")


@router.message(F.text.lower() == "продолжить")
async def resume_handler(message: Message):
    if is_admin(message.from_user):
        await store.set_paused(False)
        await message.reply("▶️ Игры возобновлены.")


@router.message(F.text.lower() == "стопигры")
async def stop_games_handler(message: Message):
    if is_admin(message.from_user):
        count, refund = await store.stop_all_games()
        await message.reply(
            f"🛑 Завершено игр: {count}.\n"
            f"Возвращено ставок: {refund}."
        )
    else:
        count, refund = await store.stop_user_games(
            message.from_user.id
        )
        if not count:
            return await message.reply(
                "ℹ️ У тебя нет активных игр."
            )
        await message.reply(
            f"🛑 Завершено твоих игр: {count}.\n"
            f"Возвращено ставок: {refund}."
        )


@router.message(F.text.regexp(RX["dice"]))
async def dice_handler(message: Message):
    if not await games_available(message):
        return
    bet = int(RX["dice"].match(message.text).group(2))
    roll = random.randint(1, 6)
    payout = payout_25(bet) if roll >= 4 else 0
    ok, error = await store.simple_game(
        message.from_user,
        "dice",
        bet,
        payout,
    )
    if not ok:
        return await message.reply(error)

    await message.reply(
        "╔══════════════╗\n"
        "🎲 <b>КОСТИ</b>\n"
        "╚══════════════╝\n\n"
        f"Выпало: <b>{roll}</b>\n"
        f"{'📈 Плюс 25%' if payout else '💀 Проигрыш'}\n"
        f"💰 Баланс: <b>{await store.get_balance(message.from_user.id)}</b>",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(RX["coin"]))
async def coin_handler(message: Message):
    if not await games_available(message):
        return

    match = RX["coin"].match(message.text)
    bet = int(match.group(2))
    choice = match.group(3).lower().replace("ё", "е")
    result = random.choice(["орел", "решка"])
    payout = payout_25(bet) if result == choice else 0

    ok, error = await store.simple_game(
        message.from_user,
        "coin",
        bet,
        payout,
    )
    if not ok:
        return await message.reply(error)

    await message.reply(
        "╔══════════════╗\n"
        "🪙 <b>МОНЕТА</b>\n"
        "╚══════════════╝\n\n"
        f"Выпало: <b>{result.title()}</b>\n"
        f"{'📈 Плюс 25%' if payout else '💀 Проигрыш'}\n"
        f"💰 Баланс: <b>{await store.get_balance(message.from_user.id)}</b>",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(RX["roulette"]))
async def roulette_handler(message: Message):
    if not await games_available(message):
        return

    match = RX["roulette"].match(message.text)
    bet = int(match.group(2))
    raw = match.group(3).lower().replace("ё", "е")

    if raw in {"к", "красное"}:
        choice = "красное"
    elif raw in {"ч", "черное"}:
        choice = "черное"
    else:
        choice = "зеленое"

    number = random.randint(0, 36)
    result = (
        "зеленое"
        if number == 0
        else ("красное" if number % 2 else "черное")
    )
    payout = payout_25(bet) if result == choice else 0

    ok, error = await store.simple_game(
        message.from_user,
        "roulette",
        bet,
        payout,
    )
    if not ok:
        return await message.reply(error)

    await message.reply(
        "╔══════════════╗\n"
        "🎡 <b>РУЛЕТКА</b>\n"
        "╚══════════════╝\n\n"
        f"Число: <b>{number}</b>\n"
        f"Цвет: <b>{result}</b>\n"
        f"{'📈 Плюс 25%' if payout else '💀 Проигрыш'}\n"
        f"💰 Баланс: <b>{await store.get_balance(message.from_user.id)}</b>",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(RX["mines"]))
async def mines_handler(message: Message):
    if not await games_available(message):
        return

    bet = int(RX["mines"].match(message.text).group(2))
    golden = random.random() < GOLDEN_MINES_CHANCE
    mine_count = random.randint(4, 6)
    danger = set(random.sample(range(16), mine_count))
    game_id = secrets.token_hex(4)

    placeholder = await message.reply("⛏ Подготавливаю поле…")
    ok, error = await store.create_game(
        game_id,
        placeholder,
        message.from_user,
        "mines",
        bet,
        bet,
        danger,
        meta={"golden": golden},
    )
    if not ok:
        return await placeholder.edit_text(error)

    title = "🌟 ЗОЛОТЫЕ МИНЫ 🌟" if golden else "💣 МИНЫ"
    extra = (
        "\n✨ Каждый безопасный выбор: +100%"
        "\n💥 После успеха добавляется ещё одна мина."
        if golden
        else ""
    )

    await placeholder.edit_text(
        "╔══════════════╗\n"
        f"<b>{title}</b>\n"
        "╚══════════════╝\n\n"
        f"💵 Ставка: <b>{bet}</b>\n"
        f"💣 Мин: <b>{mine_count}</b>\n"
        f"💰 Текущий выигрыш: <b>{bet}</b>{extra}",
        parse_mode="HTML",
        reply_markup=mines_keyboard(
            game_id,
            set(),
            danger,
            bet,
            golden=golden,
        ),
    )


@router.callback_query(F.data.startswith("mine:"))
async def mine_click_handler(callback: CallbackQuery):
    _, game_id, raw_cell = callback.data.split(":")
    game = await store.get_game(game_id)

    if not game or game["status"] != "active":
        return await callback.answer(
            "Игра завершена.",
            show_alert=True,
        )
    if callback.from_user.id != game["user_id"]:
        return await callback.answer(
            "Это игра другого игрока.",
            show_alert=True,
        )

    cell = int(raw_cell)
    danger = set(game["danger"])
    opened = set(game["opened"])
    golden = bool(game["meta"].get("golden"))

    if cell in opened:
        return await callback.answer("Клетка уже открыта.")

    opened.add(cell)

    if cell in danger:
        await store.update_game(
            game_id,
            status="lost",
            opened=sorted(opened),
        )
        await store.record_result(
            game["user_id"],
            False,
            game["bet"],
            0,
        )
        await callback.message.edit_text(
            "💥 <b>МИНА!</b>\n\n"
            f"Ставка <b>{game['bet']}</b> проиграна.\n"
            f"💰 Баланс: <b>{await store.get_balance(game['user_id'])}</b>",
            parse_mode="HTML",
            reply_markup=mines_keyboard(
                game_id,
                opened,
                danger,
                0,
                done=True,
                golden=golden,
            ),
        )
        return

    steps = len(opened)

    if golden:
        payout = game["bet"] * (1 + steps)
        free_cells = list(set(range(16)) - opened - danger)
        if free_cells:
            danger.add(random.choice(free_cells))
        percent = steps * 100
    else:
        payout = payout_25(game["bet"], steps)
        percent = steps * 25

    await store.update_game(
        game_id,
        opened=sorted(opened),
        danger=sorted(danger),
        payout=payout,
    )

    await callback.message.edit_text(
        f"{'✨' if golden else '✅'} <b>БЕЗОПАСНО</b>\n\n"
        f"📈 Плюс <b>{percent}%</b>\n"
        f"💰 Можно забрать: <b>{payout}</b>\n"
        f"💣 Мин на поле: <b>{len(danger)}</b>",
        parse_mode="HTML",
        reply_markup=mines_keyboard(
            game_id,
            opened,
            danger,
            payout,
            golden=golden,
        ),
    )


@router.callback_query(F.data.startswith("cash:"))
async def mines_cash_handler(callback: CallbackQuery):
    game_id = callback.data.split(":")[1]
    game = await store.get_game(game_id)

    if (
        not game
        or game["status"] != "active"
        or callback.from_user.id != game["user_id"]
    ):
        return await callback.answer(
            "Недоступно.",
            show_alert=True,
        )

    await store.update_game(game_id, status="won")
    await store.change_balance(game["user_id"], game["payout"])
    level_reward = await store.record_result(
        game["user_id"],
        True,
        game["bet"],
        game["payout"],
    )
    await store.quest_progress(game["user_id"], "win", 1)

    golden = bool(game["meta"].get("golden"))
    extra = (
        f"\n🎁 Награда за уровень: <b>{level_reward}</b>"
        if level_reward
        else ""
    )

    await callback.message.edit_text(
        "💰 <b>ВЫИГРЫШ ЗАБРАН</b>\n\n"
        f"Начислено: <b>{game['payout']}</b>\n"
        f"Баланс: <b>{await store.get_balance(game['user_id'])}</b>"
        f"{extra}",
        parse_mode="HTML",
        reply_markup=mines_keyboard(
            game_id,
            set(game["opened"]),
            set(game["danger"]),
            game["payout"],
            done=True,
            golden=golden,
        ),
    )


@router.message(F.text.regexp(RX["joker"]))
async def joker_handler(message: Message):
    if not await games_available(message):
        return

    bet = int(RX["joker"].match(message.text).group(2))
    danger = {random.randrange(3)}
    game_id = secrets.token_hex(4)
    placeholder = await message.reply("🃏 Перемешиваю карты…")

    ok, error = await store.create_game(
        game_id,
        placeholder,
        message.from_user,
        "joker",
        bet,
        bet,
        danger,
    )
    if not ok:
        return await placeholder.edit_text(error)

    await placeholder.edit_text(
        "╔══════════════╗\n"
        "🃏 <b>ДЖОКЕР</b>\n"
        "╚══════════════╝\n\n"
        f"💵 Ставка: <b>{bet}</b>\n"
        f"💰 Текущий выигрыш: <b>{bet}</b>\n"
        "Выбери одну из трёх карт.",
        parse_mode="HTML",
        reply_markup=joker_keyboard(
            game_id,
            danger,
            set(),
            bet,
        ),
    )


@router.callback_query(F.data.startswith("joker:"))
async def joker_click_handler(callback: CallbackQuery):
    _, game_id, raw_cell = callback.data.split(":")
    game = await store.get_game(game_id)

    if (
        not game
        or game["status"] != "active"
        or callback.from_user.id != game["user_id"]
    ):
        return await callback.answer(
            "Недоступно.",
            show_alert=True,
        )

    cell = int(raw_cell)
    danger = set(game["danger"])
    opened = set(game["opened"])

    if cell // 3 != len(opened):
        return await callback.answer(
            "Этот ряд уже неактивен.",
            show_alert=True,
        )

    opened.add(cell)

    if cell in danger:
        await store.update_game(
            game_id,
            status="lost",
            opened=sorted(opened),
        )
        await store.record_result(
            game["user_id"],
            False,
            game["bet"],
            0,
        )
        await callback.message.edit_text(
            "💀 <b>ЧЕРЕП</b>\n\n"
            "Ставка проиграна.\n"
            f"💰 Баланс: <b>{await store.get_balance(game['user_id'])}</b>",
            parse_mode="HTML",
            reply_markup=joker_keyboard(
                game_id,
                danger,
                opened,
                0,
                done=True,
            ),
        )
        return

    steps = len(opened)
    payout = payout_25(game["bet"], steps)
    next_cells = list(range(steps * 3, steps * 3 + 3))
    skull_count = 2 if steps >= 4 else 1
    danger.update(random.sample(next_cells, skull_count))

    await store.update_game(
        game_id,
        opened=sorted(opened),
        danger=sorted(danger),
        payout=payout,
    )

    await callback.message.edit_text(
        "🃏 <b>ДЖОКЕР!</b>\n\n"
        f"📈 Плюс <b>{steps * 25}%</b>\n"
        f"💰 Можно забрать: <b>{payout}</b>\n"
        "Новый ряд: "
        f"{'1 джокер и 2 черепа' if steps >= 4 else '2 джокера и 1 череп'}.",
        parse_mode="HTML",
        reply_markup=joker_keyboard(
            game_id,
            danger,
            opened,
            payout,
        ),
    )


@router.callback_query(F.data.startswith("jcash:"))
async def joker_cash_handler(callback: CallbackQuery):
    game_id = callback.data.split(":")[1]
    game = await store.get_game(game_id)

    if (
        not game
        or game["status"] != "active"
        or callback.from_user.id != game["user_id"]
    ):
        return await callback.answer(
            "Недоступно.",
            show_alert=True,
        )

    await store.update_game(game_id, status="won")
    await store.change_balance(game["user_id"], game["payout"])
    level_reward = await store.record_result(
        game["user_id"],
        True,
        game["bet"],
        game["payout"],
    )
    await store.quest_progress(game["user_id"], "win", 1)

    extra = (
        f"\n🎁 Награда за уровень: <b>{level_reward}</b>"
        if level_reward
        else ""
    )

    await callback.message.edit_text(
        "💰 <b>ВЫИГРЫШ ЗАБРАН</b>\n\n"
        f"Начислено: <b>{game['payout']}</b>\n"
        f"Баланс: <b>{await store.get_balance(game['user_id'])}</b>"
        f"{extra}",
        parse_mode="HTML",
    )


@router.message(F.text.regexp(RX["duel"]))
async def duel_handler(message: Message):
    if not await games_available(message):
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await message.reply("Ответь на сообщение соперника.")

    opponent = message.reply_to_message.from_user
    if opponent.is_bot or opponent.id == message.from_user.id:
        return await message.reply(
            "Нельзя вызвать этого пользователя."
        )

    bet = int(RX["duel"].match(message.text).group(2))
    game_id = secrets.token_hex(4)
    placeholder = await message.reply("⚔️ Создаю дуэль…")

    ok, error = await store.create_game(
        game_id,
        placeholder,
        message.from_user,
        "duel",
        bet,
        bet * 2,
        set(),
        opponent_id=opponent.id,
    )
    if not ok:
        return await placeholder.edit_text(error)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⚔️ Принять",
            callback_data=f"duel:{game_id}",
        ),
        InlineKeyboardButton(
            text="❌ Отказ",
            callback_data=f"decline:{game_id}",
        ),
    ]])

    await placeholder.edit_text(
        "⚔️ <b>ДУЭЛЬ</b>\n\n"
        f"{display_name(message.from_user)} вызывает "
        f"{display_name(opponent)}\n"
        f"Ставка каждого: <b>{bet}</b>",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("duel:"))
async def duel_accept_handler(callback: CallbackQuery):
    game_id = callback.data.split(":")[1]
    game = await store.get_game(game_id)

    if not game or game["status"] != "active":
        return await callback.answer(
            "Дуэль завершена.",
            show_alert=True,
        )
    if callback.from_user.id != game["opponent_id"]:
        return await callback.answer(
            "Принять может только вызванный игрок.",
            show_alert=True,
        )

    await store.ensure_user(callback.from_user)
    opponent_row = await store.get_user(callback.from_user.id)
    opponent_level = int(opponent_row["level"])

    if opponent_level < GAME_UNLOCK["duel"]:
        return await callback.answer(
            "Дуэли открываются на 6 уровне.",
            show_alert=True,
        )
    if int(opponent_row["balance"]) < game["bet"]:
        return await callback.answer(
            "Недостаточно баллов.",
            show_alert=True,
        )
    if game["bet"] > max_bet(opponent_level):
        return await callback.answer(
            "Ставка выше лимита твоего уровня.",
            show_alert=True,
        )

    await store.change_balance(callback.from_user.id, -game["bet"])
    await store.quest_progress(callback.from_user.id, "play", 1)
    await store.quest_progress(
        callback.from_user.id,
        "spend",
        game["bet"],
    )

    first = second = 0
    while first == second:
        first = random.randint(1, 6)
        second = random.randint(1, 6)

    if first > second:
        winner = game["user_id"]
        loser = callback.from_user.id
    else:
        winner = callback.from_user.id
        loser = game["user_id"]

    pot = game["bet"] * 2
    await store.change_balance(winner, pot)
    await store.record_result(winner, True, game["bet"], pot)
    await store.record_result(loser, False, game["bet"], 0)
    await store.quest_progress(winner, "win", 1)
    await store.update_game(
        game_id,
        status="won",
        meta={
            "first": first,
            "second": second,
            "winner": winner,
        },
    )

    await callback.message.edit_text(
        "⚔️ <b>ДУЭЛЬ ЗАВЕРШЕНА</b>\n\n"
        f"Первый бросок: <b>{first}</b>\n"
        f"Второй бросок: <b>{second}</b>\n"
        f"🏆 Победитель получил: <b>{pot}</b>",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("decline:"))
async def duel_decline_handler(callback: CallbackQuery):
    game_id = callback.data.split(":")[1]
    game = await store.get_game(game_id)

    if (
        not game
        or game["status"] != "active"
        or callback.from_user.id != game["opponent_id"]
    ):
        return await callback.answer(
            "Недоступно.",
            show_alert=True,
        )

    await store.update_game(game_id, status="cancelled")
    await store.change_balance(game["user_id"], game["bet"])
    await callback.message.edit_text(
        "❌ Дуэль отклонена. Ставка возвращена."
    )


@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    await callback.answer("Кнопка неактивна.")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN")
    if not DATABASE_URL:
        raise RuntimeError("Не найден DATABASE_URL")

    await store.init()
    bot = Bot(BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Команды игроков"),
        BotCommand(
            command="adminhelp",
            description="Команды администратора",
        ),
    ])

    logger.info("Bot started with PostgreSQL")
    try:
        await dispatcher.start_polling(bot)
    finally:
        await store.close()


if __name__ == "__main__":
    asyncio.run(main())
