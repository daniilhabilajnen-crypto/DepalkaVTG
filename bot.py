import asyncio, json, logging, os, random, re, secrets
from datetime import datetime, timezone
import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
TOKEN=os.getenv('BOT_TOKEN','').strip(); DB=os.getenv('DATABASE_PATH','game_bot.sqlite3')
START_BALANCE=2500; DAILY_BONUS=500; ADMIN='some_randomuser'; GOLDEN_CHANCE=.005
router=Router(); logging.basicConfig(level=logging.INFO)
RX={
'bal':re.compile(r'^(б|баланс)$',re.I),'profile':re.compile(r'^(профиль|profile)$',re.I),
'pay':re.compile(r'^(п|перевод)\s+(\d+)$',re.I),'bonus':re.compile(r'^(бонус|bonus)$',re.I),
'quests':re.compile(r'^(задания|квесты|quests)$',re.I),'mines':re.compile(r'^(мины|mines)\s+(\d+)$',re.I),
'joker':re.compile(r'^(джокер|joker)\s+(\d+)$',re.I),'dice':re.compile(r'^(кости|кубик|dice)\s+(\d+)$',re.I),
'coin':re.compile(r'^(монета|coin)\s+(\d+)\s+(орел|орёл|решка)$',re.I),
'roulette':re.compile(r'^(рулетка|roulette)\s+(\d+)\s+(красное|черное|чёрное|зеленое|зелёное|к|ч|з)$',re.I),
'duel':re.compile(r'^(дуэль|дуел|duel)\s+(\d+)$',re.I),'give':re.compile(r'^(выдать|дать)\s+(\d+)$',re.I),
'take':re.compile(r'^(снять|забрать)\s+(\d+)$',re.I),'reset':re.compile(r'^(обнулить|обнулить счет|обнулить счёт)$',re.I),
'help':re.compile(r'^(хелп|help)$',re.I),'adminhelp':re.compile(r'^(админхелп|adminhelp)$',re.I)}
UNLOCK={'mines':1,'joker':2,'dice':3,'coin':4,'roulette':5,'duel':6}
NAMES={'mines':'💣 Мины','joker':'🃏 Джокер','dice':'🎲 Кости','coin':'🪙 Монета','roulette':'🎡 Рулетка','duel':'⚔️ Дуэль'}

