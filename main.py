#!/usr/bin/env python
# pylint: disable=unused-argument
"""
Skill Assessment Bot with industry selection, role selection, and questionnaire
"""

from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
import io
import os
import random
import re
from subprocess import DEVNULL, PIPE, Popen
import asyncio
import subprocess
from telegram.error import NetworkError

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
    """Start the test by asking for role first"""
    keyboard = [[role.display_name] for role in Settings.roles.values()]
    await update.message.reply_text(
        Settings.get_locale("role_select"),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return ROLE

async def receive_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store role and ask for industry (if first in group) or skip (if in group)"""
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
    
    # Store role in context
    context.user_data['role_id'] = role_id
    
    # Check if this is a group test and industry already exists
    company_id = context.user_data.get('company_id')
    if company_id:
        cursor = Settings.db.conn.cursor()
        cursor.execute("SELECT industry FROM results WHERE company_id = ? LIMIT 1", (company_id,))
        existing_industry = cursor.fetchone()
        
        if existing_industry:
            # Skip industry selection, use existing one
            return await receive_industry(update, context, existing_industry[0])
    
    # Create keyboard with industries
    keyboard = [[industry] for industry in Settings.industries]
    await update.message.reply_text(
        Settings.get_locale("industry_select"),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return INDUSTRY

async def receive_industry(update: Update, context: ContextTypes.DEFAULT_TYPE, predefined_industry: str = None) -> int:
    """Store industry (either from user input or predefined) and create test instance"""
    user_industry = predefined_industry or update.message.text
    role_id = context.user_data['role_id']
    
    # Create test instance
    user_id = update.effective_user.id
    test = Test(user_id)
    test.industry = user_industry
    test.role = role_id
    
    # Store test
    Settings.ongoing_tests[user_id] = test
    
    if not context.user_data.get("company_id"):
        await update.message.reply_text(Settings.get_locale("team_size_question"))
        return TEAM_SIZE
    else:
        all_questions = []
        role_data = Settings.roles[test.role]
        for cat_id, category in role_data.questions.items():
            test.score[cat_id] = 0
            for question in category.questions:
                all_questions.append((cat_id, question))
        test.questions_left = all_questions
        test.open_questions_left = role_data.open_questions.copy()
        
        await update.message.reply_text(Settings.get_locale("start_test_explanation"),
                                      reply_markup=ReplyKeyboardRemove())
        return await ask_next_question(update, context)
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
    
    random.shuffle(all_questions)
    test.questions_left = all_questions
    
    test.open_questions_left = role_data.open_questions.copy()
    
    await update.message.reply_text(Settings.get_locale("start_test_explanation"),
                                  reply_markup=ReplyKeyboardRemove())
    
    return await ask_next_question(update, context)

async def ask_next_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask the next question in the queue"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        return await finish_test(update, context)
    
    if test.questions_left:
        cat_id, question = test.questions_left.pop(0)
        context.user_data["last_question"] = question
        test.current_category = cat_id
        
        await update.message.reply_markdown(
            question,
            reply_markup=ReplyKeyboardMarkup(Settings.get_score_keyboard(), resize_keyboard=True)
        )
        return QUESTION
    
    if test.open_questions_left:
        question = test.open_questions_left.pop(0)
        context.user_data["last_question"] = question
        await update.message.reply_text(
            f"{question}\n\n{Settings.get_locale('open_question_hint')}",
            reply_markup=ReplyKeyboardMarkup([["/skip"]], resize_keyboard=True))
        return OPEN_QUESTION
    
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
async def send_results_by_email(text: str,toemail:str,image:io.BytesIO|None):
    """Send the collected answers via email"""
    if not 'email' in Settings.config:
        print("Email configuration not found")
        return
    
    email_config = Settings.config['email']
    
    msg = MIMEMultipart()
    msg['Subject'] = Settings.get_locale("email_title")
    msg['From'] = email_config['sender_email']
    msg['To'] = toemail
    msg.attach(MIMEText(wrap_email_html(text),'html'))
    if image:
        image.seek(0)
        img_data = image.read()
        img = MIMEImage(img_data)
        # img.add_header('Content-Disposition', 'attachment', filename="team_assessment.png")
        img.add_header("Content-ID", "<image1>")
        msg.attach(img)

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
    
    role_data = Settings.roles[test.role]
    results = {}

    company_id = context.user_data.get('company_id')
    username = update.effective_user.username or update.effective_user.full_name
    Settings.db.save_results(test, username, company_id)

    average = round(test.average,2)
    
    for cat_id, score in test.score.items():
        category = role_data.questions[cat_id]
        results[category.display_name] = score / len(category.questions) if len(category.questions) > 0 else 0
    
    img_buffer = generate_spidergram(list(results.keys()), list(results.values()),
                               f"Индекс максимума команды. Роль: {Settings.roles[test.role].display_name}")
    
    sum_up_text = "\n"+Settings.get_locale("results_score_sum_up") if company_id is None or average<10 else "\n"
    loss_text = ""
    if test.person_cost and test.person_cost.isdigit():
        person_cost = float(test.person_cost)
        loss = (1 - average/10) * person_cost
        total_loss = loss * test.team_size
        loss_text=Settings.get_locale("results_losscalc").format(
                round(100-average*10), round(total_loss,2)
            )
        
    sum_up_text+="\n"+Settings.get_locale("results_score_sum_up_group" if context.user_data.get("company_id") else "results_score_sum_up_sole")\
            .format(Settings.config["consultation_number"])
    
    recomms_text = ""
    result_text = None
    if min(results.values())<10:
        additions = [k for k,v in results.items() if v<4]
        if not additions: additions = [k for k,v in results.items() if v<=min(results.values())]
        if len(additions)==1:
            cat = list(Settings.categories_locales.keys())[list(Settings.categories_locales.values()).index(additions[0])]
            recomms_text = Settings.get_locale(f"results_weak_{cat}")
        else:
            recomms_text+="\n"+";\n".join(Settings.get_locale(f"results_weak_{list(Settings.categories_locales.keys())[list(Settings.categories_locales.values()).index(i)]}") for i in additions)
        recomms_text+="."
        result_text = Settings.get_locale("results").format(average,round(100-average*10),loss_text)
    else:
        result_text = Settings.get_locale("results_perfect")
    await update.message.reply_photo(photo=img_buffer, 
                                   caption=result_text+recomms_text+sum_up_text,
                                   show_caption_above_media=True,
                                   reply_markup=ReplyKeyboardRemove(),
                                   parse_mode='HTML')
    
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
async def generate_recommendations(test: Test) -> str:
    """Generate recommendation text based on test results"""
    role_data = Settings.roles[test.role]
    average = round(test.average, 2)
    if average==10:
        recs = Settings.get_locale("email_perfect")
    else:
        recs = Settings.get_locale("email_score").format(average, round(100-average*10,2))
    free_emoji = Settings.config["free_rec_emoji"]
    paid_emoji = Settings.config["paid_rec_emoji"]
    
    results = {}
    for cat_id, score in test.score.items():
        category = role_data.questions[cat_id]
        category_score = score
        if category_score==10: continue
        results[category.display_name] = category_score
        if category_score > 7.5:
            recs += Settings.get_locale("aspect_percentage").format(cat_id, category_score) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["paid"]) + "<br><br>"
        elif category_score > 5:
            recs += Settings.get_locale("aspect_percentage").format(cat_id, category_score) + \
                    f"{free_emoji}{random.choice(Settings.recommendations['weak'][cat_id]['free'])}<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in random.sample(Settings.recommendations["weak"][cat_id]["paid"], 2)) + "<br><br>"
        else:
            recs += Settings.get_locale("aspect_percentage").format(cat_id, category_score) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["paid"]) + "<br><br>"
    
    image = generate_spidergram(list(results.keys()), list(results.values()),
                               f"Индекс максимума команды. Роль: {Settings.roles[test.role].display_name}")
    return recs,image

async def get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the email conversation - just verify test exists"""
    user_id = update.effective_user.username or update.effective_user.full_name
    
    cursor = Settings.db.conn.cursor()
    cursor.execute("""
        SELECT average_ti FROM results 
        WHERE telegram_username = ?
    """, (user_id,))
    
    res = cursor.fetchone()
    if not res:
        await update.message.reply_text(Settings.get_locale("error_notest"))
        return ConversationHandler.END
    if res==10:
        await update.message.reply_text(Settings.get_locale("error_perfect").format(Settings.config["consultation_number"]))
        return ConversationHandler.END
    
    await update.message.reply_text(Settings.get_locale("request_email"))
    return GETTING_EMAIL
def is_valid_email(email):
    if not email or len(email) > 320: return False
    pattern = r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$"
    return bool(re.fullmatch(pattern, email, re.VERBOSE))
async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Now that we have email, generate and send recommendations"""
    if not is_valid_email(update.message.text):
        await update.message.reply_text(Settings.get_locale("bad_email_address"))
        return GETTING_EMAIL

    # Fetch data and generate recommendations only now
    cursor = Settings.db.conn.cursor()
    
    # Get basic result info
    cursor.execute("""
        SELECT id, role, industry, team_size, person_cost, average_ti 
        FROM results 
        WHERE telegram_username = ?
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (update.effective_user.username,))
    result = cursor.fetchone()
    
    if not result:
        await update.message.reply_text(Settings.get_locale("error_notest"))
        return ConversationHandler.END
    await update.message.reply_text(Settings.get_locale("email_generating"))
    
    result_id, role, industry, team_size, person_cost, average_ti = result
    
    test = Test(update.effective_user.id)
    test.role = list(Settings.role_locales.keys())[list(Settings.role_locales.values()).index(role)]
    test.industry = industry
    test.team_size = team_size
    test.person_cost = person_cost
    test.score = {}
    test.force_average_by_score = True

    cursor.execute("""
        SELECT nq.category_id, nq.text, na.answer
        FROM num_answers na
        JOIN num_questions nq ON na.question_id = nq.id
        WHERE na.id = ?
    """, (result_id,))
    
    # Calculate category averages
    category_scores = {}
    cat_names = list(Settings.categories_locales.keys())
    for cat_id, question_text, answer in cursor.fetchall():
        cat_name = cat_names[cat_id-1]
        if cat_name not in category_scores:
            category_scores[cat_name] = {'sum': 0, 'count': 0}
        category_scores[cat_name]['sum'] += answer
        category_scores[cat_name]['count'] += 1
    
    for cat_id, scores in category_scores.items():
        test.score[cat_id] = scores['sum'] / scores['count'] if scores['count'] > 0 else 0
    

    # Generate and send recommendations
    recs, image = await generate_recommendations(test)
    await send_results_by_email(recs, update.message.text, image)
    await update.message.reply_text(Settings.get_locale("email_sent"))

    return ConversationHandler.END
