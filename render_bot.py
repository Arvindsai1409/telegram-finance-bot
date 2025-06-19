import os
import logging
import asyncio
import uuid
import time

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fix_database_url(url):
    """Fix common DATABASE_URL format issues"""
    if not url:
        return None
    
    # Convert postgres:// to postgresql:// for newer psycopg2
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
        logger.info("Fixed DATABASE_URL format")
    
    return url

def test_database_connection():
    """Test database connection with retry logic"""
    db_url = fix_database_url(os.environ.get('DATABASE_URL'))
    
    if not db_url:
        logger.error("DATABASE_URL environment variable not found")
        return False
    
    if not PSYCOPG_AVAILABLE:
        logger.error("psycopg2 library not available")
        return False
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info(f"Testing database connection (attempt {attempt + 1}/{max_retries})")
            conn = psycopg2.connect(db_url, connect_timeout=10)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            conn.close()
            logger.info("Database connection successful")
            return True
        except Exception as e:
            logger.warning(f"Connection attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2)
    
    logger.error("All database connection attempts failed")
    return False

def initialize_database():
    """Initialize database tables"""
    db_url = fix_database_url(os.environ.get('DATABASE_URL'))
    
    if not db_url or not PSYCOPG_AVAILABLE:
        return False
    
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor()
        
        # Create transactions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                tx_id VARCHAR(20) NOT NULL,
                tx_type VARCHAR(10) NOT NULL CHECK (tx_type IN ('income', 'expense')),
                amount DECIMAL(12,2) NOT NULL CHECK (amount > 0),
                description TEXT NOT NULL,
                user_name VARCHAR(100) NOT NULL,
                user_id VARCHAR(50) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create members table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id SERIAL PRIMARY KEY,
                user_id VARCHAR(50) UNIQUE NOT NULL,
                username VARCHAR(100),
                first_name VARCHAR(100) NOT NULL,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Create indexes for better performance
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(tx_type);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_members_user ON members(user_id);")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info("Database tables initialized successfully")
        return True
        
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        return False

def execute_db_query(query, params=None, fetch=False):
    """Execute database query with error handling"""
    db_url = fix_database_url(os.environ.get('DATABASE_URL'))
    
    if not db_url or not PSYCOPG_AVAILABLE:
        return None
    
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor if fetch else None)
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        result = None
        if fetch:
            result = cursor.fetchall()
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return result
        
    except Exception as e:
        logger.error(f"Database query failed: {str(e)}")
        return None

def add_member(user_id, username, first_name):
    """Add or update member in database"""
    query = """
        INSERT INTO members (user_id, username, first_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
        username = EXCLUDED.username,
        first_name = EXCLUDED.first_name
    """
    result = execute_db_query(query, (user_id, username or '', first_name))
    return result is not None