def is_admin(u): return bool(u and u.username and u.username.lower()==ADMIN)
def uname(u): return f'@{u.username}' if u.username else u.full_name
def max_bet(level): return 1000+(level-1)*250
def xp_need(level): return 50+(level-1)*30
def rank(level): return '👑' if level>=50 else '💎' if level>=30 else '🥇' if level>=20 else '🥈' if level>=10 else '🥉'
def payout25(bet,steps=1): return bet+(bet*25*steps)//100
def xp_gain(bet,profit): return max(8,min(300,8+bet//80+max(0,profit)//50))
def today(): return datetime.now(timezone.utc).date().isoformat()
def spend_target():
 r=random.random()
 t=random.randint(100,1000) if r<.55 else random.randint(1001,3000) if r<.82 else random.randint(3001,6000) if r<.95 else random.randint(6001,10000)
 return t,100+t//4

def new_quests():
 t,r=spend_target(); return [
 {'id':'play','title':'Сыграй 5 игр','target':5,'progress':0,'reward':250,'done':False},
 {'id':'win','title':'Выиграй 3 раза','target':3,'progress':0,'reward':350,'done':False},
 {'id':'transfer','title':'Переведи баллы другу','target':1,'progress':0,'reward':200,'done':False},
 {'id':'spend','title':f'Потрать {t} баллов на ставки','target':t,'progress':0,'reward':r,'done':False}]

class Store:
 def __init__(self,path): self.path=path; self.lock=asyncio.Lock()
 async def init(self):
  async with aiosqlite.connect(self.path) as d:
   await d.executescript('''PRAGMA journal_mode=WAL;
   CREATE TABLE IF NOT EXISTS users(user_id INTEGER PRIMARY KEY,username TEXT,full_name TEXT,balance INTEGER DEFAULT 2500,last_bonus TEXT,level INTEGER DEFAULT 1,xp INTEGER DEFAULT 0,games_played INTEGER DEFAULT 0,wins INTEGER DEFAULT 0,losses INTEGER DEFAULT 0,biggest_win INTEGER DEFAULT 0,biggest_loss INTEGER DEFAULT 0,win_streak INTEGER DEFAULT 0,loss_streak INTEGER DEFAULT 0,best_win_streak INTEGER DEFAULT 0,level_rewards_claimed TEXT DEFAULT '[]',quest_date TEXT,quests_json TEXT);
   CREATE TABLE IF NOT EXISTS games(game_id TEXT PRIMARY KEY,chat_id INTEGER,message_id INTEGER,user_id INTEGER,opponent_id INTEGER DEFAULT 0,type TEXT,bet INTEGER,payout INTEGER,danger TEXT DEFAULT '',opened TEXT DEFAULT '',status TEXT DEFAULT 'active',meta TEXT DEFAULT '');
   CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT); INSERT OR IGNORE INTO settings VALUES('paused','0');''')
   for q in ["ALTER TABLE users ADD COLUMN level INTEGER DEFAULT 1","ALTER TABLE users ADD COLUMN xp INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN games_played INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN wins INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN losses INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN biggest_win INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN biggest_loss INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN win_streak INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN loss_streak INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN best_win_streak INTEGER DEFAULT 0","ALTER TABLE users ADD COLUMN level_rewards_claimed TEXT DEFAULT '[]'","ALTER TABLE users ADD COLUMN quest_date TEXT","ALTER TABLE users ADD COLUMN quests_json TEXT","ALTER TABLE users ADD COLUMN last_bonus TEXT","ALTER TABLE games ADD COLUMN opponent_id INTEGER DEFAULT 0","ALTER TABLE games ADD COLUMN meta TEXT DEFAULT ''"]:
    try: await d.execute(q)
    except aiosqlite.OperationalError: pass
   await d.commit()
 async def ensure(self,u):
  async with self.lock,aiosqlite.connect(self.path) as d:
   await d.execute("INSERT INTO users(user_id,username,full_name,balance) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,full_name=excluded.full_name",(u.id,u.username,u.full_name,START_BALANCE)); await d.commit()
 async def row(self,uid):
  async with aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row; return await (await d.execute('SELECT * FROM users WHERE user_id=?',(uid,))).fetchone()
 async def balance(self,uid):
  r=await self.row(uid); return int(r['balance']) if r else START_BALANCE
 async def delta(self,uid,n):
  async with self.lock,aiosqlite.connect(self.path) as d:
   await d.execute('BEGIN IMMEDIATE'); r=await (await d.execute('SELECT balance FROM users WHERE user_id=?',(uid,))).fetchone()
   if not r: await d.rollback(); return False,0
   new=int(r[0])+n
   if new<0: await d.rollback(); return False,int(r[0])
   await d.execute('UPDATE users SET balance=? WHERE user_id=?',(new,uid)); await d.commit(); return True,new
 async def pause(self):
  async with aiosqlite.connect(self.path) as d:
   r=await (await d.execute("SELECT value FROM settings WHERE key='paused'")).fetchone(); return bool(r and r[0]=='1')
 async def set_pause(self,v):
  async with aiosqlite.connect(self.path) as d: await d.execute("UPDATE settings SET value=? WHERE key='paused'",('1' if v else '0',)); await d.commit()
 async def bonus(self,u):
  await self.ensure(u)
  async with self.lock,aiosqlite.connect(self.path) as d:
   await d.execute('BEGIN IMMEDIATE'); b,last=await (await d.execute('SELECT balance,last_bonus FROM users WHERE user_id=?',(u.id,))).fetchone()
   if last==today(): await d.rollback(); return False,int(b)
   b=int(b)+DAILY_BONUS; await d.execute('UPDATE users SET balance=?,last_bonus=? WHERE user_id=?',(b,today(),u.id)); await d.commit(); return True,b
 async def transfer(self,a,b,n):
  await self.ensure(a); await self.ensure(b)
  async with self.lock,aiosqlite.connect(self.path) as d:
   await d.execute('BEGIN IMMEDIATE'); ab=int((await (await d.execute('SELECT balance FROM users WHERE user_id=?',(a.id,))).fetchone())[0])
   if ab<n: await d.rollback(); return False,ab,await self.balance(b.id)
   await d.execute('UPDATE users SET balance=balance-? WHERE user_id=?',(n,a.id)); await d.execute('UPDATE users SET balance=balance+? WHERE user_id=?',(n,b.id)); await d.commit()
  await self.quest(a.id,'transfer',1); return True,ab-n,await self.balance(b.id)
 async def quests(self,uid):
  async with self.lock,aiosqlite.connect(self.path) as d:
   await d.execute('BEGIN IMMEDIATE'); r=await (await d.execute('SELECT quest_date,quests_json FROM users WHERE user_id=?',(uid,))).fetchone()
   if not r: await d.rollback(); return []
   qs=new_quests() if r[0]!=today() or not r[1] else json.loads(r[1])
   if r[0]!=today() or not r[1]: await d.execute('UPDATE users SET quest_date=?,quests_json=? WHERE user_id=?',(today(),json.dumps(qs,ensure_ascii=False),uid))
   await d.commit(); return qs
 async def quest(self,uid,qid,amount):
  qs=await self.quests(uid); reward=0; changed=False
  for q in qs:
   if q['id']==qid and not q['done']:
    q['progress']=min(q['target'],q['progress']+amount); changed=True
    if q['progress']>=q['target']: q['done']=True; reward+=q['reward']
  if changed:
   async with self.lock,aiosqlite.connect(self.path) as d: await d.execute('UPDATE users SET quests_json=?,balance=balance+? WHERE user_id=?',(json.dumps(qs,ensure_ascii=False),reward,uid)); await d.commit()
  return reward
 async def validate(self,u,typ,bet):
  await self.ensure(u); r=await self.row(u.id); lvl=int(r['level'])
  if lvl<UNLOCK[typ]: return False,f'🔒 {NAMES[typ]} открывается на {UNLOCK[typ]} уровне.'
  if bet<1:return False,'Минимальная ставка: 1.'
  if bet>max_bet(lvl):return False,f'❌ Лимит ставки для {lvl} уровня: {max_bet(lvl)}'
  if int(r['balance'])<bet:return False,f'❌ Недостаточно баллов. Баланс: {r["balance"]}'
  return True,''
 async def simple(self,u,typ,bet,payout):
  ok,e=await self.validate(u,typ,bet)
  if not ok:return False,e
  async with self.lock,aiosqlite.connect(self.path) as d: await d.execute('UPDATE users SET balance=balance-?+? WHERE user_id=?',(bet,payout,u.id)); await d.commit()
  await self.quest(u.id,'play',1); await self.quest(u.id,'spend',bet)
  if payout: await self.result(u.id,True,bet,payout); await self.quest(u.id,'win',1)
  else: await self.result(u.id,False,bet,0)
  return True,''
 async def newgame(self,gid,msg,u,typ,bet,payout,danger='',opp=0,meta=''):
  ok,e=await self.validate(u,typ,bet)
  if not ok:return False,e
  async with self.lock,aiosqlite.connect(self.path) as d:
   await d.execute('UPDATE users SET balance=balance-? WHERE user_id=?',(bet,u.id)); await d.execute('INSERT INTO games VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(gid,msg.chat.id,msg.message_id,u.id,opp,typ,bet,payout,danger,'','active',meta)); await d.commit()
  await self.quest(u.id,'play',1); await self.quest(u.id,'spend',bet); return True,''
 async def game(self,gid):
  async with aiosqlite.connect(self.path) as d: d.row_factory=aiosqlite.Row; return await (await d.execute('SELECT * FROM games WHERE game_id=?',(gid,))).fetchone()
 async def update(self,gid,**kw):
  if not kw:return
  async with aiosqlite.connect(self.path) as d: await d.execute('UPDATE games SET '+','.join(f'{k}=?' for k in kw)+' WHERE game_id=?',(*kw.values(),gid)); await d.commit()
 async def result(self,uid,won,bet,payout):
  profit=max(0,payout-bet); gain=xp_gain(bet,profit) if won else 0
  async with self.lock,aiosqlite.connect(self.path) as d:
   d.row_factory=aiosqlite.Row; await d.execute('BEGIN IMMEDIATE'); r=await (await d.execute('SELECT * FROM users WHERE user_id=?',(uid,))).fetchone(); lvl=int(r['level']); xp=int(r['xp'])+gain; claimed=json.loads(r['level_rewards_claimed'] or '[]'); reward=0
   while xp>=xp_need(lvl):
    xp-=xp_need(lvl); lvl+=1
    if lvl%5==0 and lvl not in claimed: reward+=500+(lvl//5-1)*100; claimed.append(lvl)
   if won:
    vals=(int(r['wins'])+1,int(r['losses']),max(int(r['biggest_win']),profit),int(r['biggest_loss']),int(r['win_streak'])+1,0,max(int(r['best_win_streak']),int(r['win_streak'])+1))
   else:
    vals=(int(r['wins']),int(r['losses'])+1,int(r['biggest_win']),max(int(r['biggest_loss']),bet),0,int(r['loss_streak'])+1,int(r['best_win_streak']))
   await d.execute('UPDATE users SET balance=balance+?,level=?,xp=?,games_played=games_played+1,wins=?,losses=?,biggest_win=?,biggest_loss=?,win_streak=?,loss_streak=?,best_win_streak=?,level_rewards_claimed=? WHERE user_id=?',(reward,lvl,xp,*vals,json.dumps(claimed),uid)); await d.commit(); return reward
 async def stop_user(self,uid):
  async with self.lock,aiosqlite.connect(self.path) as d:
   rows=await (await d.execute("SELECT bet FROM games WHERE user_id=? AND status='active'",(uid,))).fetchall(); refund=sum(x[0] for x in rows)
   if refund: await d.execute('UPDATE users SET balance=balance+? WHERE user_id=?',(refund,uid)); await d.execute("UPDATE games SET status='cancelled' WHERE user_id=? AND status='active'",(uid,)); await d.commit()
   return len(rows),refund
 async def stop_all(self):
  async with self.lock,aiosqlite.connect(self.path) as d:
   rows=await (await d.execute("SELECT user_id,bet FROM games WHERE status='active'")).fetchall()
   for uid,bet in rows: await d.execute('UPDATE users SET balance=balance+? WHERE user_id=?',(bet,uid))
   await d.execute("UPDATE games SET status='cancelled' WHERE status='active'"); await d.commit(); return len(rows),sum(x[1] for x in rows)
store=Store(DB)

async def available(m):
 if await store.pause(): await m.reply('⏸ Игры временно приостановлены.'); return False
 return True

def profile(r):
 l=int(r['level']); return f'''╔══════════════╗\n👤 <b>ПРОФИЛЬ</b>\n╚══════════════╝\n\n{rank(l)} Уровень: <b>{l}</b>\n⭐ Опыт: <b>{r['xp']} / {xp_need(l)}</b>\n💰 Баланс: <b>{r['balance']}</b>\n🎯 Макс. ставка: <b>{max_bet(l)}</b>\n\n🎮 Игр: <b>{r['games_played']}</b>\n🏆 Побед: <b>{r['wins']}</b>\n💀 Поражений: <b>{r['losses']}</b>\n📈 Макс. выигрыш: <b>+{r['biggest_win']}</b>\n📉 Макс. проигрыш: <b>-{r['biggest_loss']}</b>\n🔥 Лучшая серия: <b>{r['best_win_streak']}</b>'''
PLAYER='''╔══════════════╗\n🎮 <b>КОМАНДЫ</b>\n╚══════════════╝\n\n<code>профиль</code>, <code>б</code>, <code>бонус</code>, <code>задания</code>\nОтвет + <code>п 500</code>\n<code>мины 100</code> — уровень 1\n<code>джокер 100</code> — уровень 2\n<code>кости 100</code> — уровень 3\n<code>монета 100 орёл</code> — уровень 4\n<code>рулетка 100 красное</code> — уровень 5\nОтвет + <code>дуэль 100</code> — уровень 6\n<code>стопигры</code> — остановить свои игры\n\nМакс. ставка: 1000 + 250 за каждый уровень.'''
ADMINHELP='''🛡 <b>АДМИН-КОМАНДЫ</b>\nОтвет + <code>выдать 500</code>\nОтвет + <code>снять 500</code>\nОтвет + <code>обнулить</code>\n<code>пауза</code>, <code>продолжить</code>, <code>стопигры</code>'''

def mines_kb(gid,opened,danger,payout,done=False,golden=False):
 rows=[]
 for s in range(0,16,4):
  row=[]
  for c in range(s,s+4):
   if done:t='💥' if c in danger else ('✨' if c in opened and golden else '✅' if c in opened else '▫️');data='x'
   elif c in opened:t,data=('✨' if golden else '✅'),'x'
   else:t,data=('🟨' if golden else '▫️'),f'm:{gid}:{c}'
   row.append(InlineKeyboardButton(text=t,callback_data=data))
  rows.append(row)
 if not done: rows.append([InlineKeyboardButton(text=f'💰 Забрать {payout}',callback_data=f'cash:{gid}')])
 return InlineKeyboardMarkup(inline_keyboard=rows)

def joker_kb(gid,danger,opened,payout,done=False):
 stages=max(1,(max(danger|opened)//3+1) if danger or opened else 1); rows=[]
 for st in range(stages):
  cells=range(st*3,st*3+3); selected=next((c for c in cells if c in opened),None); row=[]
  for c in cells:
   if done:t='💀' if c in opened and c in danger else '🃏' if c in opened else '🂠';data='x'
   elif st<stages-1:t,data=('🃏' if c==selected else '🂠'),'x'
   else:t,data='🂠',f'j:{gid}:{c}'
   row.append(InlineKeyboardButton(text=t,callback_data=data))
  rows.append(row)
 if not done:rows.append([InlineKeyboardButton(text=f'💰 Забрать {payout}',callback_data=f'jcash:{gid}')])
 return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(CommandStart())
@router.message(Command('help'))
@router.message(F.text.regexp(RX['help']))
async def help_h(m): await store.ensure(m.from_user); await m.answer(PLAYER,parse_mode='HTML')
@router.message(Command('adminhelp'))
@router.message(F.text.regexp(RX['adminhelp']))
async def ah(m):
 if not is_admin(m.from_user):return await m.reply('⛔ Только для администратора.')
 await m.answer(ADMINHELP,parse_mode='HTML')
@router.message(F.text.regexp(RX['profile']))
async def prof(m): await store.ensure(m.from_user); await m.reply(profile(await store.row(m.from_user.id)),parse_mode='HTML')
@router.message(F.text.regexp(RX['bal']))
async def bal(m): await store.ensure(m.from_user); await m.reply(f'💰 Баланс: <b>{await store.balance(m.from_user.id)}</b>',parse_mode='HTML')
@router.message(F.text.regexp(RX['bonus']))
async def bonus(m):
 ok,b=await store.bonus(m.from_user); await m.reply(f"{'🎁 Получено 500' if ok else '⏳ Уже получено сегодня'}. Баланс: {b}")
@router.message(F.text.regexp(RX['quests']))
async def quests(m):
 await store.ensure(m.from_user); qs=await store.quests(m.from_user.id); lines=['📅 <b>ЕЖЕДНЕВНЫЕ ЗАДАНИЯ</b>','']
 for q in qs: lines += [f"{'✅' if q['done'] else '▫️'} {q['title']}",f"   {q['progress']} / {q['target']} · 🎁 {q['reward']}"]
 await m.reply('\n'.join(lines),parse_mode='HTML')
@router.message(F.text.regexp(RX['pay']))
async def pay(m):
 if not m.reply_to_message:return await m.reply('Ответь на сообщение игрока.')
 t=m.reply_to_message.from_user;n=int(RX['pay'].match(m.text).group(2))
 if t.is_bot or t.id==m.from_user.id:return await m.reply('Нельзя перевести этому пользователю.')
 ok,a,b=await store.transfer(m.from_user,t,n); await m.reply(f"{'✅ Переведено' if ok else '❌ Недостаточно'}. Твой баланс: {a}"+(f'\nБаланс получателя: {b}' if ok else ''))
@router.message(F.text.regexp(RX['give']))
async def give(m):
 if not is_admin(m.from_user):return
 if not m.reply_to_message:return await m.reply('Ответь на сообщение.')
 t=m.reply_to_message.from_user;n=int(RX['give'].match(m.text).group(2));await store.ensure(t);_,b=await store.delta(t.id,n);await m.reply(f'✅ Баланс: {b}')
@router.message(F.text.regexp(RX['take']))
async def take(m):
 if not is_admin(m.from_user):return
 if not m.reply_to_message:return await m.reply('Ответь на сообщение.')
 t=m.reply_to_message.from_user;n=int(RX['take'].match(m.text).group(2));await store.ensure(t);ok,b=await store.delta(t.id,-n);await m.reply(f"{'✅ Снято' if ok else '❌ Недостаточно'}. Баланс: {b}")
@router.message(F.text.regexp(RX['reset']))
async def reset(m):
 if not is_admin(m.from_user):return
 if not m.reply_to_message:return await m.reply('Ответь на сообщение.')
 t=m.reply_to_message.from_user;await store.ensure(t);await store.delta(t.id,-await store.balance(t.id));await m.reply('🧹 Баланс обнулён.')
@router.message(F.text.lower()=='пауза')
async def pause(m):
 if is_admin(m.from_user):await store.set_pause(True);await m.reply('⏸ Игры приостановлены.')
@router.message(F.text.lower()=='продолжить')
async def resume(m):
 if is_admin(m.from_user):await store.set_pause(False);await m.reply('▶️ Игры возобновлены.')
@router.message(F.text.lower()=='стопигры')
async def stop(m):
 c,s=await (store.stop_all() if is_admin(m.from_user) else store.stop_user(m.from_user.id));await m.reply(f'🛑 Завершено: {c}. Возвращено: {s}.')

@router.message(F.text.regexp(RX['dice']))
async def dice(m):
 if not await available(m):return
 bet=int(RX['dice'].match(m.text).group(2));x=random.randint(1,6);p=payout25(bet) if x>=4 else 0;ok,e=await store.simple(m.from_user,'dice',bet,p)
 if not ok:return await m.reply(e)
 await m.reply(f'🎲 <b>КОСТИ</b>\n\nВыпало: <b>{x}</b>\n'+('📈 Плюс 25%' if p else '💀 Проигрыш')+f'\n💰 Баланс: {await store.balance(m.from_user.id)}',parse_mode='HTML')
@router.message(F.text.regexp(RX['coin']))
async def coin(m):
 if not await available(m):return
 q=RX['coin'].match(m.text);bet=int(q.group(2));choice=q.group(3).lower().replace('ё','е');x=random.choice(['орел','решка']);p=payout25(bet) if x==choice else 0;ok,e=await store.simple(m.from_user,'coin',bet,p)
 if not ok:return await m.reply(e)
 await m.reply(f'🪙 Выпало: <b>{x}</b>\n'+('📈 Плюс 25%' if p else '💀 Проигрыш')+f'\n💰 Баланс: {await store.balance(m.from_user.id)}',parse_mode='HTML')
@router.message(F.text.regexp(RX['roulette']))
async def roulette(m):
 if not await available(m):return
 q=RX['roulette'].match(m.text);bet=int(q.group(2));raw=q.group(3).lower().replace('ё','е');choice='красное' if raw in ('к','красное') else 'черное' if raw in ('ч','черное') else 'зеленое';n=random.randint(0,36);x='зеленое' if n==0 else ('красное' if n%2 else 'черное');p=payout25(bet) if x==choice else 0;ok,e=await store.simple(m.from_user,'roulette',bet,p)
 if not ok:return await m.reply(e)
 await m.reply(f'🎡 Число: <b>{n}</b> · {x}\n'+('📈 Плюс 25%' if p else '💀 Проигрыш')+f'\n💰 Баланс: {await store.balance(m.from_user.id)}',parse_mode='HTML')

@router.message(F.text.regexp(RX['mines']))
async def mines(m):
 if not await available(m):return
 bet=int(RX['mines'].match(m.text).group(2));gold=random.random()<GOLDEN_CHANCE;danger=set(random.sample(range(16),random.randint(4,6)));gid=secrets.token_hex(4);p=await m.reply('⛏ Подготавливаю поле…');ok,e=await store.newgame(gid,p,m.from_user,'mines',bet,bet,','.join(map(str,danger)),meta=json.dumps({'golden':gold}))
 if not ok:return await p.edit_text(e)
 title='🌟 ЗОЛОТЫЕ МИНЫ 🌟' if gold else '💣 МИНЫ';extra='\n✨ +100% и новая мина за успех' if gold else ''
 await p.edit_text(f'╔══════════════╗\n<b>{title}</b>\n╚══════════════╝\n\n💵 Ставка: <b>{bet}</b>\n💣 Мин: <b>{len(danger)}</b>\n💰 Забрать: <b>{bet}</b>{extra}',parse_mode='HTML',reply_markup=mines_kb(gid,set(),danger,bet,golden=gold))
@router.callback_query(F.data.startswith('m:'))
async def mclick(c):
 _,gid,s=c.data.split(':');g=await store.game(gid)
 if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
 cell=int(s);danger=set(map(int,g['danger'].split(',')));opened=set(map(int,g['opened'].split(','))) if g['opened'] else set();gold=json.loads(g['meta'] or '{}').get('golden',False);opened.add(cell)
 if cell in danger:
  await store.update(gid,status='lost',opened=','.join(map(str,opened)));await store.result(g['user_id'],False,g['bet'],0);return await c.message.edit_text(f'💥 <b>МИНА!</b>\nБаланс: {await store.balance(g["user_id"])}',parse_mode='HTML',reply_markup=mines_kb(gid,opened,danger,0,True,gold))
 steps=len(opened);p=g['bet']*(1+steps) if gold else payout25(g['bet'],steps)
 if gold:
  free=list(set(range(16))-opened-danger)
  if free:danger.add(random.choice(free))
 await store.update(gid,opened=','.join(map(str,opened)),danger=','.join(map(str,danger)),payout=p);await c.message.edit_text(f"{'✨' if gold else '✅'} <b>БЕЗОПАСНО</b>\n📈 Плюс <b>{steps*(100 if gold else 25)}%</b>\n💰 Забрать: <b>{p}</b>\n💣 Мин: <b>{len(danger)}</b>",parse_mode='HTML',reply_markup=mines_kb(gid,opened,danger,p,golden=gold))
@router.callback_query(F.data.startswith('cash:'))
async def cash(c):
 gid=c.data.split(':')[1];g=await store.game(gid)
 if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
 await store.update(gid,status='won');await store.delta(g['user_id'],g['payout']);reward=await store.result(g['user_id'],True,g['bet'],g['payout']);await store.quest(g['user_id'],'win',1);await c.message.edit_text(f'💰 <b>ВЫИГРЫШ ЗАБРАН</b>\nНачислено: <b>{g["payout"]}</b>\nБаланс: <b>{await store.balance(g["user_id"])}</b>'+ (f'\n🎁 Награда уровня: {reward}' if reward else ''),parse_mode='HTML')

@router.message(F.text.regexp(RX['joker']))
async def joker(m):
 if not await available(m):return
 bet=int(RX['joker'].match(m.text).group(2));danger={random.randrange(3)};gid=secrets.token_hex(4);p=await m.reply('🃏 Перемешиваю…');ok,e=await store.newgame(gid,p,m.from_user,'joker',bet,bet,','.join(map(str,danger)))
 if not ok:return await p.edit_text(e)
 await p.edit_text(f'🃏 <b>ДЖОКЕР</b>\n💵 Ставка: {bet}\n💰 Забрать: {bet}',parse_mode='HTML',reply_markup=joker_kb(gid,danger,set(),bet))
@router.callback_query(F.data.startswith('j:'))
async def jclick(c):
 _,gid,s=c.data.split(':');g=await store.game(gid)
 if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
 cell=int(s);danger=set(map(int,g['danger'].split(',')));opened=set(map(int,g['opened'].split(','))) if g['opened'] else set()
 if cell//3!=len(opened):return await c.answer('Ряд неактивен.',show_alert=True)
 opened.add(cell)
 if cell in danger:await store.update(gid,status='lost',opened=','.join(map(str,opened)));await store.result(g['user_id'],False,g['bet'],0);return await c.message.edit_text(f'💀 <b>ЧЕРЕП</b>\nБаланс: {await store.balance(g["user_id"])}',parse_mode='HTML',reply_markup=joker_kb(gid,danger,opened,0,True))
 steps=len(opened);p=payout25(g['bet'],steps);nextcells=list(range(steps*3,steps*3+3));danger.update(random.sample(nextcells,2 if steps>=4 else 1));await store.update(gid,opened=','.join(map(str,opened)),danger=','.join(map(str,danger)),payout=p);await c.message.edit_text(f'🃏 <b>ДЖОКЕР!</b>\n📈 Плюс {steps*25}%\n💰 Забрать: {p}\nНовый ряд: '+('1 джокер и 2 черепа' if steps>=4 else '2 джокера и 1 череп'),parse_mode='HTML',reply_markup=joker_kb(gid,danger,opened,p))
@router.callback_query(F.data.startswith('jcash:'))
async def jcash(c):
 gid=c.data.split(':')[1];g=await store.game(gid)
 if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
 await store.update(gid,status='won');await store.delta(g['user_id'],g['payout']);reward=await store.result(g['user_id'],True,g['bet'],g['payout']);await store.quest(g['user_id'],'win',1);await c.message.edit_text(f'💰 <b>ВЫИГРЫШ ЗАБРАН</b>\nНачислено: {g["payout"]}\nБаланс: {await store.balance(g["user_id"])}'+(f'\n🎁 Награда уровня: {reward}' if reward else ''),parse_mode='HTML')

@router.message(F.text.regexp(RX['duel']))
async def duel(m):
 if not await available(m):return
 if not m.reply_to_message:return await m.reply('Ответь на сообщение соперника.')
 t=m.reply_to_message.from_user
 if t.id==m.from_user.id or t.is_bot:return await m.reply('Нельзя вызвать этого пользователя.')
 bet=int(RX['duel'].match(m.text).group(2));gid=secrets.token_hex(4);p=await m.reply('⚔️ Создаю дуэль…');ok,e=await store.newgame(gid,p,m.from_user,'duel',bet,bet*2,opp=t.id)
 if not ok:return await p.edit_text(e)
 kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⚔️ Принять',callback_data=f'd:{gid}'),InlineKeyboardButton(text='❌ Отказ',callback_data=f'r:{gid}')]]);await p.edit_text(f'⚔️ <b>ДУЭЛЬ</b>\n{uname(m.from_user)} вызывает {uname(t)}\nСтавка: {bet}',parse_mode='HTML',reply_markup=kb)
@router.callback_query(F.data.startswith('d:'))
async def daccept(c):
 gid=c.data.split(':')[1];g=await store.game(gid)
 if not g or g['status']!='active' or c.from_user.id!=g['opponent_id']:return await c.answer('Недоступно.',show_alert=True)
 ok,e=await store.validate(c.from_user,'duel',g['bet'])
 if not ok:return await c.answer(e,show_alert=True)
 await store.delta(c.from_user.id,-g['bet']);await store.quest(c.from_user.id,'play',1);await store.quest(c.from_user.id,'spend',g['bet']);a=b=0
 while a==b:a,b=random.randint(1,6),random.randint(1,6)
 winner=g['user_id'] if a>b else c.from_user.id;loser=c.from_user.id if winner==g['user_id'] else g['user_id'];pot=g['bet']*2;await store.delta(winner,pot);await store.result(winner,True,g['bet'],pot);await store.result(loser,False,g['bet'],0);await store.quest(winner,'win',1);await store.update(gid,status='won');await c.message.edit_text(f'⚔️ <b>ДУЭЛЬ</b>\nБроски: {a} против {b}\n🏆 Банк: {pot}',parse_mode='HTML')
@router.callback_query(F.data.startswith('r:'))
async def decline(c):
 gid=c.data.split(':')[1];g=await store.game(gid)
 if not g or g['status']!='active' or c.from_user.id!=g['opponent_id']:return await c.answer('Недоступно.',show_alert=True)
 await store.update(gid,status='cancelled');await store.delta(g['user_id'],g['bet']);await c.message.edit_text('❌ Дуэль отклонена. Ставка возвращена.')
@router.callback_query(F.data=='x')
async def noop(c):await c.answer('Кнопка неактивна.')

async def main():
 if not TOKEN:raise RuntimeError('Не найден BOT_TOKEN')
 await store.init();bot=Bot(TOKEN);dp=Dispatcher();dp.include_router(router);await bot.set_my_commands([BotCommand(command='start',description='Запустить'),BotCommand(command='help',description='Команды игроков'),BotCommand(command='adminhelp',description='Команды администратора')]);await dp.start_polling(bot)
if __name__=='__main__':asyncio.run(main())