async def check_admin(update: Update) -> bool:
    """Check if user is admin"""
    username = update.effective_user.username
    return username in Settings.admins if username else False

async def get_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send journalctl logs (admin only)"""
    if not await check_admin(update):
        return
    
    try:
        lines = int(context.args[0]) if context.args else 50
        lines = min(lines, 1000)  # Limit for safety
    except (ValueError, IndexError):
        lines = 50
    
    proc = await asyncio.create_subprocess_exec(
        'journalctl', '-u', 'tgbot', '-n', str(lines),
        stdout=PIPE, stderr=PIPE
    )
    stdout, stderr = await proc.communicate()
    
    if proc.returncode != 0:
        await update.message.reply_text(f"Error getting logs:\n{stderr.decode()}")
        return
    
    logs = stdout.decode()
    for i in range(0, len(logs), 4096):
        await update.message.reply_text(logs[i:i+4096])

async def exec_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute remote command (admin only)"""
    if not await check_admin(update):
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /exec <command>")
        return
    
    try:
        command = ' '.join(context.args)
            
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=PIPE, stderr=PIPE, shell=True
        )
        stdout, stderr = await proc.communicate()
        
        output = f"Return code: {proc.returncode}\n"
        if stdout:
            output += f"STDOUT:\n{stdout.decode()}\n"
        if stderr:
            output += f"STDERR:\n{stderr.decode()}\n"
            
        await update.message.reply_text(output[:4000])  # Truncate if too long
        
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send requested file (admin only)"""
    if not await check_admin(update):
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /getfile <path>")
        return
    
    path = ' '.join(context.args)
    
    try:
        with open(path, 'rb') as f:
            await update.message.reply_document(f)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

async def put_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive and save file (admin only)"""
    if not await check_admin(update):
        return
    
    if not update.message.document and not update.message.reply_to_message:
        await update.message.reply_text("Reply to a file message with /putfile <destination_path>")
        return
    
    try:
        # Get destination path from command args
        if not context.args:
            await update.message.reply_text("Usage: /putfile <destination_path>")
            return
        
        dest_path = ' '.join(context.args)
        
        message = update.message.reply_to_message or update.message
        file = await message.document.get_file()
        
        await file.download_to_drive(dest_path)
        await update.message.reply_text(f"File saved to {dest_path}")
        
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Pong!")

async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Safe update command that avoids multiple instances"""
    if not await check_admin(update):
        return

    await update.message.reply_text("🔄 Starting update...")
    
    subprocess.run(["git", "pull"])
    exit(0)


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
    Settings.load_admins(Settings.config.get("admins_file","admins.txt"))

    # Create application
    application = Application.builder().token(getenv("TOKEN")).concurrent_updates(False)\
                                                            .read_timeout(30)\
                                                            .write_timeout(30)\
                                                            .connect_timeout(30)\
                                                            .pool_timeout(30)\
                                                            .build()

    application.add_handler(CommandHandler("logs", get_logs))
    application.add_handler(CommandHandler("exec", exec_command))
    application.add_handler(CommandHandler("getfile", get_file))
    application.add_handler(CommandHandler("putfile", put_file))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("pong", ping))
    application.add_handler(CommandHandler("update", update_command))
    
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