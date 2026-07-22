import asyncio, json, logging, os, random, re, secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

load_dotenv()
TOKEN=os.getenv('BOT_TOKEN','').strip(); DATABASE_URL=os.getenv('DATABASE_URL','').strip()
ADMIN='some_randomuser'; START_BALANCE=2500; FIN_TZ=ZoneInfo('Europe/Helsinki')
router=Router(); logging.basicConfig(level=logging.INFO)

RX={
'bal':re.compile(r'^(б|баланс)$',re.I),'profile':re.compile(r'^(профиль|profile)$',re.I),
'pay':re.compile(r'^(п|перевод)\s+(\d+)$',re.I),'bonus':re.compile(r'^(бонус|bonus)$',re.I),
'quests':re.compile(r'^(задания|квесты|quests)$',re.I),'shop':re.compile(r'^(магазин|shop)$',re.I),
'mines':re.compile(r'^(мины|mines)\s+(\d+)$',re.I),'mega':re.compile(r'^(мега\s*удача|mega\s*luck)\s+(\d+)$',re.I),
'joker':re.compile(r'^(джокер|joker)\s+(\d+)$',re.I),'dice':re.compile(r'^(кости|кубик|dice)\s+(\d+)$',re.I),
'coin':re.compile(r'^(монета|coin)\s+(\d+)\s+(орел|орёл|решка)$',re.I),
'roulette':re.compile(r'^(рулетка|roulette)\s+(\d+)\s+(красное|черное|чёрное|зеленое|зелёное|к|ч|з)$',re.I),
'duel':re.compile(r'^(дуэль|дуел|duel)\s+(\d+)$',re.I),'give':re.compile(r'^(выдать|дать)\s+(\d+)$',re.I),
'take':re.compile(r'^(снять|забрать)\s+(\d+)$',re.I),'reset':re.compile(r'^(обнулить|обнулить счет|обнулить счёт)$',re.I),
'givelevel':re.compile(r'^выдать\s+уров(?:ень|ня)\s+(\d+)$',re.I),'takelevel':re.compile(r'^забрать\s+уров(?:ень|ня)\s+(\d+)$',re.I),
'setlevel':re.compile(r'^установить\s+уровень\s+(\d+)$',re.I),'promote':re.compile(r'^назначить\s+подадмина(?:\s+@?([A-Za-z0-9_]{5,32}))?$',re.I),
'demote':re.compile(r'^снять\s+подадмина(?:\s+@?([A-Za-z0-9_]{5,32}))?$',re.I),'goldrain':re.compile(r'^золотой\s+дождь$',re.I),
'buyminus':re.compile(r'^купить\s+(-1\s*мина|минус\s*мина)$',re.I),'buylife':re.compile(r'^купить\s+(вечная\s*удача|\+2%)$',re.I),
'buy24':re.compile(r'^купить\s+(куш\s*24|большой\s*куш\s*24)$',re.I),'buy48':re.compile(r'^купить\s+(куш\s*48|большой\s*куш\s*48)$',re.I),
'grantminus':re.compile(r'^выдать\s+бустер\s+(-1\s*мина|минус\s*мина)$',re.I),
'grantlife':re.compile(r'^выдать\s+бустер\s+(вечная\s*удача|\+2%)$',re.I),
'grant24':re.compile(r'^выдать\s+бустер\s+(куш\s*24|большой\s*куш\s*24)$',re.I),
'grant48':re.compile(r'^выдать\s+бустер\s+(куш\s*48|большой\s*куш\s*48)$',re.I),
'help':re.compile(r'^(хелп|help)$',re.I),'adminhelp':re.compile(r'^(админхелп|adminhelp)$',re.I)}

