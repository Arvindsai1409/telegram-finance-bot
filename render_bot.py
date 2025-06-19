#!/usr/bin/env python3
"""
Render Deployment Bot - Production-ready Telegram Finance Bot
Optimized for Render.com deployment with PostgreSQL database
"""

import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional
import uuid

# Database imports
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

# Telegram imports
try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("Telegram library not available - running in database-only mode")

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
logger = logging.getLogger(__name__)

# Database setup
Base = declarative_base()


class Transaction(Base):
    __tablename__ = 'transactions'

    id = Column(Integer, primary_key=True)
    transaction_id = Column(String(50), unique=True, nullable=False)
    type = Column(String(20), nullable=False)
    amount = Column(Float, nullable=False)
    description = Column(Text, nullable=False)
    added_by = Column(String(100), nullable=False)
    user_id = Column(String(50), nullable=False)
    photo_id = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class GroupMember(Base):
    __tablename__ = 'group_members'

    id = Column(Integer, primary_key=True)
    user_id = Column(String(50), unique=True, nullable=False)
    username = Column(String(100), nullable=True)
    first_name = Column(String(100), nullable=False)
    joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# Database configuration
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://')

engine = create_engine(DATABASE_URL) if DATABASE_URL else None
SessionLocal = sessionmaker(autocommit=False, autoflush=False,
                            bind=engine) if engine else None


def create_tables():
    """Create all database tables"""
    if engine:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    else:
        logger.error("No database connection available")


def get_db_session() -> Session:
    """Get database session"""
    if SessionLocal:
        return SessionLocal()
    else:
        raise Exception("Database not configured")


class DatabaseManager:
    """Database operations manager"""

    @staticmethod
    def add_member(user_id: str, username: Optional[str],
                   first_name: str) -> bool:
        """Add or update a group member"""
        try:
            session = get_db_session()

            # Check if member exists
            existing = session.query(GroupMember).filter_by(
                user_id=user_id).first()

            if existing:
                existing.username = username
                existing.first_name = first_name
            else:
                member = GroupMember(user_id=user_id,
                                     username=username,
                                     first_name=first_name)
                session.add(member)

            session.commit()
            session.close()
            return True

        except Exception as e:
            logger.error(f"Error adding member: {e}")
            return False

    @staticmethod
    def add_transaction(transaction_type: str,
                        amount: float,
                        description: str,
                        added_by: str,
                        user_id: str,
                        photo_id: Optional[str] = None) -> str:
        """Add a transaction to the database"""
        try:
            session = get_db_session()

            transaction_id = str(uuid.uuid4())[:8]

            transaction = Transaction(transaction_id=transaction_id,
                                      type=transaction_type,
                                      amount=amount,
                                      description=description,
                                      added_by=added_by,
                                      user_id=user_id,
                                      photo_id=photo_id)

            session.add(transaction)
            session.commit()
            session.close()

            logger.info(f"Added {transaction_type}: ₹{amount} - {description}")
            return transaction_id

        except Exception as e:
            logger.error(f"Error adding transaction: {e}")
            return ""

    @staticmethod
    def get_balance() -> dict:
        """Calculate current group balance"""
        try:
            session = get_db_session()

            income_result = session.execute(
                text(
                    "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'income'"
                )).scalar()

            expense_result = session.execute(
                text(
                    "SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE type = 'expense'"
                )).scalar()

            session.close()

            income = float(income_result) if income_result else 0.0
            expenses = float(expense_result) if expense_result else 0.0
            balance = income - expenses

            return {'income': income, 'expenses': expenses, 'balance': balance}

        except Exception as e:
            logger.error(f"Error calculating balance: {e}")
            return {'income': 0.0, 'expenses': 0.0, 'balance': 0.0}

    @staticmethod
    def get_transaction_history(limit: int = 10) -> list:
        """Get recent transaction history"""
        try:
            session = get_db_session()

            transactions = session.query(Transaction)\
                .order_by(Transaction.created_at.desc())\
                .limit(limit)\
                .all()

            history = []
            for t in transactions:
                history.append({
                    'id': t.transaction_id,
                    'type': t.type,
                    'amount': t.amount,
                    'description': t.description,
                    'added_by': t.added_by,
                    'created_at': t.created_at
                })

            session.close()
            return history

        except Exception as e:
            logger.error(f"Error getting history: {e}")
            return []