def add_transaction(tx_type, amount, description, user_name, user_id):
    """Add transaction to database"""
    tx_id = str(uuid.uuid4())[:8]
    query = """
        INSERT INTO transactions (tx_id, tx_type, amount, description, user_name, user_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    result = execute_db_query(query, (tx_id, tx_type, amount, description, user_name, user_id))
    
    if result is not None:
        logger.info(f"Added {tx_type}: ₹{amount} by {user_name}")
        return tx_id
    return ""

def get_balance():
    """Get financial balance from database"""
    income_query = "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE tx_type = 'income'"
    expense_query = "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE tx_type = 'expense'"
    
    income_result = execute_db_query(income_query, fetch=True)
    expense_result = execute_db_query(expense_query, fetch=True)
    
    if income_result is not None and expense_result is not None:
        income = float(income_result[0][0]) if income_result else 0.0
        expenses = float(expense_result[0][0]) if expense_result else 0.0
        
        return {
            'income': income,
            'expenses': expenses,
            'balance': income - expenses
        }
    
    return {'income': 0.0, 'expenses': 0.0, 'balance': 0.0}

def get_recent_transactions(limit=10):
    """Get recent transactions from database"""
    query = """
        SELECT tx_id, tx_type, amount, description, user_name, created_at
        FROM transactions
        ORDER BY created_at DESC
        LIMIT %s
    """
    result = execute_db_query(query, (limit,), fetch=True)
    
    if result:
        return [dict(row) for row in result]
    return []

class FinanceBot:
    def __init__(self, token):
        if not TELEGRAM_AVAILABLE:
            raise Exception("Telegram library not available")
        
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
    
    def setup_handlers(self):
        """Setup command handlers"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_cmd))
        self.application.add_handler(CommandHandler("income", self.income))
        self.application.add_handler(CommandHandler("expense", self.expense))
        self.application.add_handler(CommandHandler("balance", self.balance))
        self.application.add_handler(CommandHandler("history", self.history))
    
    def parse_command(self, args):
        """Parse command arguments"""
        if not args or len(args) < 2:
            return None, None, "Usage: /command <amount> <description>"
        
        try:
            amount = float(args[0])
            if amount <= 0:
                return None, None, "Amount must be positive"
            
            description = ' '.join(args[1:])
            if len(description.strip()) == 0:
                return None, None, "Description cannot be empty"
            
            return amount, description, None
        except ValueError:
            return None, None, "Invalid amount format"
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user
        add_member(str(user.id), user.username, user.first_name or "User")
        
        welcome_text = """🏦 Finance Tracker Bot

Track your group's finances easily!

Commands:
💰 /income 5000 Salary payment
💸 /expense 1200 Monthly rent
📊 /balance - Current balance
📋 /history - Recent transactions
❓ /help - Show this help

Start tracking your finances now!"""
        
        await update.message.reply_text(welcome_text)
    
    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await self.start(update, context)
    
    async def income(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /income command"""
        user = update.effective_user
        amount, description, error = self.parse_command(context.args)
        
        if error:
            await update.message.reply_text(f"❌ {error}")
            return
        
        # Add member
        add_member(str(user.id), user.username, user.first_name or "User")
        
        # Add transaction
        tx_id = add_transaction('income', amount, description, user.first_name or "User", str(user.id))
        
        if tx_id:
            balance = get_balance()
            response = f"""✅ Income Added Successfully!

💰 Amount: ₹{amount:,.2f}
📝 Description: {description}
👤 Added by: {user.first_name or 'User'}
🏦 New Balance: ₹{balance['balance']:,.2f}
🆔 Transaction ID: {tx_id}"""
            
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ Failed to add income. Please try again.")
    
    async def expense(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /expense command"""
        user = update.effective_user
        amount, description, error = self.parse_command(context.args)
        
        if error:
            await update.message.reply_text(f"❌ {error}")
            return
        
        # Add member
        add_member(str(user.id), user.username, user.first_name or "User")
        
        # Add transaction
        tx_id = add_transaction('expense', amount, description, user.first_name or "User", str(user.id))
        
        if tx_id:
            balance = get_balance()
            response = f"""✅ Expense Added Successfully!

💸 Amount: ₹{amount:,.2f}
📝 Description: {description}
👤 Added by: {user.first_name or 'User'}
🏦 New Balance: ₹{balance['balance']:,.2f}
🆔 Transaction ID: {tx_id}"""
            
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("❌ Failed to add expense. Please try again.")
    
    async def balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        balance = get_balance()
        
        response = f"""💰 Financial Summary

📈 Total Income: ₹{balance['income']:,.2f}
📉 Total Expenses: ₹{balance['expenses']:,.2f}
🏦 Current Balance: ₹{balance['balance']:,.2f}

{'💚 Positive balance' if balance['balance'] >= 0 else '🔴 Negative balance'}"""
        
        await update.message.reply_text(response)
    
    async def history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /history command"""
        transactions = get_recent_transactions(10)
        
        if not transactions:
            await update.message.reply_text("📋 No transactions found yet.\n\nStart by adding income or expenses!")
            return
        
        response = "📋 Recent Transactions\n" + "="*30 + "\n\n"
        
        for tx in transactions:
            emoji = "📈" if tx['tx_type'] == 'income' else "📉"
            sign = "+" if tx['tx_type'] == 'income' else "-"
            date_str = tx['created_at'].strftime("%m/%d %H:%M") if tx['created_at'] else "Unknown"
            
            response += f"{emoji} {sign}₹{tx['amount']:,.2f}\n"
            response += f"📝 {tx['description']}\n"
            response += f"👤 {tx['user_name']}\n"
            response += f"📅 {date_str}\n"
            response += f"🆔 {tx['tx_id']}\n\n"
        
        await update.message.reply_text(response)
    
    async def run(self):
        """Start the bot"""
        logger.info("Starting Telegram bot polling...")
        await self.application.run_polling(drop_pending_updates=True)

async def main():
    """Main application entry point"""
    logger.info("🏦 Starting Finance Tracker Bot...")
    
    # Check environment variables
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if not bot_token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not found")
        return
    
    if not TELEGRAM_AVAILABLE:
        logger.error("Telegram library not available")
        return
    
    if not PSYCOPG_AVAILABLE:
        logger.error("psycopg2 library not available")
        return
    
    # Test database connection
    if not test_database_connection():
        logger.error("Database connection failed")
        return
    
    # Initialize database
    if not initialize_database():
        logger.error("Database initialization failed")
        return
    
    # Test database operations
    try:
        balance = get_balance()
        logger.info(f"Database operational - Current balance: ₹{balance['balance']:,.2f}")
    except Exception as e:
        logger.error(f"Database test failed: {str(e)}")
        return
    
    # Start bot
    try:
        bot = FinanceBot(bot_token)
        logger.info("Bot initialized successfully")
        logger.info("✅ All systems ready - Bot is now running!")
        await bot.run()
    except Exception as e:
        logger.error(f"Bot startup failed: {str(e)}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
