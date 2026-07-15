
import asyncio
import logging
import os
import random
import re
import secrets
from dataclasses import dataclass
from typing import Optional

import aiosqlite
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
DATABASE_PATH = os.getenv("DATABASE_PATH", "game_bot.sqlite3")

START_BALANCE = 2500
MIN_BET = 1
MAX_BET = 1_000_000_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

router = Router()

BALANCE_RE = re.compile(r"^(?:б|баланс)(?:@\w+)?$", re.IGNORECASE)
TRANSFER_RE = re.compile(r"^(?:п|перевод)(?:@\w+)?\s+(\d+)$", re.IGNORECASE)
MINES_RE = re.compile(r"^(?:мины|mines)(?:@\w+)?\s+(\d+)$", re.IGNORECASE)
JOKER_RE = re.compile(r"^(?:джокер|joker)(?:@\w+)?\s+(\d+)$", re.IGNORECASE)


@dataclass(slots=True)
class Game:
    game_id: str
    chat_id: int
    message_id: int
    user_id: int
    game_type: str
    bet: int
    payout: int
    danger_cells: set[int]
    opened: set[int]
    status: str


class Storage:
    def __init__(self, path: str):
        self.path = path
        self.lock = asyncio.Lock()

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT NOT NULL,
                    balance INTEGER NOT NULL DEFAULT 2500,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    game_type TEXT NOT NULL,
                    bet INTEGER NOT NULL,
                    payout INTEGER NOT NULL,
                    danger_cells TEXT NOT NULL,
                    opened TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_game_per_chat_user
                ON games(chat_id, user_id)
                WHERE status = 'active'
            """)
            await db.commit()

    async def ensure_user(self, user_id: int, username: Optional[str], full_name: str) -> int:
        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute("""
                    INSERT INTO users(user_id, username, full_name, balance)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        full_name = excluded.full_name,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, username, full_name, START_BALANCE))
                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (user_id,),
                )
                row = await cur.fetchone()
                await db.commit()
                return int(row[0])

    async def get_balance(self, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else START_BALANCE

    async def transfer(
        self,
        sender_id: int,
        recipient_id: int,
        amount: int,
        sender_username: Optional[str],
        sender_name: str,
        recipient_username: Optional[str],
        recipient_name: str,
    ) -> tuple[bool, str, int, int]:
        if sender_id == recipient_id:
            return False, "Нельзя переводить баллы самому себе.", 0, 0

        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")

                for uid, username, name in (
                    (sender_id, sender_username, sender_name),
                    (recipient_id, recipient_username, recipient_name),
                ):
                    await db.execute("""
                        INSERT INTO users(user_id, username, full_name, balance)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            username = excluded.username,
                            full_name = excluded.full_name,
                            updated_at = CURRENT_TIMESTAMP
                    """, (uid, username, name, START_BALANCE))

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (sender_id,),
                )
                sender_balance = int((await cur.fetchone())[0])

                if sender_balance < amount:
                    await db.rollback()
                    return False, "Недостаточно баллов.", sender_balance, 0

                await db.execute(
                    "UPDATE users SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (amount, sender_id),
                )
                await db.execute(
                    "UPDATE users SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (amount, recipient_id),
                )

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (sender_id,),
                )
                new_sender_balance = int((await cur.fetchone())[0])

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (recipient_id,),
                )
                new_recipient_balance = int((await cur.fetchone())[0])

                await db.commit()
                return True, "Перевод выполнен.", new_sender_balance, new_recipient_balance

    async def create_game(
        self,
        game_id: str,
        chat_id: int,
        message_id: int,
        user_id: int,
        game_type: str,
        bet: int,
        payout: int,
        danger_cells: set[int],
        username: Optional[str],
        full_name: str,
    ) -> tuple[bool, str, int]:
        danger_text = ",".join(str(x) for x in sorted(danger_cells))

        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")

                await db.execute("""
                    INSERT INTO users(user_id, username, full_name, balance)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        full_name = excluded.full_name,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, username, full_name, START_BALANCE))

                cur = await db.execute("""
                    SELECT 1 FROM games
                    WHERE chat_id = ? AND user_id = ? AND status = 'active'
                """, (chat_id, user_id))
                if await cur.fetchone():
                    await db.rollback()
                    return False, "Сначала закончи текущую игру.", 0

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (user_id,),
                )
                balance = int((await cur.fetchone())[0])

                if balance < bet:
                    await db.rollback()
                    return False, f"Недостаточно баллов. Баланс: {balance}", balance

                await db.execute(
                    "UPDATE users SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (bet, user_id),
                )

                await db.execute("""
                    INSERT INTO games(
                        game_id, chat_id, message_id, user_id, game_type,
                        bet, payout, danger_cells, opened, status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'active')
                """, (
                    game_id,
                    chat_id,
                    message_id,
                    user_id,
                    game_type,
                    bet,
                    payout,
                    danger_text,
                ))

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (user_id,),
                )
                new_balance = int((await cur.fetchone())[0])

                await db.commit()
                return True, "Игра создана.", new_balance

    async def get_game(self, game_id: str) -> Optional[Game]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("""
                SELECT game_id, chat_id, message_id, user_id, game_type,
                       bet, payout, danger_cells, opened, status
                FROM games
                WHERE game_id = ?
            """, (game_id,))
            row = await cur.fetchone()

            if not row:
                return None

            return Game(
                game_id=row[0],
                chat_id=int(row[1]),
                message_id=int(row[2]),
                user_id=int(row[3]),
                game_type=row[4],
                bet=int(row[5]),
                payout=int(row[6]),
                danger_cells={int(x) for x in row[7].split(",") if x},
                opened={int(x) for x in row[8].split(",") if x},
                status=row[9],
            )

    async def open_mines_cell(
        self,
        game_id: str,
        cell: int,
    ) -> tuple[str, Optional[Game], int]:
        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")

                cur = await db.execute("""
                    SELECT game_id, chat_id, message_id, user_id, game_type,
                           bet, payout, danger_cells, opened, status
                    FROM games
                    WHERE game_id = ?
                """, (game_id,))
                row = await cur.fetchone()

                if not row:
                    await db.rollback()
                    return "missing", None, 0

                game = Game(
                    game_id=row[0],
                    chat_id=int(row[1]),
                    message_id=int(row[2]),
                    user_id=int(row[3]),
                    game_type=row[4],
                    bet=int(row[5]),
                    payout=int(row[6]),
                    danger_cells={int(x) for x in row[7].split(",") if x},
                    opened={int(x) for x in row[8].split(",") if x},
                    status=row[9],
                )

                if game.status != "active":
                    await db.rollback()
                    return "inactive", game, await self.get_balance(game.user_id)

                if cell in game.opened:
                    await db.rollback()
                    return "already", game, await self.get_balance(game.user_id)

                game.opened.add(cell)
                opened_text = ",".join(str(x) for x in sorted(game.opened))

                if cell in game.danger_cells:
                    game.status = "lost"
                    await db.execute("""
                        UPDATE games
                        SET opened = ?, status = 'lost', updated_at = CURRENT_TIMESTAMP
                        WHERE game_id = ? AND status = 'active'
                    """, (opened_text, game_id))
                    await db.commit()
                    balance = await self.get_balance(game.user_id)
                    return "mine", game, balance

                game.payout *= 2
                await db.execute("""
                    UPDATE games
                    SET opened = ?, payout = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE game_id = ? AND status = 'active'
                """, (opened_text, game.payout, game_id))
                await db.commit()

                balance = await self.get_balance(game.user_id)
                return "safe", game, balance

    async def cashout(self, game_id: str) -> tuple[bool, Optional[Game], int]:
        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")

                cur = await db.execute("""
                    SELECT game_id, chat_id, message_id, user_id, game_type,
                           bet, payout, danger_cells, opened, status
                    FROM games
                    WHERE game_id = ?
                """, (game_id,))
                row = await cur.fetchone()

                if not row:
                    await db.rollback()
                    return False, None, 0

                game = Game(
                    game_id=row[0],
                    chat_id=int(row[1]),
                    message_id=int(row[2]),
                    user_id=int(row[3]),
                    game_type=row[4],
                    bet=int(row[5]),
                    payout=int(row[6]),
                    danger_cells={int(x) for x in row[7].split(",") if x},
                    opened={int(x) for x in row[8].split(",") if x},
                    status=row[9],
                )

                if game.status != "active":
                    await db.rollback()
                    return False, game, await self.get_balance(game.user_id)

                result = await db.execute("""
                    UPDATE games
                    SET status = 'won', updated_at = CURRENT_TIMESTAMP
                    WHERE game_id = ? AND status = 'active'
                """, (game_id,))

                if result.rowcount != 1:
                    await db.rollback()
                    return False, game, await self.get_balance(game.user_id)

                await db.execute("""
                    UPDATE users
                    SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (game.payout, game.user_id))

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (game.user_id,),
                )
                balance = int((await cur.fetchone())[0])

                game.status = "won"
                await db.commit()
                return True, game, balance

    async def choose_joker(
        self,
        game_id: str,
        cell: int,
    ) -> tuple[str, Optional[Game], int]:
        async with self.lock:
            async with aiosqlite.connect(self.path) as db:
                await db.execute("BEGIN IMMEDIATE")

                cur = await db.execute("""
                    SELECT game_id, chat_id, message_id, user_id, game_type,
                           bet, payout, danger_cells, opened, status
                    FROM games
                    WHERE game_id = ?
                """, (game_id,))
                row = await cur.fetchone()

                if not row:
                    await db.rollback()
                    return "missing", None, 0

                game = Game(
                    game_id=row[0],
                    chat_id=int(row[1]),
                    message_id=int(row[2]),
                    user_id=int(row[3]),
                    game_type=row[4],
                    bet=int(row[5]),
                    payout=int(row[6]),
                    danger_cells={int(x) for x in row[7].split(",") if x},
                    opened={int(x) for x in row[8].split(",") if x},
                    status=row[9],
                )

                if game.status != "active":
                    await db.rollback()
                    return "inactive", game, await self.get_balance(game.user_id)

                game.opened = {cell}

                if cell in game.danger_cells:
                    game.status = "lost"
                    await db.execute("""
                        UPDATE games
                        SET opened = ?, status = 'lost', updated_at = CURRENT_TIMESTAMP
                        WHERE game_id = ? AND status = 'active'
                    """, (str(cell), game_id))
                    await db.commit()
                    balance = await self.get_balance(game.user_id)
                    return "skull", game, balance

                game.status = "won"
                game.payout = game.bet * 2

                await db.execute("""
                    UPDATE games
                    SET opened = ?, payout = ?, status = 'won', updated_at = CURRENT_TIMESTAMP
                    WHERE game_id = ? AND status = 'active'
                """, (str(cell), game.payout, game_id))

                await db.execute("""
                    UPDATE users
                    SET balance = balance + ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                """, (game.payout, game.user_id))

                cur = await db.execute(
                    "SELECT balance FROM users WHERE user_id = ?",
                    (game.user_id,),
                )
                balance = int((await cur.fetchone())[0])

                await db.commit()
                return "joker", game, balance