class TelegramBot:
    """Main Telegram bot class"""

    def __init__(self, token: str):
        self.token = token
        self.application = Application.builder().token(token).build()
        self.setup_handlers()

    def setup_handlers(self):
        """Setup command and message handlers"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("income", self.income))
        self.application.add_handler(CommandHandler("expense", self.expense))
        self.application.add_handler(CommandHandler("balance", self.balance))
        self.application.add_handler(CommandHandler("history", self.history))
        self.application.add_handler(CommandHandler("members", self.members))
        self.application.add_handler(
            CommandHandler("statement", self.statement))
        self.application.add_handler(CommandHandler("reset", self.reset))

        # Photo handler
        self.application.add_handler(
            MessageHandler(filters.PHOTO, self.handle_photo))

    def parse_amount_description(self, text: str) -> tuple:
        """Parse amount and description from command text"""
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

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user = update.effective_user

        # Add member to database
        DatabaseManager.add_member(str(user.id), user.username, user.first_name
                                   or "Unknown")

        welcome_message = """🏦 Welcome to Group Finance Tracker Bot!

Available commands:
💰 /income <amount> <description> - Add group income
💸 /expense <amount> <description> - Add group expense
📊 /balance - Show current group balance
📋 /history - Show recent transactions
👥 /members - Show group members
📄 /statement - Generate Excel-ready statement
🔄 /reset - Reset all data (admin only)
❓ /help - Show this help message

📸 You can also send a photo with expense amount in caption!

