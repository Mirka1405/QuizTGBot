#!/usr/bin/env python
# pylint: disable=unused-argument
"""
Skill Assessment Bot with industry selection, role selection, and questionnaire
"""

import io
import random
import re

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler
)

from dotenv import load_dotenv
from os import getenv

from spidergram import generate_spidergram

import smtplib
from email.mime.text import MIMEText

from engine import *

INDUSTRY, ROLE, TEAM_SIZE, PERSON_COST, QUESTION, OPEN_QUESTION, GETTING_EMAIL = range(7)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start with company ID parameter"""
    company_id = None
    kb = ["/starttest"]
    
    if context.args:
        if not context.args[0].isdigit():
            await update.message.reply_text(
                Settings.get_locale("error_companylinkstopped"),
                reply_markup=ReplyKeyboardMarkup([kb], resize_keyboard=True)
            )
            return
        
        company_id = int(context.args[0])
        
        cursor = Settings.db.conn.cursor()
        cursor.execute("SELECT is_active FROM companies WHERE id = ?", (company_id,))
        result = cursor.fetchone()
        if not result or not result[0]:
            await update.message.reply_text(
                Settings.get_locale("error_companylinkstopped"),
                reply_markup=ReplyKeyboardMarkup([kb], resize_keyboard=True)
            )
            return
    
    welcome_msg = Settings.get_locale("start_reply").format(
        Settings.get_locale("start_company_detected") if company_id else Settings.get_locale("start_recommendations_nocompany")
    )
    
    if company_id:
        context.user_data['company_id'] = company_id
    
    await update.message.reply_text(
        welcome_msg,
        reply_markup=ReplyKeyboardMarkup([kb], resize_keyboard=True)
    )

async def group_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a new company"""
    user_id = update.effective_user.id
    company_id = Settings.db.create_company(user_id)
    
    invite_link = f"https://t.me/{context.bot.username}?start={company_id}"
    await update.message.reply_text(Settings.get_locale("company_created").format(invite_link,company_id),reply_markup=ReplyKeyboardMarkup([["/starttest"]], resize_keyboard=True))
    context.user_data['company_id'] = company_id

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
        test.score[cat_id] = 0
        for question in category.questions:
            all_questions.append((cat_id, question))
    
    test.questions_left = all_questions
    
    # Prepare open questions
    test.open_questions_left = role_data.open_questions.copy()
    
    await update.message.reply_text(Settings.get_locale("start_test_explanation"),
                                  reply_markup=ReplyKeyboardRemove())
    
    # Ask first question
    return await ask_next_question(update, context)

async def ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask the next question in the queue"""
    last_category = context.user_data.get("last_cat_id")
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        return await finish_test(update, context)
    
    if test.questions_left:
        cat_id, question = test.questions_left.pop(0)
        context.user_data["last_question"] = question
        test.current_category = cat_id
        if cat_id!=last_category:
            question=Settings.get_locale("new_category").format(cat_id,Settings.categories_locales[cat_id])+question
            context.user_data["last_cat_id"] = cat_id
        
        await update.message.reply_markdown(
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
    
    question = context.user_data["last_question"]
    test.answers[question] = (rating, test.current_category)
    
    # Add rating to current category
    test.score[test.current_category] += rating
    
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
        question = context.user_data["last_question"]
        test.open_answers[question] = update.message.text
    
    # Ask next question
    return await ask_next_question(update, context)

def wrap_email_html(content):
    return Settings.html.replace("CONTENT",content).replace("NUMBER",Settings.config["consultation_number"])
async def send_results_by_email(text: str,toemail:str):
    """Send the collected answers via email"""
    if not 'email' in Settings.config:
        print("Email configuration not found")
        return
    
    email_config = Settings.config['email']
    
    
    # Create email message
    msg = MIMEText(wrap_email_html(text),'html')
    msg['Subject'] = f"Результаты анализа командной работы"
    msg['From'] = email_config['sender_email']
    msg['To'] = toemail
    
    # Send email
    try:
        with smtplib.SMTP_SSL(email_config['smtp_server'], email_config['smtp_port']) as server:
            server.login(email_config['sender_email'], getenv("EMAIL_PASSWORD"))
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

async def my_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send company results as CSV file"""
    user_id = update.effective_user.id
    
    # Find companies created by this user
    cursor = Settings.db.conn.cursor()
    cursor.execute("SELECT id FROM companies WHERE created_by = ?", (user_id,))
    companies = cursor.fetchall()
    
    if not companies:
        await update.message.reply_text(Settings.get_locale("company_results_nocompany"))
        return
    
    for company in companies:
        company_id = company[0]
        
        try:
            # Generate CSV content
            csv_content = Settings.db.get_company_results_csv(company_id)
            
            # Send as file
            await update.message.reply_document(
                document=io.BytesIO(csv_content.encode('utf-8')),
                filename=f"company_{company_id}_results.csv",
                caption=Settings.get_locale("company_results")
            )
            
            # Send summary
            cursor.execute("""
            SELECT COUNT(*), AVG(average_ti) 
            FROM results 
            WHERE company_id = ?
            """, (company_id,))
            summary = cursor.fetchone()
            
            if summary and summary[0] > 0:
                await update.message.reply_text(Settings.get_locale("company_results_full").format(
                    company_id, summary[0], round(summary[1], 1)
                ))
            else:
                await update.message.reply_text(Settings.get_locale("company_results_none"))
                
        except Exception as e:
            await update.message.reply_text(Settings.get_locale("error_generating_report"))
            raise e

