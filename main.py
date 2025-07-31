#!/usr/bin/env python
# pylint: disable=unused-argument
"""
Skill Assessment Bot with industry selection, role selection, and questionnaire
"""

import json
import random

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler
)

from dotenv import load_dotenv
from os import getenv, listdir
from os.path import join, exists

from spidergram import generate_spidergram

import smtplib
from email.mime.text import MIMEText

# Define conversation states
INDUSTRY, ROLE, TEAM_SIZE, PERSON_COST, QUESTION, OPEN_QUESTION = range(6)

class Test:
    def __init__(self, userid: int):
        self.userid = userid
        self.score: dict[str, int] = {}  # category: score
        self.role: str = None
        self.industry: str = None
        self.team_size: int = None
        self.person_cost: str = None
        self.questions_left: list[tuple[str, str]] = []  # (category_id, question)
        self.open_questions_left: list[str] = []  # List of open questions
        self.current_category: str = None
        self.answers: dict[str, int] = {}
        self.open_answers: dict[str, str] = {}

class QuestionCategory:
    def __init__(self, display: str, questions: list[str] | None = None):
        self.display_name = display
        self.questions = questions or []
    def __repr__(self):
        return f"QuestionCategory({self.display_name}, {self.questions})"

class Role:
    def __init__(self, display: str, questions: dict[str, QuestionCategory] | None = None, open_questions: list[str] | None = None):
        self.display_name = display
        self.questions = questions or {}
        self.open_questions = open_questions or []
    def __repr__(self):
        return f"Role({self.display_name}, {self.questions}, open_questions={self.open_questions})"

class Settings:
    config: dict[str, str] = {}
    locales: dict[str, dict[str, str]] = {}
    ongoing_tests: dict[int, Test] = {}
    roles: dict[str, Role] = {}
    industries: list[str] = []
    
    @classmethod
    def get_config(cls, file: str = "config.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                cls.config = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError("Config file not found.")
    
    @classmethod
    def load_locales(cls, dir: str):
        for filename in listdir(dir):
            if filename.endswith(".json"):
                name = filename[:-5]
                try:
                    with open(join(dir, filename), "r", encoding="utf-8") as f:
                        cls.locales[name] = json.load(f)
                except Exception as e:
                    print(f"Error loading locale {filename}: {e}")
    
    @classmethod
    def get_locale(cls, string: str, locale: str = "ru_RU"):
        return cls.locales.get(locale, {}).get(string, string)
    
    @classmethod
    def get_questions(cls, file: str):
        try:
            with open(file, "r", encoding="utf-8") as f:
                content = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError("Question file not found.")
        
        roles: dict[str, str] = content.get("roles", {})
        categories: dict[str, str] = content.get("categories", {})
        open_questions: list[str] = content.get("open_questions", [])
        
        cls.roles = {role_id: Role(display_name) for role_id, display_name in roles.items()}
        
        for role_id in cls.roles:
            role_data = content.get(role_id, {})
            category_obj: dict[str, QuestionCategory] = {}
            
            for cat_id, questions in role_data.items():
                if cat_id in categories:
                    category_obj[cat_id] = QuestionCategory(
                        display=categories[cat_id],
                        questions=questions
                    )
            
            cls.roles[role_id].questions = category_obj
            cls.roles[role_id].open_questions = open_questions
    
    @classmethod
    def load_industries(cls, file: str = "industries.txt"):
        if exists(file):
            with open(file, "r", encoding="utf-8") as f:
                cls.industries = [line.strip() for line in f if line.strip()]
        else:
            raise FileNotFoundError(f"Нет файла индустрий {file}")
    
    @classmethod
    def get_score_keyboard(cls):
        return [[str(i) for i in range(1, 11)]]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        Settings.get_locale("start_reply"),
        reply_markup=ReplyKeyboardMarkup([["/starttest"]], resize_keyboard=True, one_time_keyboard=True)
    )

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(Settings.get_locale("about"))