Example: /income 1000 Monthly contribution
Example: /expense 250 Groceries"""

        await update.message.reply_text(welcome_message)

    async def help_command(self, update: Update,
                           context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        await self.start(update, context)

    async def income(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /income command"""
        user = update.effective_user
        text = ' '.join(context.args)

        # Add member
        DatabaseManager.add_member(str(user.id), user.username, user.first_name
                                   or "Unknown")

        amount, description = self.parse_amount_description(text)

        if amount is None:
            await update.message.reply_text(f"❌ Error: {description}")
            return

        # Add transaction
        transaction_id = DatabaseManager.add_transaction(
            'income', amount, description, user.first_name or "Unknown",
            str(user.id))

        if transaction_id:
            balance_info = DatabaseManager.get_balance()

            response = f"""✅ Income added successfully!
💰 Amount: ₹{amount:.2f}
📝 Description: {description}
👤 Added by: {user.first_name}
🏦 Current Balance: ₹{balance_info['balance']:.2f}
🆔 Transaction ID: {transaction_id}"""

            await update.message.reply_text(response)
        else:
            await update.message.reply_text(
                "❌ Failed to add income. Please try again.")

    async def expense(self, update: Update,
                      context: ContextTypes.DEFAULT_TYPE):
        """Handle /expense command"""
        user = update.effective_user
        text = ' '.join(context.args)

        # Add member
        DatabaseManager.add_member(str(user.id), user.username, user.first_name
                                   or "Unknown")

        amount, description = self.parse_amount_description(text)

        if amount is None:
            await update.message.reply_text(f"❌ Error: {description}")
            return

        # Add transaction
        transaction_id = DatabaseManager.add_transaction(
            'expense', amount, description, user.first_name or "Unknown",
            str(user.id))

        if transaction_id:
            balance_info = DatabaseManager.get_balance()

            response = f"""✅ Expense added successfully!
💸 Amount: ₹{amount:.2f}
📝 Description: {description}
👤 Added by: {user.first_name}
🏦 Current Balance: ₹{balance_info['balance']:.2f}
🆔 Transaction ID: {transaction_id}"""

            await update.message.reply_text(response)
        else:
            await update.message.reply_text(
                "❌ Failed to add expense. Please try again.")

    async def handle_photo(self, update: Update,
                           context: ContextTypes.DEFAULT_TYPE):
        """Handle photo messages with expense captions"""
        user = update.effective_user
        caption = update.message.caption or ""

        if not caption:
            await update.message.reply_text(
                "📸 Please add expense amount and description in photo caption!\nExample: 250 Coffee and snacks"
            )
            return

        # Add member
        DatabaseManager.add_member(str(user.id), user.username, user.first_name
                                   or "Unknown")

        amount, description = self.parse_amount_description(caption)

        if amount is None:
            await update.message.reply_text(
                f"❌ Photo Caption Error: {description}")
            return

        # Get photo file ID
        photo_id = update.message.photo[-1].file_id

        # Add transaction
        transaction_id = DatabaseManager.add_transaction(
            'expense', amount, description, user.first_name or "Unknown",
            str(user.id), photo_id)

        if transaction_id:
            balance_info = DatabaseManager.get_balance()

            response = f"""✅ Photo expense added successfully!
💸 Amount: ₹{amount:.2f}
📝 Description: {description}
👤 Added by: {user.first_name}
📸 Receipt photo saved
🏦 Current Balance: ₹{balance_info['balance']:.2f}
🆔 Transaction ID: {transaction_id}"""

            await update.message.reply_text(response)
        else:
            await update.message.reply_text(
                "❌ Failed to add photo expense. Please try again.")

    async def balance(self, update: Update,
                      context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        balance_info = DatabaseManager.get_balance()

        response = f"""💰 Group Financial Status:
📈 Total Income: ₹{balance_info['income']:.2f}
📉 Total Expenses: ₹{balance_info['expenses']:.2f}
🏦 Current Balance: ₹{balance_info['balance']:.2f}"""

        await update.message.reply_text(response)

    async def history(self, update: Update,
                      context: ContextTypes.DEFAULT_TYPE):
        """Handle /history command"""
        history = DatabaseManager.get_transaction_history(10)

        if not history:
            await update.message.reply_text("📋 No transactions found.")
            return

        response = "📋 Recent Transaction History\n━━━━━━━━━━━━━━━━━━━━━━\n"

        for t in history:
            emoji = "📈" if t['type'] == 'income' else "📉"
            sign = "+" if t['type'] == 'income' else "-"
            date_str = t['created_at'].strftime("%m-%d %H:%M")

            response += f"{emoji} {sign}₹{t['amount']:.2f}\n"
            response += f"📝 {t['description']}\n"
            response += f"👤 {t['added_by']}\n"
            response += f"📅 {date_str}\n"
            response += f"🆔 {t['id']}\n━━━━━━━━━━━━━━━━━━━━━━\n"

        await update.message.reply_text(response)

    async def members(self, update: Update,
                      context: ContextTypes.DEFAULT_TYPE):
        """Handle /members command"""
        try:
            session = get_db_session()
            members = session.query(GroupMember).all()
            session.close()

            if not members:
                await update.message.reply_text("👥 No members found.")
                return

            response = "👥 Group Members:\n━━━━━━━━━━━━━━━━━━━━━━\n"

            for i, member in enumerate(members, 1):
                username = f"@{member.username}" if member.username else "No username"
                join_date = member.joined_at.strftime("%Y-%m-%d")

                response += f"{i}. {member.first_name}\n"
                response += f"   {username}\n"
                response += f"   Joined: {join_date}\n━━━━━━━━━━━━━━━━━━━━━━\n"

            await update.message.reply_text(response)

        except Exception as e:
            logger.error(f"Error getting members: {e}")
            await update.message.reply_text("❌ Error retrieving members.")

    async def statement(self, update: Update,
                        context: ContextTypes.DEFAULT_TYPE):
        """Handle /statement command"""
        history = DatabaseManager.get_transaction_history(50)

        if not history:
            await update.message.reply_text("📄 No transactions for statement.")
            return

        # Generate Excel-ready format with pipe separators
        statement = "📄 Excel-Ready Financial Statement:\n```\n"
        statement += "Date|Type|Amount|Description|Added By|Transaction ID\n"
        statement += "---|---|---|---|---|---\n"

        for t in history:
            date_str = t['created_at'].strftime("%Y-%m-%d %H:%M")
            amount_str = f"₹{t['amount']:.2f}" if t[
                'type'] == 'income' else f"-₹{t['amount']:.2f}"
            type_str = "INCOME" if t['type'] == 'income' else "EXPENSE"

            statement += f"{date_str}|{type_str}|{amount_str}|{t['description']}|{t['added_by']}|{t['id']}\n"

        statement += "```\n\n💡 Copy this data and paste into Excel/Google Sheets for analysis!"

        await update.message.reply_text(statement, parse_mode='Markdown')

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reset command"""
        await update.message.reply_text(
            "⚠️ Reset functionality disabled in production for data safety.")

    async def run(self):
        """Start the bot"""
        logger.info("Starting Telegram bot...")
        await self.application.run_polling()


async def main():
    """Main function"""
    logger.info("🏦 Starting Render Telegram Finance Bot...")

    # Create database tables
    create_tables()

    # Get bot token
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')

    if not bot_token:
        logger.error("❌ TELEGRAM_BOT_TOKEN environment variable not set")
        return

    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL environment variable not set")
        return

    if not TELEGRAM_AVAILABLE:
        logger.error("❌ Telegram library not available")
        return

    # Test database connection
    try:
        balance = DatabaseManager.get_balance()
        logger.info(
            f"✅ Database connected - Current balance: ₹{balance['balance']:.2f}"
        )
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        return

    # Start bot
    bot = TelegramBot(bot_token)
    logger.info("🚀 Bot starting with full functionality...")

    try:
        await bot.run()
    except Exception as e:
        logger.error(f"Bot error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