async def stop_group_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark all of this user's tests as finished"""
    user_id = update.effective_user.id
    cursor = Settings.db.conn.cursor()

    cursor.execute("UPDATE companies SET is_active = 0 WHERE created_by = ?", (user_id,))
    cursor = Settings.db.conn.commit()
    await update.message.reply_text(
        Settings.get_locale("company_deleted"),
        reply_markup=ReplyKeyboardRemove()
    )
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

    company_id = context.user_data.get('company_id')
    username = update.effective_user.username or update.effective_user.full_name
    Settings.db.save_results(test, username, company_id)

    average = round(test.average,2)
    
    recs = Settings.get_locale("email_score").format(average,100-average*10)
    free_emoji = Settings.config["free_rec_emoji"]
    paid_emoji = Settings.config["paid_rec_emoji"]
    for cat_id, score in test.score.items():
        category = role_data.questions[cat_id]
        results[category.display_name] = score / len(category.questions) if len(category.questions) > 0 else 0
        if results[category.display_name] > 7.5:
            recs += Settings.get_locale("aspect_strong").format(cat_id,results[category.display_name]) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["paid"]) + "<br><br>"
        elif results[category.display_name] > 5:
            recs += Settings.get_locale("aspect_medium").format(cat_id,results[category.display_name]) + \
                    f"{free_emoji}{random.choice(Settings.recommendations["weak"][cat_id]["free"])}<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in random.sample(Settings.recommendations["weak"][cat_id]["paid"], 2)) + "<br><br>"
        else:
            recs += Settings.get_locale("aspect_weak").format(cat_id,results[category.display_name]) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["paid"]) + "<br><br>"
    
    img_buffer = generate_spidergram(list(results.keys()), list(results.values()),
                               f"{test.industry}: {Settings.roles[test.role].display_name}")
    
    # Send the image
    await update.message.reply_photo(photo=img_buffer, 
                                   caption=Settings.get_locale("results").format(average),
                                   show_caption_above_media=True)
    await update.message.reply_text(Settings.get_locale("results_score_sum_up").format(Settings.config["consultation_number"]))
    # await update.message.reply_markdown(recs)
    context.user_data["recs"] = recs
    try:
        if test.person_cost and test.person_cost.isdigit():
            person_cost = float(test.person_cost)
            loss = (1 - average/10) * person_cost
            total_loss = loss * test.team_size
            await update.message.reply_text(Settings.get_locale("results_losscalc").format(
                round(100-average*10), round(loss,2), round(total_loss,2)
                ))
    except (ValueError, TypeError):
        pass
    
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

async def get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if "recs" not in context.user_data.keys():
        await update.message.reply_text("Вы еще не прошли тест.")
        return ConversationHandler.END
    await update.message.reply_text("Пожалуйста, отправьте ваш почтовый адрес.")
    return GETTING_EMAIL
def is_valid_email(email):
    if not email or len(email) > 320: return False
    pattern = r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$"
    return bool(re.fullmatch(pattern, email, re.VERBOSE))
async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_valid_email(update.message.text):
        await update.message.reply_text("Пожалуйста, пришлите рабочий адрес почты.")
        return GETTING_EMAIL
    await send_results_by_email(context.user_data["recs"],update.message.text)
    await update.message.reply_text("Отправлено.")
    return ConversationHandler.END
def main() -> None:
    """Start the bot."""
    # Load environment and configuration
    if not load_dotenv():
        raise FileNotFoundError("В этой папке нет файла \".env\". Создайте файл .env с полем TOKEN=<токен бота> и EMAIL_PASSWORD=<пароль от почты в конфиге>.")
    

    Settings.get_config()
    Settings.init_db(Settings.config.get("database", "database.db"))
    Settings.load_locales(Settings.config.get("locale_folder", "locales"))
    Settings.get_questions(Settings.config.get("question_file", "questions.json"))
    Settings.load_industries(Settings.config.get("industry_file", "industries.txt"))
    Settings.load_recommendations(Settings.config.get("recommendations_file","recommendation.json"))
    Settings.load_html_template(Settings.config.get("email_template", "email_template.html"))

    # Create application
    application = Application.builder().token(getenv("TOKEN")).build()
    
    # Add conversation handler for the test
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("starttest", start_test),CommandHandler("getrecommendations", get_recommendations)],
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
            GETTING_EMAIL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_test)],
    )
    
    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("grouptest", group_test))
    # application.add_handler(CommandHandler("myresults", my_results)) # TODO: paywall this
    application.add_handler(CommandHandler("stopgrouptest", stop_group_test))
    application.add_handler(conv_handler)
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()