storage = Storage(DATABASE_PATH)


def user_name(user) -> str:
    return f"@{user.username}" if user.username else user.full_name


def parse_amount(raw: str) -> tuple[bool, int, str]:
    try:
        amount = int(raw)
    except ValueError:
        return False, 0, "Сумма должна быть целым числом."

    if amount < MIN_BET:
        return False, 0, f"Минимальная сумма: {MIN_BET}."
    if amount > MAX_BET:
        return False, 0, f"Максимальная сумма: {MAX_BET}."

    return True, amount, ""


def mines_keyboard(game: Game, reveal_all: bool = False) -> InlineKeyboardMarkup:
    rows = []

    for row_start in range(0, 16, 4):
        row = []
        for cell in range(row_start, row_start + 4):
            if reveal_all:
                if cell in game.danger_cells:
                    text = "💣"
                elif cell in game.opened:
                    text = "✅"
                else:
                    text = "▫️"
                callback_data = "noop"
            elif cell in game.opened:
                text = "✅"
                callback_data = "noop"
            else:
                text = "▫️"
                callback_data = f"mn:{game.game_id}:{cell}"

            row.append(
                InlineKeyboardButton(
                    text=text,
                    callback_data=callback_data,
                )
            )
        rows.append(row)

    if game.status == "active":
        rows.append([
            InlineKeyboardButton(
                text=f"💰 Забрать {game.payout}",
                callback_data=f"cash:{game.game_id}",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def joker_keyboard(game: Game, reveal_all: bool = False) -> InlineKeyboardMarkup:
    row = []

    for cell in range(3):
        if reveal_all:
            text = "💀" if cell in game.danger_cells else "🃏"
            callback_data = "noop"
        else:
            text = "🂠"
            callback_data = f"jk:{game.game_id}:{cell}"

        row.append(
            InlineKeyboardButton(
                text=text,
                callback_data=callback_data,
            )
        )

    return InlineKeyboardMarkup(inline_keyboard=[row])


def help_text() -> str:
    return (
        "🎮 <b>Игровой бот</b>\n\n"
        "Команды:\n"
        "• <code>б</code> — показать свой баланс.\n"
        "• Ответь на сообщение игрока и напиши <code>п 500</code> — перевести 500 баллов.\n"
        "• <code>мины 100</code> — начать «Мины» со ставкой 100.\n"
        "• <code>джокер 100</code> — начать «Джокер» со ставкой 100.\n\n"
        f"Каждый новый игрок получает <b>{START_BALANCE}</b> баллов.\n"
        "Баланс сохраняется в базе SQLite после перезапуска.\n\n"
        "💣 <b>Мины:</b> поле 4×4, на нём случайно размещается 4–6 мин. "
        "Ставка списывается при начале. Каждая безопасная клетка удваивает "
        "текущий выигрыш. Нажми «Забрать», чтобы начислить его на баланс. "
        "При попадании на мину ставка проиграна.\n\n"
        "🃏 <b>Джокер:</b> три закрытые карты. За двумя находятся джокеры, "
        "за одной — череп. Джокер начисляет двойную ставку, череп означает проигрыш."
    )


@router.message(CommandStart())
@router.message(Command("help"))
async def start_handler(message: Message) -> None:
    if not message.from_user:
        return

    await storage.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )
    await message.answer(help_text(), parse_mode="HTML")


@router.message(F.text.regexp(BALANCE_RE))
async def balance_handler(message: Message) -> None:
    if not message.from_user:
        return

    balance = await storage.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name,
    )

    await message.reply(
        f"💰 {user_name(message.from_user)}, твой баланс: {balance} баллов."
    )


@router.message(F.text.regexp(TRANSFER_RE))
async def transfer_handler(message: Message) -> None:
    if not message.from_user or not message.text:
        return

    match = TRANSFER_RE.match(message.text.strip())
    if not match:
        return

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "Ответь на сообщение нужного игрока и напиши, например: п 500"
        )
        return

    recipient = message.reply_to_message.from_user

    if recipient.is_bot:
        await message.reply("Ботам нельзя переводить игровые баллы.")
        return

    valid, amount, error = parse_amount(match.group(1))
    if not valid:
        await message.reply(error)
        return

    ok, result_text, sender_balance, recipient_balance = await storage.transfer(
        sender_id=message.from_user.id,
        recipient_id=recipient.id,
        amount=amount,
        sender_username=message.from_user.username,
        sender_name=message.from_user.full_name,
        recipient_username=recipient.username,
        recipient_name=recipient.full_name,
    )

    if not ok:
        await message.reply(f"❌ {result_text}")
        return

    await message.reply(
        f"✅ {user_name(message.from_user)} перевёл {amount} баллов игроку "
        f"{user_name(recipient)}.\n"
        f"Баланс отправителя: {sender_balance}\n"
        f"Баланс получателя: {recipient_balance}"
    )


