import os
import logging
import asyncio
from datetime import datetime
import uuid

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
except ImportError:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

def get_connection():
    if DATABASE_URL and psycopg2:
        try:
            return psycopg2.connect(DATABASE_URL)
        except:
            return None
    return None

def init_db():
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                transaction_id VARCHAR(50) UNIQUE,
                type VARCHAR(20),
                amount FLOAT,
                description TEXT,
                added_by VARCHAR(100),
                user_id VARCHAR(50),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        cur.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(50) UNIQUE,
                username VARCHAR(100),
                first_name VARCHAR(100),
                joined_at TIMESTAMP DEFAULT NOW()
            )
        """)
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DB init error: {e}")
        return False

def add_member(user_id, username, first_name):
    conn = get_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO members (user_id, username, first_name)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name
        """, (user_id, username or '', first_name))
        
        conn.commit()
        cur.close()
        conn.close()
        return True
    except:
        return False

def add_transaction(tx_type, amount, desc, added_by, user_id):
    conn = get_connection()
    if not conn:
        return ""
    
    try:
        cur = conn.cursor()
        tx_id = str(uuid.uuid4())[:8]
        
        cur.execute("""
            INSERT INTO transactions (transaction_id, type, amount, description, added_by, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (tx_id, tx_type, amount, desc, added_by, user_id))
        
        conn.commit()
        cur.close()
        conn.close()
        return tx_id
    except:
        return ""

def get_balance():
    conn = get_connection()
    if not conn:
        return {'income': 0, 'expenses': 0, 'balance': 0}
    
    try:
        cur = conn.cursor()
        
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'income'")
        income = float(cur.fetchone()[0])
        
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'expense'")
        expenses = float(cur.fetchone()[0])
        
        cur.close()
        conn.close()
        
        return {'income': income, 'expenses': expenses, 'balance': income - expenses}
    except:
        return {'income': 0, 'expenses': 0, 'balance': 0}

def get_history(limit=10):
    conn = get_connection()
    if not conn:
        return []
    
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT transaction_id, type, amount, description, added_by, created_at
            FROM transactions
            ORDER BY created_at DESC
            LIMIT %s
        """, (limit,))
        
        result = cur.fetchall()
        cur.close()
        conn.close()
        
        return [dict(r) for r in result]
    except:
        return []

def parse_command(text):
    try:
        parts = text.strip().split(' ', 1)
        if len(parts) < 2:
            return None, "Please provide amount and description"
        
        amount = float(parts[0])
        description = parts[1]
        
        if amount <= 0:
            return None, "Amount must be positive"
        
        return amount, description
    except ValueError:
        return None, "Invalid amount format"

class Bot:
    def __init__(self, token):
        self.app = Application.builder().token(token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("income", self.income))
        self.app.add_handler(CommandHandler("expense", self.expense))
        self.app.add_handler(CommandHandler("balance", self.balance))
        self.app.add_handler(CommandHandler("history", self.history))
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        add_member(str(user.id), user.username, user.first_name or "User")
        
        msg = """🏦 Finance Tracker Bot

Commands:
/income 1000 Monthly salary
/expense 250 Groceries
/balance - Show current balance
/history - Show recent transactions"""
        
        await update.message.reply_text(msg)
    
    async def income(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = ' '.join(context.args)
        
        add_member(str(user.id), user.username, user.first_name or "User")
        
        amount, desc = parse_command(text)
        if amount is None:
            await update.message.reply_text(f"Error: {desc}")
            return
        
        tx_id = add_transaction('income', amount, desc, user.first_name or "User", str(user.id))
        
        if tx_id:
            balance = get_balance()
            msg = f"""✅ Income added!
💰 Amount: ₹{amount:.2f}
📝 {desc}
🏦 Balance: ₹{balance['balance']:.2f}"""
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("Failed to add income")
    
    async def expense(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        text = ' '.join(context.args)
        
        add_member(str(user.id), user.username, user.first_name or "User")
        
        amount, desc = parse_command(text)
        if amount is None:
            await update.message.reply_text(f"Error: {desc}")
            return
        
        tx_id = add_transaction('expense', amount, desc, user.first_name or "User", str(user.id))
        
        if tx_id:
            balance = get_balance()
            msg = f"""✅ Expense added!
💸 Amount: ₹{amount:.2f}
📝 {desc}
🏦 Balance: ₹{balance['balance']:.2f}"""
            await update.message.reply_text(msg)
        else:
            await update.message.reply_text("Failed to add expense")
    
    async def balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        balance = get_balance()
        msg = f"""💰 Financial Status:
📈 Income: ₹{balance['income']:.2f}
📉 Expenses: ₹{balance['expenses']:.2f}
🏦 Balance: ₹{balance['balance']:.2f}"""
        await update.message.reply_text(msg)
    
    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = get_history(10)
        
        if not history:
            await update.message.reply_text("No transactions found")
            return
        
        msg = "📋 Recent Transactions:\n\n"
        
        for t in history:
            emoji = "📈" if t['type'] == 'income' else "📉"
            sign = "+" if t['type'] == 'income' else "-"
            date_str = t['created_at'].strftime("%m-%d %H:%M") if t['created_at'] else ""
            
            msg += f"{emoji} {sign}₹{t['amount']:.2f}\n"
            msg += f"📝 {t['description']}\n"
            msg += f"👤 {t['added_by']}\n"
            msg += f"📅 {date_str}\n\n"
        
        await update.message.reply_text(msg)
    
    async def run(self):
        logger.info("Starting bot...")
        await self.app.run_polling()

async def main():
    logger.info("Finance Bot Starting...")
    
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("No bot token")
        return
    
    if not DATABASE_URL:
        logger.error("No database URL")
        return
    
    if not init_db():
        logger.error("Database init failed")
        return
    
    # Test database
    balance = get_balance()
    logger.info(f"Database ready - Balance: ₹{balance['balance']:.2f}")
    
    bot = Bot(token)
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())