def now_fin(): return datetime.now(timezone.utc).astimezone(FIN_TZ)
def today_fin(): return now_fin().date()
def is_admin(u): return bool(u and u.username and u.username.lower()==ADMIN)
def name(u): return f'@{u.username}' if u.username else u.full_name
def xp_need(level): return 50+(level-1)*30
def max_bet(level,boost=0): return 1000+(level-1)*250+boost
def payout25(bet,steps=1): return bet+(bet*25*steps)//100
def win_xp(bet,profit): return max(8,min(300,8+bet//80+max(0,profit)//50))
def dec(v,d):
    if v is None:return d
    if isinstance(v,str):
        try:return json.loads(v)
        except:return d
    return v

def random_quests():
    r=random.random()
    spend=random.randint(100,1000) if r<.55 else random.randint(1001,3000) if r<.82 else random.randint(3001,6000) if r<.95 else random.randint(6001,10000)
    pool=[]
    for n in range(3,9): pool.append({'id':'play','title':f'Сыграй {n} игр','target':n,'reward':100+n*30,'xp':10+n*3})
    for n in range(2,6): pool.append({'id':'win','title':f'Выиграй {n} игр','target':n,'reward':180+n*50,'xp':15+n*5})
    for n in (3,5,7,10): pool.append({'id':'safe','title':f'Открой {n} безопасных клеток','target':n,'reward':150+n*25,'xp':15+n*3})
    for n in (300,500,1000,2000): pool.append({'id':'earn','title':f'Заработай {n} баллов прибыли','target':n,'reward':200+n//4,'xp':20+n//100})
    pool += [{'id':'transfer','title':'Переведи баллы другу','target':1,'reward':200,'xp':20},
             {'id':'mines','title':'Сыграй в Мины 3 раза','target':3,'reward':270,'xp':27},
             {'id':'joker','title':'Сыграй в Джокера 3 раза','target':3,'reward':270,'xp':27}]
    qs=random.sample(pool,3); qs.append({'id':'spend','title':f'Потрать {spend} баллов на ставки','target':spend,'reward':100+spend//4,'xp':15+spend//250})
    for q in qs:q.update(progress=0,done=False)
    random.shuffle(qs); return qs

class Store:
    def __init__(self,url):self.url=url;self.pool=None
    async def init(self):
        async def codec(c):
            await c.set_type_codec('json',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
            await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
        self.pool=await asyncpg.create_pool(self.url,min_size=1,max_size=5,init=codec)
        async with self.pool.acquire() as c:
            await c.execute('''CREATE TABLE IF NOT EXISTS users(user_id BIGINT PRIMARY KEY,username TEXT,full_name TEXT NOT NULL,balance BIGINT NOT NULL DEFAULT 2500,last_bonus DATE,role TEXT NOT NULL DEFAULT 'player',grant_date DATE,grant_total BIGINT NOT NULL DEFAULT 0,level INT NOT NULL DEFAULT 1,xp INT NOT NULL DEFAULT 0,games_played INT NOT NULL DEFAULT 0,wins INT NOT NULL DEFAULT 0,losses INT NOT NULL DEFAULT 0,biggest_win BIGINT NOT NULL DEFAULT 0,biggest_loss BIGINT NOT NULL DEFAULT 0,best_win_streak INT NOT NULL DEFAULT 0,win_streak INT NOT NULL DEFAULT 0,level_rewards JSONB NOT NULL DEFAULT '[]'::jsonb,quest_date DATE,quests JSONB,minus_mine INT NOT NULL DEFAULT 0,lifetime_pct INT NOT NULL DEFAULT 0,lifetime_bought BOOLEAN NOT NULL DEFAULT FALSE,maxbet_boost INT NOT NULL DEFAULT 0,maxbet_until TIMESTAMPTZ)''')
            await c.execute('''CREATE TABLE IF NOT EXISTS games(game_id TEXT PRIMARY KEY,chat_id BIGINT,message_id BIGINT,user_id BIGINT,opponent_id BIGINT NOT NULL DEFAULT 0,game_type TEXT,bet BIGINT,payout BIGINT,danger JSONB NOT NULL DEFAULT '[]'::jsonb,opened JSONB NOT NULL DEFAULT '[]'::jsonb,status TEXT NOT NULL DEFAULT 'active',meta JSONB NOT NULL DEFAULT '{}'::jsonb,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
            await c.execute('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)''')
            for k,v in [('paused','0'),('gold_start',''),('gold_end','')]: await c.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT(key) DO NOTHING',k,v)
            for q in ["ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'player'","ALTER TABLE users ADD COLUMN IF NOT EXISTS grant_date DATE","ALTER TABLE users ADD COLUMN IF NOT EXISTS grant_total BIGINT NOT NULL DEFAULT 0","ALTER TABLE users ADD COLUMN IF NOT EXISTS minus_mine INT NOT NULL DEFAULT 0","ALTER TABLE users ADD COLUMN IF NOT EXISTS lifetime_pct INT NOT NULL DEFAULT 0","ALTER TABLE users ADD COLUMN IF NOT EXISTS lifetime_bought BOOLEAN NOT NULL DEFAULT FALSE","ALTER TABLE users ADD COLUMN IF NOT EXISTS maxbet_boost INT NOT NULL DEFAULT 0","ALTER TABLE users ADD COLUMN IF NOT EXISTS maxbet_until TIMESTAMPTZ"]: await c.execute(q)
    async def close(self):
        if self.pool:await self.pool.close()
    async def ensure(self,u): await self.pool.execute('INSERT INTO users(user_id,username,full_name,balance) VALUES($1,$2,$3,$4) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,full_name=EXCLUDED.full_name',u.id,u.username,u.full_name,START_BALANCE)
    async def user(self,uid): return await self.pool.fetchrow('SELECT * FROM users WHERE user_id=$1',uid)
    async def by_username(self,s): return await self.pool.fetchrow('SELECT * FROM users WHERE LOWER(username)=LOWER($1)',s.lstrip('@'))
    async def balance(self,uid): return int(await self.pool.fetchval('SELECT balance FROM users WHERE user_id=$1',uid) or 0)
    async def delta(self,uid,n):
        async with self.pool.acquire() as c:
            async with c.transaction():
                b=await c.fetchval('SELECT balance FROM users WHERE user_id=$1 FOR UPDATE',uid)
                if b is None:return False,0
                nb=int(b)+n
                if nb<0:return False,int(b)
                await c.execute('UPDATE users SET balance=$1 WHERE user_id=$2',nb,uid);return True,nb
    async def role(self,uid): return str(await self.pool.fetchval('SELECT role FROM users WHERE user_id=$1',uid) or 'player')
    async def set_role(self,uid,r): await self.pool.execute('UPDATE users SET role=$1 WHERE user_id=$2',r,uid)
    async def active_boost(self,row):
        if row['maxbet_until'] and row['maxbet_until']>datetime.now(timezone.utc):return int(row['maxbet_boost'])
        if row['maxbet_until']:await self.pool.execute('UPDATE users SET maxbet_boost=0,maxbet_until=NULL WHERE user_id=$1',row['user_id'])
        return 0
    async def add_xp(self,uid,base):
        async with self.pool.acquire() as c:
            async with c.transaction():
                r=await c.fetchrow('SELECT role,level,xp,level_rewards FROM users WHERE user_id=$1 FOR UPDATE',uid)
                gain=round(base*(1.5 if r['role']=='subadmin' else 1)); lvl=int(r['level']);xp=int(r['xp'])+gain;claimed=list(dec(r['level_rewards'],[]));reward=0
                while xp>=xp_need(lvl):
                    xp-=xp_need(lvl);lvl+=1
                    if lvl%5==0 and lvl not in claimed:reward+=500+(lvl//5-1)*100;claimed.append(lvl)
                await c.execute('UPDATE users SET level=$1,xp=$2,balance=balance+$3,level_rewards=$4 WHERE user_id=$5',lvl,xp,reward,claimed,uid)
                return gain,lvl,reward
    async def set_level(self,uid,lvl):
        lvl=max(1,min(lvl,10000));old=int(await self.pool.fetchval('SELECT level FROM users WHERE user_id=$1',uid));await self.pool.execute('UPDATE users SET level=$1,xp=0 WHERE user_id=$2',lvl,uid);return old,lvl
    async def bonus(self,u):
        await self.ensure(u)
        async with self.pool.acquire() as c:
            async with c.transaction():
                r=await c.fetchrow('SELECT balance,last_bonus,role FROM users WHERE user_id=$1 FOR UPDATE',u.id);amount=1000 if r['role']=='subadmin' else 500
                if r['last_bonus']==today_fin():return False,int(r['balance']),amount
                nb=int(r['balance'])+amount;await c.execute('UPDATE users SET balance=$1,last_bonus=$2 WHERE user_id=$3',nb,today_fin(),u.id);return True,nb,amount
    async def quests(self,uid):
        async with self.pool.acquire() as c:
            async with c.transaction():
                r=await c.fetchrow('SELECT quest_date,quests FROM users WHERE user_id=$1 FOR UPDATE',uid)
                if r['quest_date']!=today_fin() or r['quests'] is None:
                    q=random_quests();await c.execute('UPDATE users SET quest_date=$1,quests=$2 WHERE user_id=$3',today_fin(),q,uid);return q
                return list(dec(r['quests'],[]))
    async def quest(self,uid,qid,n):
        qs=await self.quests(uid);coins=xp=0;changed=False
        for q in qs:
            if q['id']==qid and not q['done']:
                q['progress']=min(q['target'],q['progress']+n);changed=True
                if q['progress']>=q['target']:q['done']=True;coins+=q['reward'];xp+=q['xp']
        if changed:
            await self.pool.execute('UPDATE users SET quests=$1,balance=balance+$2 WHERE user_id=$3',qs,coins,uid)
            if xp:await self.add_xp(uid,xp)
        return coins,xp
    async def transfer(self,a,b,n):
        await self.ensure(a);await self.ensure(b)
        async with self.pool.acquire() as c:
            async with c.transaction():
                ab=int(await c.fetchval('SELECT balance FROM users WHERE user_id=$1 FOR UPDATE',a.id));bb=int(await c.fetchval('SELECT balance FROM users WHERE user_id=$1 FOR UPDATE',b.id))
                if ab<n:return False,ab,bb
                await c.execute('UPDATE users SET balance=balance-$1 WHERE user_id=$2',n,a.id);await c.execute('UPDATE users SET balance=balance+$1 WHERE user_id=$2',n,b.id)
        await self.quest(a.id,'transfer',1);return True,ab-n,bb+n
    async def subgrant(self,sub,target,n):
        async with self.pool.acquire() as c:
            async with c.transaction():
                r=await c.fetchrow('SELECT role,grant_date,grant_total FROM users WHERE user_id=$1 FOR UPDATE',sub)
                if not r or r['role']!='subadmin':return False,'Нет прав подадмина.',0
                used=0 if r['grant_date']!=today_fin() else int(r['grant_total']);left=max(0,1000-used)
                if n<1 or n>left:return False,f'Сегодня можно выдать ещё {left}.',left
                await c.execute('UPDATE users SET balance=balance+$1 WHERE user_id=$2',n,target);await c.execute('UPDATE users SET grant_date=$1,grant_total=$2 WHERE user_id=$3',today_fin(),used+n,sub)
                return True,'',int(await c.fetchval('SELECT balance FROM users WHERE user_id=$1',target))
    async def paused(self):return await self.pool.fetchval("SELECT value FROM settings WHERE key='paused'")=='1'
    async def set_paused(self,v):await self.pool.execute("UPDATE settings SET value=$1 WHERE key='paused'",'1' if v else '0')
    async def gold_event(self):
        s=(now_fin()+timedelta(minutes=1)).replace(second=0,microsecond=0);e=s+timedelta(minutes=1)
        await self.pool.execute("UPDATE settings SET value=$1 WHERE key='gold_start'",s.astimezone(timezone.utc).isoformat());await self.pool.execute("UPDATE settings SET value=$1 WHERE key='gold_end'",e.astimezone(timezone.utc).isoformat());return s,e
    async def gold_active(self):
        s=await self.pool.fetchval("SELECT value FROM settings WHERE key='gold_start'");e=await self.pool.fetchval("SELECT value FROM settings WHERE key='gold_end'")
        if not s or not e:return False
        try:return datetime.fromisoformat(s)<=datetime.now(timezone.utc)<datetime.fromisoformat(e)
        except:return False
    async def validate(self,u,bet):
        await self.ensure(u);r=await self.user(u.id);boost=await self.active_boost(r);limit=max_bet(int(r['level']),boost)
        if bet<1:return False,'Минимальная ставка: 1.'
        if bet>limit:return False,f'❌ Максимальная ставка: {limit}'
        if int(r['balance'])<bet:return False,f"❌ Недостаточно баллов. Баланс: {r['balance']}"
        return True,''
    async def win_bonus(self,uid,payout):
        pct=int(await self.pool.fetchval('SELECT lifetime_pct FROM users WHERE user_id=$1',uid) or 0);return payout+(payout*pct)//100
    async def result(self,uid,won,bet,payout):
        profit=max(0,payout-bet)
        if won:
            await self.pool.execute('UPDATE users SET games_played=games_played+1,wins=wins+1,biggest_win=GREATEST(biggest_win,$1),best_win_streak=GREATEST(best_win_streak,win_streak+1),win_streak=win_streak+1 WHERE user_id=$2',profit,uid)
            await self.add_xp(uid,win_xp(bet,profit))
        else:await self.pool.execute('UPDATE users SET games_played=games_played+1,losses=losses+1,biggest_loss=GREATEST(biggest_loss,$1),win_streak=0 WHERE user_id=$2',bet,uid)
    async def simple(self,u,kind,bet,payout):
        ok,err=await self.validate(u,bet)
        if not ok:return False,err
        if payout:payout=await self.win_bonus(u.id,payout)
        async with self.pool.acquire() as c:
            async with c.transaction():
                b=int(await c.fetchval('SELECT balance FROM users WHERE user_id=$1 FOR UPDATE',u.id));await c.execute('UPDATE users SET balance=$1 WHERE user_id=$2',b-bet+payout,u.id)
        await self.quest(u.id,'play',1);await self.quest(u.id,'spend',bet)
        if payout:await self.result(u.id,True,bet,payout);await self.quest(u.id,'win',1);await self.quest(u.id,'earn',payout-bet)
        else:await self.result(u.id,False,bet,0)
        return True,''
    async def reserve_minus(self,uid):
        async with self.pool.acquire() as c:
            async with c.transaction():
                n=int(await c.fetchval('SELECT minus_mine FROM users WHERE user_id=$1 FOR UPDATE',uid) or 0)
                if n<=0:return False
                await c.execute('UPDATE users SET minus_mine=minus_mine-1 WHERE user_id=$1',uid);return True
    async def return_minus(self,uid):await self.pool.execute('UPDATE users SET minus_mine=minus_mine+1 WHERE user_id=$1',uid)
    async def create_game(self,gid,msg,u,kind,bet,payout,danger,opp=0,meta=None):
        ok,err=await self.validate(u,bet)
        if not ok:return False,err
        async with self.pool.acquire() as c:
            async with c.transaction():
                b=int(await c.fetchval('SELECT balance FROM users WHERE user_id=$1 FOR UPDATE',u.id));
                if b<bet:return False,'Недостаточно баллов.'
                await c.execute('UPDATE users SET balance=balance-$1 WHERE user_id=$2',bet,u.id)
                await c.execute("INSERT INTO games(game_id,chat_id,message_id,user_id,opponent_id,game_type,bet,payout,danger,opened,status,meta) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,'[]'::jsonb,'active',$10)",gid,msg.chat.id,msg.message_id,u.id,opp,kind,bet,payout,sorted(danger),meta or {})
        await self.quest(u.id,'play',1);await self.quest(u.id,'spend',bet)
        if kind=='mines':await self.quest(u.id,'mines',1)
        if kind=='joker':await self.quest(u.id,'joker',1)
        return True,''
    async def game(self,gid):return await self.pool.fetchrow('SELECT * FROM games WHERE game_id=$1',gid)
    async def update_game(self,gid,**kw):
        if not kw:return
        vals=[];sets=[]
        for i,(k,v) in enumerate(kw.items(),1):sets.append(f'{k}=${i}');vals.append(v)
        vals.append(gid);await self.pool.execute(f"UPDATE games SET {','.join(sets)} WHERE game_id=${len(vals)}",*vals)
    async def buy(self,uid,kind):
        prices={'minus':1000,'life':7777,'24':500,'48':700};price=prices[kind]
        async with self.pool.acquire() as c:
            async with c.transaction():
                r=await c.fetchrow('SELECT * FROM users WHERE user_id=$1 FOR UPDATE',uid)
                if int(r['balance'])<price:return False,f'Нужно {price} баллов.'
                if kind=='life' and r['lifetime_bought']:return False,'Этот бустер уже куплен.'
                await c.execute('UPDATE users SET balance=balance-$1 WHERE user_id=$2',price,uid)
                if kind=='minus':await c.execute('UPDATE users SET minus_mine=minus_mine+1 WHERE user_id=$1',uid)
                elif kind=='life':await c.execute('UPDATE users SET lifetime_pct=2,lifetime_bought=TRUE WHERE user_id=$1',uid)
                else:
                    amount=200 if kind=='24' else 300;hours=24 if kind=='24' else 48;start=r['maxbet_until'] if r['maxbet_until'] and r['maxbet_until']>datetime.now(timezone.utc) else datetime.now(timezone.utc)
                    await c.execute('UPDATE users SET maxbet_boost=$1,maxbet_until=$2 WHERE user_id=$3',max(int(r['maxbet_boost']),amount),start+timedelta(hours=hours),uid)
        return True,'Бустер куплен.'
    async def grant_booster(self,uid,kind):
        if kind=='minus':await self.pool.execute('UPDATE users SET minus_mine=minus_mine+1 WHERE user_id=$1',uid)
        elif kind=='life':await self.pool.execute('UPDATE users SET lifetime_pct=2,lifetime_bought=TRUE WHERE user_id=$1',uid)
        else:
            r=await self.user(uid);amount=200 if kind=='24' else 300;hours=24 if kind=='24' else 48;start=r['maxbet_until'] if r['maxbet_until'] and r['maxbet_until']>datetime.now(timezone.utc) else datetime.now(timezone.utc)
            await self.pool.execute('UPDATE users SET maxbet_boost=$1,maxbet_until=$2 WHERE user_id=$3',max(int(r['maxbet_boost']),amount),start+timedelta(hours=hours),uid)
    async def stop(self,uid=None):
        where='status=\'active\'' if uid is None else 'status=\'active\' AND user_id=$1';args=[] if uid is None else [uid]
        async with self.pool.acquire() as c:
            async with c.transaction():
                rows=await c.fetch(f'SELECT user_id,bet,meta FROM games WHERE {where} FOR UPDATE',*args);refund=ret=0
                for r in rows:
                    reserved=1 if dec(r['meta'],{}).get('minus_reserved') else 0;await c.execute('UPDATE users SET balance=balance+$1,minus_mine=minus_mine+$2 WHERE user_id=$3',int(r['bet']),reserved,int(r['user_id']));refund+=int(r['bet']);ret+=reserved
                await c.execute(f"UPDATE games SET status='cancelled' WHERE {where}",*args);return len(rows),refund,ret
store=Store(DATABASE_URL)
async def available(m):
    if await store.paused():await m.reply('⏸ Игры временно приостановлены.');return False
    return True

def mines_kb(gid,opened,danger,payout,done=False,golden=False):
    rows=[]
    for s in range(0,16,4):
        row=[]
        for c in range(s,s+4):
            if done:t='💥' if c in danger else ('✨' if c in opened and golden else '✅' if c in opened else '▫️');d='noop'
            elif c in opened:t,d=('✨' if golden else '✅'),'noop'
            else:t,d=('🟨' if golden else '▫️'),f'mine:{gid}:{c}'
            row.append(InlineKeyboardButton(text=t,callback_data=d))
        rows.append(row)
    if not done:rows.append([InlineKeyboardButton(text=f'💰 Забрать {payout}',callback_data=f'cash:{gid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def mega_kb(gid,safe,done=False):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=('🍀' if c==safe else '💣') if done else '❔',callback_data='noop' if done else f'mega:{gid}:{c}') for c in range(s,s+4)] for s in range(0,16,4)])

def joker_kb(gid,danger,opened,payout,done=False):
    stages=max(1,(max(danger|opened)//3+1) if danger or opened else 1);rows=[]
    for st in range(stages):
        cells=range(st*3,st*3+3);sel=next((c for c in cells if c in opened),None);row=[]
        for c in cells:
            if done:t='💀' if c in opened and c in danger else '🃏' if c in opened else '🂠';d='noop'
            elif st<stages-1:t,d=('🃏' if c==sel else '🂠'),'noop'
            else:t,d='🂠',f'joker:{gid}:{c}'
            row.append(InlineKeyboardButton(text=t,callback_data=d))
        rows.append(row)
    if not done:rows.append([InlineKeyboardButton(text=f'💰 Забрать {payout}',callback_data=f'jcash:{gid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

HELP='''╔══════════════╗\n🎮 <b>КОМАНДЫ</b>\n╚══════════════╝\n\n<code>профиль</code>, <code>б</code>, <code>бонус</code>, <code>задания</code>, <code>магазин</code>\nОтвет + <code>п 500</code>\n\nВсе игры с 1 уровня:\n<code>мины 100</code>\n<code>мега удача 100</code>\n<code>джокер 100</code>\n<code>кости 100</code>\n<code>монета 100 орёл</code>\n<code>рулетка 100 красное</code>\nОтвет + <code>дуэль 100</code>\n<code>стопигры</code>'''
ADMINHELP='''🛡 <b>АДМИН-КОМАНДЫ</b>\n\nОтвет: <code>выдать 500</code>, <code>снять 500</code>, <code>обнулить</code>\nОтвет: <code>выдать уровень 3</code>, <code>забрать уровень 2</code>, <code>установить уровень 10</code>\nОтвет или username: <code>назначить подадмина</code>, <code>снять подадмина</code>\n<code>золотой дождь</code>\nОтвет: <code>выдать бустер -1 мина</code>, <code>выдать бустер вечная удача</code>, <code>выдать бустер куш 24</code>, <code>выдать бустер куш 48</code>\n<code>пауза</code>, <code>продолжить</code>, <code>стопигры</code>\n\nПодадмин может только выдавать до 1000 баллов за финские сутки.'''

@router.message(CommandStart())
@router.message(Command('help'))
@router.message(F.text.regexp(RX['help']))
async def help_h(m):await store.ensure(m.from_user);await m.answer(HELP,parse_mode='HTML')
@router.message(Command('adminhelp'))
@router.message(F.text.regexp(RX['adminhelp']))
async def ahelp(m):
    if is_admin(m.from_user):await m.answer(ADMINHELP,parse_mode='HTML')
@router.message(F.text.regexp(RX['bal']))
async def bal(m):await store.ensure(m.from_user);await m.reply(f'💰 Баланс: {await store.balance(m.from_user.id)}')
@router.message(F.text.regexp(RX['profile']))
async def profile(m):
    await store.ensure(m.from_user);r=await store.user(m.from_user.id);boost=await store.active_boost(r);until=r['maxbet_until'];until=until.astimezone(FIN_TZ).strftime('%d.%m %H:%M') if until and until>datetime.now(timezone.utc) else 'нет'
    await m.reply(f"👤 <b>ПРОФИЛЬ</b>\n\n🛡 Роль: <b>{'Подадмин' if r['role']=='subadmin' else 'Игрок'}</b>\n🏅 Уровень: <b>{r['level']}</b>\n⭐ XP: <b>{r['xp']}/{xp_need(int(r['level']))}</b>\n💰 Баланс: <b>{r['balance']}</b>\n🎯 Макс. ставка: <b>{max_bet(int(r['level']),boost)}</b>\n🏆 Побед: <b>{r['wins']}</b> | 💀 Поражений: <b>{r['losses']}</b>\n📈 Макс. выигрыш: <b>{r['biggest_win']}</b>\n📉 Макс. проигрыш: <b>{r['biggest_loss']}</b>\n🧿 -1 мина: <b>{r['minus_mine']}</b>\n✨ Вечный бонус: <b>+{r['lifetime_pct']}%</b>\n💼 Куш до: <b>{until}</b>",parse_mode='HTML')
@router.message(F.text.regexp(RX['bonus']))
async def bonus(m):
    ok,b,n=await store.bonus(m.from_user);await m.reply(f"{'🎁 Получено '+str(n) if ok else '⏳ Бонус уже получен'}. Баланс: {b}\nСброс в 00:00 по Финляндии.")
@router.message(F.text.regexp(RX['quests']))
async def quests(m):
    await store.ensure(m.from_user);qs=await store.quests(m.from_user.id);lines=['📅 <b>ЕЖЕДНЕВНЫЕ ЗАДАНИЯ</b>','']
    for q in qs:lines += [f"{'✅' if q['done'] else '▫️'} {q['title']}",f"   {q['progress']}/{q['target']} · {q['reward']} баллов · {q['xp']} XP"]
    lines.append('\nСброс в 00:00 по Финляндии.');await m.reply('\n'.join(lines),parse_mode='HTML')
@router.message(F.text.regexp(RX['shop']))
async def shop(m):await m.reply('🛒 <b>МАГАЗИН</b>\n\n🧿 -1 мина — 1000: <code>купить -1 мина</code>\n✨ +2% навсегда — 7777: <code>купить вечная удача</code>\n💼 +200 к ставке на 24ч — 500: <code>купить куш 24</code>\n💼 +300 к ставке на 48ч — 700: <code>купить куш 48</code>',parse_mode='HTML')
async def buymsg(m,k):await store.ensure(m.from_user);ok,t=await store.buy(m.from_user.id,k);await m.reply(('✅ ' if ok else '❌ ')+t)
@router.message(F.text.regexp(RX['buyminus']))
async def bm(m):await buymsg(m,'minus')
@router.message(F.text.regexp(RX['buylife']))
async def bl(m):await buymsg(m,'life')
@router.message(F.text.regexp(RX['buy24']))
async def b24(m):await buymsg(m,'24')
@router.message(F.text.regexp(RX['buy48']))
async def b48(m):await buymsg(m,'48')
@router.message(F.text.regexp(RX['pay']))
async def pay(m):
    if not m.reply_to_message:return await m.reply('Ответь на сообщение игрока.')
    t=m.reply_to_message.from_user;n=int(RX['pay'].match(m.text).group(2));ok,a,b=await store.transfer(m.from_user,t,n);await m.reply(f"{'✅ Переведено' if ok else '❌ Недостаточно'}. Баланс: {a}"+(f'\nБаланс получателя: {b}' if ok else ''))
async def target(m,username=None):
    if m.reply_to_message:
        u=m.reply_to_message.from_user;await store.ensure(u);return u.id,name(u)
    if username:
        r=await store.by_username(username)
        if r:return int(r['user_id']),f"@{r['username']}"
    await m.reply('Ответь на сообщение игрока или укажи username игрока, который уже писал боту.');return None
@router.message(F.text.regexp(RX['promote']))
async def promote(m):
    if not is_admin(m.from_user):return
    mt=RX['promote'].match(m.text);t=await target(m,mt.group(1));
    if t:await store.set_role(t[0],'subadmin');await m.reply(f'🛡 {t[1]} назначен подадмином.')
@router.message(F.text.regexp(RX['demote']))
async def demote(m):
    if not is_admin(m.from_user):return
    mt=RX['demote'].match(m.text);t=await target(m,mt.group(1));
    if t:await store.set_role(t[0],'player');await m.reply(f'👤 {t[1]} больше не подадмин.')
@router.message(F.text.regexp(RX['give']))
async def give(m):
    await store.ensure(m.from_user);role=await store.role(m.from_user.id)
    if not is_admin(m.from_user) and role!='subadmin':return
    t=await target(m);n=int(RX['give'].match(m.text).group(2))
    if not t:return
    if is_admin(m.from_user):_,b=await store.delta(t[0],n);return await m.reply(f'✅ Выдано {n}. Баланс {t[1]}: {b}')
    ok,e,b=await store.subgrant(m.from_user.id,t[0],n);await m.reply(f"{'✅ Выдано. Баланс: '+str(b) if ok else '❌ '+e}")
@router.message(F.text.regexp(RX['take']))
async def take(m):
    if not is_admin(m.from_user):return
    t=await target(m);n=int(RX['take'].match(m.text).group(2));
    if t:ok,b=await store.delta(t[0],-n);await m.reply(f"{'✅ Снято '+str(n) if ok else '❌ Недостаточно'}. Баланс: {b}")
@router.message(F.text.regexp(RX['reset']))
async def reset(m):
    if not is_admin(m.from_user):return
    t=await target(m)
    if t:await store.pool.execute('UPDATE users SET balance=0 WHERE user_id=$1',t[0]);await m.reply(f'🧹 Баланс {t[1]} обнулён.')
@router.message(F.text.regexp(RX['givelevel']))
async def gl(m):
    if not is_admin(m.from_user):return
    t=await target(m);n=int(RX['givelevel'].match(m.text).group(1));
    if t:r=await store.user(t[0]);o,x=await store.set_level(t[0],int(r['level'])+n);await m.reply(f'⬆️ {t[1]}: {o} → {x}')
@router.message(F.text.regexp(RX['takelevel']))
async def tl(m):
    if not is_admin(m.from_user):return
    t=await target(m);n=int(RX['takelevel'].match(m.text).group(1));
    if t:r=await store.user(t[0]);o,x=await store.set_level(t[0],int(r['level'])-n);await m.reply(f'⬇️ {t[1]}: {o} → {x}')
@router.message(F.text.regexp(RX['setlevel']))
async def sl(m):
    if not is_admin(m.from_user):return
    t=await target(m);n=int(RX['setlevel'].match(m.text).group(1));
    if t:o,x=await store.set_level(t[0],n);await m.reply(f'🛠 {t[1]}: {o} → {x}')
async def grant(m,k,title):
    if not is_admin(m.from_user):return
    t=await target(m)
    if t:await store.grant_booster(t[0],k);await m.reply(f'🎁 {t[1]} выдан бустер «{title}».')
@router.message(F.text.regexp(RX['grantminus']))
async def gm(m):await grant(m,'minus','-1 мина')
@router.message(F.text.regexp(RX['grantlife']))
async def gli(m):await grant(m,'life','Вечная удача')
@router.message(F.text.regexp(RX['grant24']))
async def g24(m):await grant(m,'24','Большой куш 24')
@router.message(F.text.regexp(RX['grant48']))
async def g48(m):await grant(m,'48','Большой куш 48')
@router.message(F.text.regexp(RX['goldrain']))
async def rain(m):
    if is_admin(m.from_user):s,e=await store.gold_event();await m.reply(f'🌧✨ Золотой дождь: {s:%H:%M}–{e:%H:%M}. Шанс золотых мин 50%.')
@router.message(F.text.lower()=='пауза')
async def pause(m):
    if is_admin(m.from_user):await store.set_paused(True);await m.reply('⏸ Игры приостановлены.')
@router.message(F.text.lower()=='продолжить')
async def resume(m):
    if is_admin(m.from_user):await store.set_paused(False);await m.reply('▶️ Игры возобновлены.')
@router.message(F.text.lower()=='стопигры')
async def stop(m):
    c,r,b=await store.stop(None if is_admin(m.from_user) else m.from_user.id);await m.reply(f'🛑 Завершено: {c}. Возвращено ставок: {r}. Бустеров: {b}.')

@router.message(F.text.regexp(RX['dice']))
async def dice(m):
    if not await available(m):return
    bet=int(RX['dice'].match(m.text).group(2));x=random.randint(1,6);p=payout25(bet) if x>=4 else 0;ok,e=await store.simple(m.from_user,'dice',bet,p);await m.reply(e if not ok else f"🎲 Выпало {x}. {'Победа' if p else 'Проигрыш'}. Баланс: {await store.balance(m.from_user.id)}")
@router.message(F.text.regexp(RX['coin']))
async def coin(m):
    if not await available(m):return
    q=RX['coin'].match(m.text);bet=int(q.group(2));choice=q.group(3).lower().replace('ё','е');x=random.choice(['орел','решка']);p=payout25(bet) if x==choice else 0;ok,e=await store.simple(m.from_user,'coin',bet,p);await m.reply(e if not ok else f"🪙 Выпало {x}. {'Победа' if p else 'Проигрыш'}. Баланс: {await store.balance(m.from_user.id)}")
@router.message(F.text.regexp(RX['roulette']))
async def roulette(m):
    if not await available(m):return
    q=RX['roulette'].match(m.text);bet=int(q.group(2));raw=q.group(3).lower().replace('ё','е');choice='красное' if raw in {'к','красное'} else 'черное' if raw in {'ч','черное'} else 'зеленое';n=random.randint(0,36);x='зеленое' if n==0 else ('красное' if n%2 else 'черное');p=payout25(bet) if x==choice else 0;ok,e=await store.simple(m.from_user,'roulette',bet,p);await m.reply(e if not ok else f"🎡 {n}, {x}. {'Победа' if p else 'Проигрыш'}. Баланс: {await store.balance(m.from_user.id)}")
@router.message(F.text.regexp(RX['mines']))
async def mines(m):
    if not await available(m):return
    bet=int(RX['mines'].match(m.text).group(2));await store.ensure(m.from_user);reserved=await store.reserve_minus(m.from_user.id);gold=random.random()<(0.5 if await store.gold_active() else 0.005);count=max(1,random.randint(4,6)-(1 if reserved else 0));danger=set(random.sample(range(16),count));gid=secrets.token_hex(4);p=await m.reply('⛏ Подготавливаю поле…');ok,e=await store.create_game(gid,p,m.from_user,'mines',bet,bet,danger,meta={'golden':gold,'minus_reserved':reserved})
    if not ok:
        if reserved:await store.return_minus(m.from_user.id)
        return await p.edit_text(e)
    await p.edit_text(f"{'🌟 ЗОЛОТЫЕ МИНЫ' if gold else '💣 МИНЫ'}\n\nСтавка: {bet}\nМин: {count}\nМожно забрать: {bet}"+('\n🧿 Бустер -1 мина активирован.' if reserved else ''),reply_markup=mines_kb(gid,set(),danger,bet,golden=gold))
@router.callback_query(F.data.startswith('mine:'))
async def mineclick(c):
    _,gid,s=c.data.split(':');g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
    cell=int(s);danger=set(dec(g['danger'],[]));opened=set(dec(g['opened'],[]));meta=dec(g['meta'],{});gold=bool(meta.get('golden'));opened.add(cell)
    if cell in danger:
        await store.update_game(gid,status='lost',opened=sorted(opened));await store.result(g['user_id'],False,int(g['bet']),0);return await c.message.edit_text(f"💥 Мина! Баланс: {await store.balance(g['user_id'])}",reply_markup=mines_kb(gid,opened,danger,0,True,gold))
    await store.quest(g['user_id'],'safe',1);steps=len(opened)
    if gold:
        payout=int(g['bet'])*(1+steps);free=list(set(range(16))-opened-danger)
        if free:danger.add(random.choice(free))
    else:payout=payout25(int(g['bet']),steps)
    await store.update_game(gid,opened=sorted(opened),danger=sorted(danger),payout=payout);await c.message.edit_text(f"{'✨' if gold else '✅'} Безопасно. Плюс {steps*(100 if gold else 25)}%. Можно забрать: {payout}. Мин: {len(danger)}",reply_markup=mines_kb(gid,opened,danger,payout,golden=gold))
@router.callback_query(F.data.startswith('cash:'))
async def cash(c):
    gid=c.data.split(':')[1];g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
    payout=await store.win_bonus(g['user_id'],int(g['payout']));await store.update_game(gid,status='won',payout=payout);await store.delta(g['user_id'],payout);await store.result(g['user_id'],True,int(g['bet']),payout);await store.quest(g['user_id'],'win',1);await store.quest(g['user_id'],'earn',payout-int(g['bet']));meta=dec(g['meta'],{})
    await c.message.edit_text(f"💰 Выигрыш: {payout}. Баланс: {await store.balance(g['user_id'])}",reply_markup=mines_kb(gid,set(dec(g['opened'],[])),set(dec(g['danger'],[])),payout,True,bool(meta.get('golden'))))

@router.message(F.text.regexp(RX['mega']))
async def mega(m):
    if not await available(m):return
    bet=int(RX['mega'].match(m.text).group(2));safe=random.randrange(16);danger=set(range(16))-{safe};gid=secrets.token_hex(4);p=await m.reply('🍀 Запускаю Мега удачу…');ok,e=await store.create_game(gid,p,m.from_user,'mega',bet,bet*10,danger,meta={'safe':safe})
    if not ok:return await p.edit_text(e)
    await p.edit_text(f'🍀 МЕГА УДАЧА\n\nСтавка: {bet}\nПобеда: {bet*10}\nТолько одна безопасная клетка.',reply_markup=mega_kb(gid,safe))
@router.callback_query(F.data.startswith('mega:'))
async def megaclick(c):
    _,gid,s=c.data.split(':');g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
    safe=int(dec(g['meta'],{}).get('safe'));cell=int(s)
    if cell==safe:
        payout=await store.win_bonus(g['user_id'],int(g['bet'])*10);await store.update_game(gid,status='won',opened=[cell],payout=payout);await store.delta(g['user_id'],payout);await store.result(g['user_id'],True,int(g['bet']),payout);await store.quest(g['user_id'],'win',1);await store.quest(g['user_id'],'earn',payout-int(g['bet']));text=f'🍀 Победа! Начислено {payout}. Баланс: {await store.balance(g["user_id"])}'
    else:await store.update_game(gid,status='lost',opened=[cell]);await store.result(g['user_id'],False,int(g['bet']),0);text=f'💣 Проигрыш. Баланс: {await store.balance(g["user_id"])}'
    await c.message.edit_text(text,reply_markup=mega_kb(gid,safe,True))

@router.message(F.text.regexp(RX['joker']))
async def joker(m):
    if not await available(m):return
    bet=int(RX['joker'].match(m.text).group(2));danger={random.randrange(3)};gid=secrets.token_hex(4);p=await m.reply('🃏 Перемешиваю…');ok,e=await store.create_game(gid,p,m.from_user,'joker',bet,bet,danger)
    if not ok:return await p.edit_text(e)
    await p.edit_text(f'🃏 ДЖОКЕР\n\nСтавка: {bet}\nМожно забрать: {bet}',reply_markup=joker_kb(gid,danger,set(),bet))
@router.callback_query(F.data.startswith('joker:'))
async def jokerclick(c):
    _,gid,s=c.data.split(':');g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
    cell=int(s);danger=set(dec(g['danger'],[]));opened=set(dec(g['opened'],[]))
    if cell//3!=len(opened):return await c.answer('Ряд неактивен.',show_alert=True)
    opened.add(cell)
    if cell in danger:await store.update_game(gid,status='lost',opened=sorted(opened));await store.result(g['user_id'],False,int(g['bet']),0);return await c.message.edit_text(f'💀 Череп. Баланс: {await store.balance(g["user_id"])}',reply_markup=joker_kb(gid,danger,opened,0,True))
    await store.quest(g['user_id'],'safe',1);steps=len(opened);payout=payout25(int(g['bet']),steps);cells=list(range(steps*3,steps*3+3));danger.update(random.sample(cells,2 if steps>=4 else 1));await store.update_game(gid,opened=sorted(opened),danger=sorted(danger),payout=payout);await c.message.edit_text(f'🃏 Плюс {steps*25}%. Можно забрать: {payout}.',reply_markup=joker_kb(gid,danger,opened,payout))
@router.callback_query(F.data.startswith('jcash:'))
async def jcash(c):
    gid=c.data.split(':')[1];g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['user_id']:return await c.answer('Недоступно.',show_alert=True)
    payout=await store.win_bonus(g['user_id'],int(g['payout']));await store.update_game(gid,status='won',payout=payout);await store.delta(g['user_id'],payout);await store.result(g['user_id'],True,int(g['bet']),payout);await store.quest(g['user_id'],'win',1);await store.quest(g['user_id'],'earn',payout-int(g['bet']));await c.message.edit_text(f'💰 Выигрыш: {payout}. Баланс: {await store.balance(g["user_id"])}')
@router.message(F.text.regexp(RX['duel']))
async def duel(m):
    if not await available(m):return
    if not m.reply_to_message:return await m.reply('Ответь на сообщение соперника.')
    opp=m.reply_to_message.from_user
    if opp.is_bot or opp.id==m.from_user.id:return await m.reply('Нельзя вызвать этого игрока.')
    bet=int(RX['duel'].match(m.text).group(2));gid=secrets.token_hex(4);p=await m.reply('⚔️ Создаю дуэль…');ok,e=await store.create_game(gid,p,m.from_user,'duel',bet,bet*2,set(),opp=opp.id)
    if not ok:return await p.edit_text(e)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⚔️ Принять',callback_data=f'duel:{gid}'),InlineKeyboardButton(text='❌ Отказ',callback_data=f'decline:{gid}')]])
    await p.edit_text(f'⚔️ {name(m.from_user)} вызывает {name(opp)}. Ставка каждого: {bet}',reply_markup=kb)
@router.callback_query(F.data.startswith('duel:'))
async def duelaccept(c):
    gid=c.data.split(':')[1];g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['opponent_id']:return await c.answer('Недоступно.',show_alert=True)
    await store.ensure(c.from_user);ok,e=await store.validate(c.from_user,int(g['bet']))
    if not ok:return await c.answer(e,show_alert=True)
    await store.delta(c.from_user.id,-int(g['bet']));await store.quest(c.from_user.id,'play',1);await store.quest(c.from_user.id,'spend',int(g['bet']))
    a=b=0
    while a==b:a,b=random.randint(1,6),random.randint(1,6)
    winner=int(g['user_id']) if a>b else c.from_user.id;loser=c.from_user.id if winner==int(g['user_id']) else int(g['user_id']);pot=await store.win_bonus(winner,int(g['bet'])*2)
    await store.delta(winner,pot);await store.result(winner,True,int(g['bet']),pot);await store.result(loser,False,int(g['bet']),0);await store.quest(winner,'win',1);await store.quest(winner,'earn',pot-int(g['bet']));await store.update_game(gid,status='won',payout=pot,meta={'a':a,'b':b,'winner':winner});await c.message.edit_text(f'⚔️ Дуэль: {a} против {b}. Победитель получил {pot}.')
@router.callback_query(F.data.startswith('decline:'))
async def decline(c):
    gid=c.data.split(':')[1];g=await store.game(gid)
    if not g or g['status']!='active' or c.from_user.id!=g['opponent_id']:return await c.answer('Недоступно.',show_alert=True)
    await store.update_game(gid,status='cancelled');await store.delta(g['user_id'],int(g['bet']));await c.message.edit_text('❌ Дуэль отклонена. Ставка возвращена.')
@router.callback_query(F.data=='noop')
async def noop(c):await c.answer('Кнопка неактивна.')

async def main():
    if not TOKEN:raise RuntimeError('Не найден BOT_TOKEN')
    if not DATABASE_URL:raise RuntimeError('Не найден DATABASE_URL')
    await store.init();bot=Bot(TOKEN);dp=Dispatcher();dp.include_router(router);await bot.set_my_commands([BotCommand(command='start',description='Запустить'),BotCommand(command='help',description='Команды'),BotCommand(command='adminhelp',description='Админ-команды')])
    try:await dp.start_polling(bot)
    finally:await store.close()
if __name__=='__main__':asyncio.run(main())