async def start_mines(message: Message, bet: int) -> None:
    if not message.from_user:
        return

    game_id = secrets.token_hex(4)
    mine_count = random.randint(4, 6)
    mines = set(random.sample(range(16), mine_count))

    placeholder = await message.reply("Создаю поле…")

    game = Game(
        game_id=game_id,
        chat_id=message.chat.id,
        message_id=placeholder.message_id,
        user_id=message.from_user.id,
        game_type="mines",
        bet=bet,
        payout=bet,
        danger_cells=mines,
        opened=set(),
        status="active",
    )

    ok, result_text, balance = await storage.create_game(
        game_id=game_id,
        chat_id=message.chat.id,
        message_id=placeholder.message_id,
        user_id=message.from_user.id,
        game_type="mines",
        bet=bet,
        payout=bet,
        danger_cells=mines,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    if not ok:
        await placeholder.edit_text(f"❌ {result_text}")
        return

    await placeholder.edit_text(
        f"💣 <b>Мины 4×4</b>\n"
        f"Игрок: {user_name(message.from_user)}\n"
        f"Ставка: {bet}\n"
        f"Мин на поле: {mine_count}\n"
        f"Текущий выигрыш: {bet}\n"
        f"Баланс после ставки: {balance}\n\n"
        f"Нажми на клетку или забери текущий выигрыш.",
        parse_mode="HTML",
        reply_markup=mines_keyboard(game),
    )


async def start_joker(message: Message, bet: int) -> None:
    if not message.from_user:
        return

    game_id = secrets.token_hex(4)
    skull_cell = {random.randrange(3)}

    placeholder = await message.reply("Перемешиваю карты…")

    game = Game(
        game_id=game_id,
        chat_id=message.chat.id,
        message_id=placeholder.message_id,
        user_id=message.from_user.id,
        game_type="joker",
        bet=bet,
        payout=bet * 2,
        danger_cells=skull_cell,
        opened=set(),
        status="active",
    )

    ok, result_text, balance = await storage.create_game(
        game_id=game_id,
        chat_id=message.chat.id,
        message_id=placeholder.message_id,
        user_id=message.from_user.id,
        game_type="joker",
        bet=bet,
        payout=bet * 2,
        danger_cells=skull_cell,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )

    if not ok:
        await placeholder.edit_text(f"❌ {result_text}")
        return

    await placeholder.edit_text(
        f"🃏 <b>Джокер</b>\n"
        f"Игрок: {user_name(message.from_user)}\n"
        f"Ставка: {bet}\n"
        f"Выигрыш при джокере: {bet * 2}\n"
        f"Баланс после ставки: {balance}\n\n"
        f"Выбери одну из трёх карт.",
        parse_mode="HTML",
        reply_markup=joker_keyboard(game),
    )


@router.message(F.text.regexp(MINES_RE))
async def mines_handler(message: Message) -> None:
    match = MINES_RE.match((message.text or "").strip())
    if not match:
        return

    valid, bet, error = parse_amount(match.group(1))
    if not valid:
        await message.reply(error)
        return

    await start_mines(message, bet)


@router.message(F.text.regexp(JOKER_RE))
async def joker_handler(message: Message) -> None:
    match = JOKER_RE.match((message.text or "").strip())
    if not match:
        return

    valid, bet, error = parse_amount(match.group(1))
    if not valid:
        await message.reply(error)
        return

    await start_joker(message, bet)


async def validate_owner(
    callback: CallbackQuery,
    game: Optional[Game],
) -> bool:
    if not game:
        await callback.answer("Игра не найдена.", show_alert=True)
        return False

    if callback.from_user.id != game.user_id:
        await callback.answer(
            "Это игра другого игрока.",
            show_alert=True,
        )
        return False

    if not callback.message:
        await callback.answer(
            "Сообщение игры недоступно.",
            show_alert=True,
        )
        return False

    if (
        callback.message.chat.id != game.chat_id
        or callback.message.message_id != game.message_id
    ):
        await callback.answer(
            "Эта кнопка больше неактуальна.",
            show_alert=True,
        )
        return False

    return True


@router.callback_query(F.data.startswith("mn:"))
async def mines_cell_handler(callback: CallbackQuery) -> None:
    _, game_id, cell_raw = callback.data.split(":")
    game = await storage.get_game(game_id)

    if not await validate_owner(callback, game):
        return

    if game.game_type != "mines":
        await callback.answer("Некорректная игра.", show_alert=True)
        return

    result, game, balance = await storage.open_mines_cell(
        game_id,
        int(cell_raw),
    )

    if result == "already":
        await callback.answer("Эта клетка уже открыта.")
        return

    if result in {"missing", "inactive"} or not game:
        await callback.answer(
            "Игра уже завершена или не найдена.",
            show_alert=True,
        )
        return

    if result == "mine":
        await callback.message.edit_text(
            f"💥 <b>Ты попал на мину!</b>\n"
            f"Ставка {game.bet} проиграна.\n"
            f"Баланс: {balance}",
            parse_mode="HTML",
            reply_markup=mines_keyboard(game, reveal_all=True),
        )
        await callback.answer("Бум! Мина.", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ <b>Безопасная клетка</b>\n"
        f"Начальная ставка: {game.bet}\n"
        f"Открыто безопасных клеток: {len(game.opened)}\n"
        f"Текущий выигрыш: {game.payout}\n"
        f"Баланс без незабранного выигрыша: {balance}\n\n"
        f"Продолжай или забери выигрыш.",
        parse_mode="HTML",
        reply_markup=mines_keyboard(game),
    )
    await callback.answer(f"Текущий выигрыш: {game.payout}")


@router.callback_query(F.data.startswith("cash:"))
async def cashout_handler(callback: CallbackQuery) -> None:
    _, game_id = callback.data.split(":")
    game = await storage.get_game(game_id)

    if not await validate_owner(callback, game):
        return

    if game.game_type != "mines":
        await callback.answer("Некорректная игра.", show_alert=True)
        return

    ok, game, balance = await storage.cashout(game_id)

    if not ok or not game:
        await callback.answer(
            "Игра уже завершена.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        f"💰 <b>Выигрыш забран!</b>\n"
        f"Начальная ставка: {game.bet}\n"
        f"Начислено: {game.payout}\n"
        f"Новый баланс: {balance}",
        parse_mode="HTML",
        reply_markup=mines_keyboard(game, reveal_all=True),
    )
    await callback.answer(
        f"На баланс начислено {game.payout}",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("jk:"))
async def joker_choice_handler(callback: CallbackQuery) -> None:
    _, game_id, cell_raw = callback.data.split(":")
    game = await storage.get_game(game_id)

    if not await validate_owner(callback, game):
        return

    if game.game_type != "joker":
        await callback.answer("Некорректная игра.", show_alert=True)
        return

    result, game, balance = await storage.choose_joker(
        game_id,
        int(cell_raw),
    )

    if result in {"missing", "inactive"} or not game:
        await callback.answer(
            "Игра уже завершена или не найдена.",
            show_alert=True,
        )
        return

    if result == "skull":
        text = (
            f"💀 <b>Череп!</b>\n"
            f"Ставка {game.bet} проиграна.\n"
            f"Баланс: {balance}"
        )
        alert = "Ты выбрал череп."
    else:
        text = (
            f"🃏 <b>Джокер!</b>\n"
            f"Ставка: {game.bet}\n"
            f"Начислено: {game.payout}\n"
            f"Новый баланс: {balance}"
        )
        alert = f"Ты выиграл {game.payout}!"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=joker_keyboard(game, reveal_all=True),
    )
    await callback.answer(alert, show_alert=True)


@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery) -> None:
    await callback.answer("Эта клетка уже открыта или игра завершена.")


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не найден BOT_TOKEN. Создай файл .env и вставь токен от BotFather."
        )

    await storage.init()

    bot = Bot(BOT_TOKEN)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Показать правила"),
    ])

    logger.info("Bot started")
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