async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the test by asking for industry"""
    # Create keyboard with industries
    keyboard = [[industry] for industry in Settings.industries]
    await update.message.reply_text(
        Settings.get_locale("industry_select"),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return INDUSTRY

async def receive_industry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store industry and ask for role"""
    user_industry = update.message.text
    context.user_data['industry'] = user_industry
    
    # Create keyboard with roles
    keyboard = [[role.display_name] for role in Settings.roles.values()]
    await update.message.reply_text(
        Settings.get_locale("industry_selected").format(user_industry),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return ROLE

async def receive_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store role and ask for team size"""
    user_role_display = update.message.text
    
    # Find role ID from display name
    role_id = None
    for rid, role in Settings.roles.items():
        if role.display_name == user_role_display:
            role_id = rid
            break
    
    if not role_id:
        await update.message.reply_text(Settings.get_locale("error_wrongrole"))
        return ROLE
    
    # Create test instance
    user_id = update.effective_user.id
    test = Test(user_id)
    test.industry = context.user_data['industry']
    test.role = role_id
    
    # Store test
    Settings.ongoing_tests[user_id] = test
    
    # Ask for team size
    await update.message.reply_text(Settings.get_locale("team_size_question"))
    return TEAM_SIZE

async def receive_team_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store team size and ask for person cost (optional)"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await update.message.reply_text(Settings.get_locale("error_noactivetest"))
        return ConversationHandler.END
    
    try:
        team_size = int(update.message.text)
        if team_size <= 0:
            raise ValueError
        test.team_size = team_size
    except (ValueError, TypeError):
        await update.message.reply_text(Settings.get_locale("error_positive_number"))
        return TEAM_SIZE
    
    # Ask for average person cost (optional)
    await update.message.reply_text(
        Settings.get_locale("person_cost_question"),
        reply_markup=ReplyKeyboardMarkup([["/skip"]], resize_keyboard=True))
    return PERSON_COST

async def receive_person_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store person cost and start the questionnaire"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await update.message.reply_text(Settings.get_locale("error_noactivetest"))
        return ConversationHandler.END
    
    if update.message.text != "/skip":
        test.person_cost = update.message.text
    
    # Prepare all questions
    all_questions = []
    role_data = Settings.roles[test.role]
    for cat_id, category in role_data.questions.items():
        test.score[cat_id] = 0  # Initialize score for category
        for question in category.questions:
            all_questions.append((cat_id, question))
    
    # Randomize question order
    random.shuffle(all_questions)
    test.questions_left = all_questions
    
    # Prepare open questions
    test.open_questions_left = role_data.open_questions.copy()
    
    await update.message.reply_text(Settings.get_locale("start_test_explanation"),
                                  reply_markup=ReplyKeyboardRemove())
    
    # Ask first question
    return await ask_next_question(update, context)

async def ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask the next question in the queue"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        return await finish_test(update, context)
    
    # First handle all regular questions
    if test.questions_left:
        # Get next question
        cat_id, question = test.questions_left.pop(0)
        context.user_data["last_question"] = question
        test.current_category = cat_id
        
        # Ask question with 1-10 keyboard
        await update.message.reply_text(
            question,
            reply_markup=ReplyKeyboardMarkup(Settings.get_score_keyboard(), resize_keyboard=True)
        )
        return QUESTION
    
    # Then handle open questions
    if test.open_questions_left:
        question = test.open_questions_left.pop(0)
        context.user_data["last_question"] = question
        await update.message.reply_text(
            f"{question}\n\n{Settings.get_locale('open_question_hint')}",
            reply_markup=ReplyKeyboardMarkup([["/skip"]], resize_keyboard=True))
        return OPEN_QUESTION
    
    # No more questions
    return await finish_test(update, context)

async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process user's answer and ask next question"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await update.message.reply_text(Settings.get_locale("error_noactivetest"))
        return ConversationHandler.END
    
    try:
        rating = int(update.message.text)
        if rating < 1 or rating > 10:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text(
            Settings.get_locale("error_outofrange"),
            reply_markup=ReplyKeyboardMarkup(Settings.get_score_keyboard(), resize_keyboard=True))
        return QUESTION
    
    test.answers[context.user_data["last_question"]] = rating
    
    # Add rating to current category
    test.score[test.current_category] += rating
    
    # Ask next question
    return await ask_next_question(update, context)

async def receive_open_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process user's open answer and ask next question"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await update.message.reply_text(Settings.get_locale("error_noactivetest"))
        return ConversationHandler.END
    
    if update.message.text != "/skip":
        # Store the answer with the question text as key
        question = test.open_questions_left[0] if test.open_questions_left else "Unknown question"
        test.open_answers[question] = update.message.text
    
    # Ask next question
    return await ask_next_question(update, context)

async def send_results_by_email(test: Test):
    """Send the collected answers via email"""
    if not 'email' in Settings.config:
        print("Email configuration not found")
        return
    
    email_config = Settings.config['email']
    
    # Format answers as string
    answers_str = "{\n"
    answers_str += f'    "industry": {test.industry},\n'
    answers_str += f'    "role": {test.role},\n'
    answers_str += f'    "team_size": {test.team_size},\n'
    answers_str += f'    "person_cost": "{test.person_cost if test.person_cost else "not specified"}",\n'
    
    for question, answer in test.answers.items():
        answers_str += f'    "{question}": {answer},\n'
    for question, answer in test.open_answers.items():
        answers_str += f'    "{question}": "{answer}",\n'
    answers_str = answers_str.rstrip(",\n") + "\n}"
    
    # Create email message
    msg = MIMEText(answers_str)
    msg['Subject'] = f"Test results from user {test.userid}"
    msg['From'] = email_config['sender_email']
    msg['To'] = email_config['receiver_email']
    
    # Send email
    try:
        with smtplib.SMTP_SSL(email_config['smtp_server'], email_config['smtp_port']) as server:
            # server.starttls()
            server.login(email_config['sender_email'], getenv("EMAIL_PASSWORD"))
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

async def finish_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Calculate and display results"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.pop(user_id, None)
    
    if not test:
        await update.message.reply_text(Settings.get_locale("error"))
        return ConversationHandler.END
    
    # Prepare results
    role_data = Settings.roles[test.role]
    results = {}
    average = 0
    total_qs = 0
    
    for cat_id, score in test.score.items():
        average += score
        category = role_data.questions[cat_id]
        results[category.display_name] = score / len(category.questions) if len(category.questions) > 0 else 0
        total_qs += len(category.questions)
    
    if total_qs > 0:
        average /= total_qs
    
    img_buffer = generate_spidergram(list(results.keys()), list(results.values()),
                               f"{test.industry}: {Settings.roles[test.role].display_name}")
    
    # Send the image
    await update.message.reply_photo(photo=img_buffer, 
                                   caption=Settings.get_locale("results").format(average),
                                   show_caption_above_media=True)
    
    loss = (100-average*10)*float(test.person_cost)
    await update.message.reply_text(Settings.get_locale("results_losscalc").format(test.person_cost,test.team_size,loss,loss*test.team_size))
    
    # Send results by email
    await send_results_by_email(test)
    
    return ConversationHandler.END

async def cancel_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the ongoing test"""
    user_id = update.effective_user.id
    if user_id in Settings.ongoing_tests:
        del Settings.ongoing_tests[user_id]
    
    await update.message.reply_text(
        Settings.get_locale("cancel"),
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def main() -> None:
    """Start the bot."""
    # Load environment and configuration
    if not load_dotenv():
        raise FileNotFoundError("В этой папке нет файла \".env\". Создайте файл .env с полем TOKEN=<токен бота> и EMAIL_PASSWORD=<пароль от почты в конфиге>.")
    
    try:
        Settings.get_config()
        Settings.load_locales(Settings.config.get("locale_folder", "locales"))
        Settings.get_questions(Settings.config.get("question_file", "questions.json"))
        Settings.load_industries(Settings.config.get("industry_file", "industries.txt"))
    except Exception as e:
        raise Exception(f"Ошибка инициализации: {e}")
    
    # Create application
    application = Application.builder().token(getenv("TOKEN")).build()
    
    # Add conversation handler for the test
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("starttest", start_test)],
        states={
            INDUSTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_industry)],
            ROLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_role)],
            TEAM_SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_team_size)],
            PERSON_COST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_person_cost),
                CommandHandler("skip", receive_person_cost)
            ],
            QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_answer)],
            OPEN_QUESTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_open_answer),
                CommandHandler("skip", receive_open_answer)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_test)],
    )
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(conv_handler)
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()