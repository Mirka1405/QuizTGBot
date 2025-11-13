#!/usr/bin/env python
# pylint: disable=unused-argument
"""
Skill Assessment Bot with industry selection, role selection, and questionnaire
"""
import datetime
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
import io
import random
import re
from subprocess import PIPE
import asyncio
import sys

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler
)

from dotenv import load_dotenv
from os import getenv

import datascience
import pdf_generator
from spidergram import *

import smtplib
from email.mime.text import MIMEText

from engine import *
STATEAMOUNT = 9
NO, INDUSTRY, ROLE, TEAM_SIZE, PERSON_COST, QUESTION, OPEN_QUESTION, GETTING_EMAIL, GETTING_GROUP_EMAIL = range(STATEAMOUNT)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, response_code: str | None = None):
    if not (update.effective_message or response_code): return
    val = Settings.button_callbacks.get(response_code if response_code else update.effective_message.text)
    if val: return await val(update,context)
    states = {INDUSTRY:receive_industry,ROLE:receive_role,TEAM_SIZE:receive_team_size,
              PERSON_COST:receive_person_cost,QUESTION:receive_answer,
              OPEN_QUESTION:receive_open_answer,GETTING_EMAIL:receive_email,
              GETTING_GROUP_EMAIL:receive_group_email}
    state = context.user_data.get("state")
    if not state or state>=STATEAMOUNT or state==NO: return
    value = await states[state](update,context)
    if value: context.user_data["state"] = value
async def handle_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query: return
    # fake = Update(update.update_id,update.callback_query.message)
    await handle_message(update,context,query.data)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start with company ID parameter"""
    company_id = None
    starttesttext = Settings.get_locale("button_starttest" if context.args else "button_starttest_alt")
    grouptesttext = Settings.get_locale("button_grouptest")
    Settings.skip_locales.add(Settings.get_locale("button_skip"))

    Settings.add_button_locales({"start":start,"starttest":start_test,"grouptest":group_test,"getrecommendations":get_recommendations,
                                 "getgrouprecommendations":get_group_recommendations,"grouptestresults":group_test_results})

    kb = [[InlineKeyboardButton(starttesttext,callback_data="starttest")]]
    
    if context.args:
        if not context.args[0].isdigit():
            await context.bot.send_message(update.effective_message.chat_id,
                Settings.get_locale("error_companylinkstopped"),
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return
        
        company_id = int(context.args[0])
        
        cursor = Settings.db.conn.cursor()
        cursor.execute("SELECT is_active FROM companies WHERE id = ?", (company_id,))
        result = cursor.fetchone()
        if not result or not result[0]:
            await context.bot.send_message(update.effective_message.chat_id,
                Settings.get_locale("error_companylinkstopped"),
                reply_markup=InlineKeyboardMarkup(kb)
            )
            return
    
    welcome_msg = Settings.get_locale("start_reply").format(Settings.config["link"])
    context.user_data.clear()
    if company_id:
        context.user_data['company_id'] = company_id
    else:
        kb.append([InlineKeyboardButton(grouptesttext,callback_data="grouptest")])
    
    await context.bot.send_message(update.effective_message.chat_id,
        welcome_msg,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

async def group_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a new company"""
    user_id = update.effective_user.id
    company_id = Settings.db.create_company(user_id)
    
    invite_link = f"https://t.me/{context.bot.username}?start={company_id}"
    await context.bot.send_message(update.effective_message.chat_id,
        Settings.get_locale("company_created").format(invite_link,company_id),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(Settings.get_locale("button_starttest"),callback_data="starttest")]])
            )
    context.user_data.clear()
    context.user_data['company_id'] = company_id

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("about"))

async def start_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the test by asking for role first"""
    #attempt again just in case
    Settings.skip_locales.add(Settings.get_locale("button_skip"))

    Settings.add_button_locales({"starttest":start_test,"grouptest":group_test,"getrecommendations":get_recommendations,"getgrouprecommendations":get_group_recommendations,"grouptestresults":group_test_results})

    keyboard = [[role.display_name] for role in Settings.roles.values()]
    await context.bot.send_message(update.effective_message.chat_id,
        Settings.get_locale("role_select"),
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    context.user_data["state"] = ROLE

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
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_wrongrole"))
        return ROLE
    
    grouptest = context.user_data.get("company_id")
    if grouptest:
        cursor = Settings.db.conn.cursor()
        cursor.execute("SELECT telegram_username FROM results WHERE company_id = ? AND role = ?", (grouptest,role_id))
        res = cursor.fetchall()
        if res:
            if res[0][0]==update.effective_user.username:
                await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_group_test_completed"))
                return ROLE
            if role_id == "Manager":
                await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_group_test_completed_by_manager").format(res[0][0]))
                return ROLE
    
    # Store role in context
    context.user_data['role_id'] = role_id
    
    # Check if this is a group test and industry already exists
    company_id = context.user_data.get('company_id')
    if company_id and role_id!="Manager":
        user_id = update.effective_user.id
        test = Test(user_id)
        test.role = role_id
        
        # Store test
        Settings.ongoing_tests[user_id] = test
        all_questions = []
        role_data = Settings.roles[test.role]
        for cat_id, category in role_data.questions.items():
            test.score[cat_id] = 0
            for question in category.questions:
                all_questions.append((cat_id, question))
        test.questions_left = all_questions
        test.open_questions_left = role_data.open_questions.copy()
        
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("start_test_explanation"),
                                        reply_markup=ReplyKeyboardRemove())
        return await ask_next_question(update, context)
    
    # Create keyboard with industries
    keyboard = [[industry] for industry in Settings.industries]
    await context.bot.send_message(update.effective_message.chat_id,
        Settings.get_locale("industry_select"),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    )
    return INDUSTRY

async def receive_industry(update: Update, context: ContextTypes.DEFAULT_TYPE, predefined_industry: str|bool|None = None) -> int:
    """Store industry (either from user input or predefined) and create test instance"""
    if predefined_industry:
        if predefined_industry==True: user_industry = None
        else: user_industry = predefined_industry
    else: user_industry = update.message.text
    role_id = context.user_data['role_id']
    
    # Create test instance
    user_id = update.effective_user.id
    test = Test(user_id)
    test.industry = user_industry
    test.role = role_id
    
    # Store test
    Settings.ongoing_tests[user_id] = test
    
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("team_size_question"))
    return TEAM_SIZE
    
async def receive_team_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store team size and ask for person cost (optional)"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_noactivetest"),reply_markup=[[Settings.get_locale("button_starttest")]])
        return NO
    
    try:
        team_size = int(update.message.text)
        if team_size <= 0:
            raise ValueError
        if team_size < 2:
            await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_need_more_than_one"))
            return TEAM_SIZE
        test.team_size = team_size
    except (ValueError, TypeError):
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_positive_number"))
        return TEAM_SIZE
    
    # Ask for average person cost (optional)
    await context.bot.send_message(update.effective_message.chat_id,
        Settings.get_locale("person_cost_question"),
        reply_markup=ReplyKeyboardMarkup([[Settings.get_locale("button_skip")]], resize_keyboard=True))
    return PERSON_COST

async def receive_person_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store person cost and start the questionnaire"""
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_noactivetest"),reply_markup=[[Settings.get_locale("button_starttest")]])
        return NO
    if update.message.text not in Settings.skip_locales:
        try:
            float(update.message.text)
        except ValueError:
            await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_positive_number"))
            return PERSON_COST
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
    
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("start_test_explanation"),
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
        await context.bot.send_message(update.effective_message.chat_id,
            f"{question}\n\n{Settings.get_locale('open_question_hint')}",
            reply_markup=ReplyKeyboardMarkup([[Settings.get_locale("button_skip")]], resize_keyboard=True, one_time_keyboard=True))
        return OPEN_QUESTION
    
    return await finish_test(update, context)

async def receive_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    test = Settings.ongoing_tests.get(user_id)
    
    if not test:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_noactivetest"),reply_markup=[[Settings.get_locale("button_starttest")]])
        return NO
    
    try:
        rating = int(update.message.text)
        if rating < 1 or rating > 10:
            raise ValueError
    except (ValueError, TypeError):
        await context.bot.send_message(update.effective_message.chat_id,
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
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_noactivetest"),reply_markup=[[Settings.get_locale("button_starttest")]])
        return NO
    
    if update.message.text not in Settings.skip_locales:
        # Store the answer with the question text as key
        question = context.user_data["last_question"]
        test.open_answers[question] = update.message.text
    
    # Ask next question
    return await ask_next_question(update, context)

def wrap_email_html(content):
    replacements = {
        "CONTENT": content,
        "NUMBER": Settings.config["consultation_tg"],
        "LINK": Settings.config["link"],
        "MAIL": Settings.config["owner_mail"],
        "LEGEND": Settings.get_locale("email_legend"),
        "EMOJIFREE": Settings.config["free_rec_emoji"],
        "EMOJIPAID": Settings.config["paid_rec_emoji"],
    }

    result = Settings.html
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, str(value))
    return result
async def send_results_by_email(text: str,toemail:str,image:io.BytesIO|None,csv:str|None=None,pdf:BytesIO|None=None):
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
        img.add_header("Content-ID", "<image1>")
        img.add_header('Content-Disposition', 'inline')
        msg.attach(img)
    if csv:
        filebase = MIMEApplication(csv.encode('utf-8'))
        filebase['Content-Disposition'] = f'attachment; filename=results.csv'
        msg.attach(filebase)
    if pdf:
        pdf.seek(0)
        filebase = MIMEApplication(pdf.read())
        filebase['Content-Disposition'] = f'attachment; filename=report.pdf'
        msg.attach(filebase)
    try:
        with smtplib.SMTP_SSL(email_config['smtp_server'], email_config['smtp_port']) as server:
            server.login(email_config['sender_email'], getenv("EMAIL_PASSWORD"))
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

async def results_to_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Find companies created by this user
    cursor = Settings.db.conn.cursor()
    cursor.execute("SELECT id FROM companies WHERE created_by = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (user_id,))
    companies = cursor.fetchall()
    
    if not companies:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error"))
        return
    
    company_id = companies[0][0]
    
    try:
        # Generate CSV content
        csv_content = Settings.db.get_company_results_csv(company_id)
        return csv_content
        
        # Send as file
        # await update.message.reply_document(
        #     document=io.BytesIO(csv_content.encode('utf-8')),
        #     filename=f"company_{company_id}_results.csv",
        #     caption=Settings.get_locale("company_results")
        # )
        
        # Send summary
        # cursor.execute("""
        # SELECT COUNT(*), AVG(average_ti) 
        # FROM results 
        # WHERE company_id = ?
        # """, (company_id,))
        # summary = cursor.fetchone()
        
        # if summary and summary[0] > 0:
        #     await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("company_results_full").format(
        #         summary[0], round(summary[1], 1)
        #     ))
        # else:
        #     await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("company_results_none"))
            
    except Exception as e:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_generating_report"))
        raise e

async def group_test_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cursor = Settings.db.conn.cursor()

    # Get all active companies created by this user
    cursor.execute("SELECT id FROM companies WHERE created_by = ? AND is_active = 1 ORDER BY id DESC LIMIT 1", (user_id,))
    companies = cursor.fetchall()
    
    if not companies:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_notest"))
        return
    
    cursor.execute(f"""
        SELECT * FROM results 
        WHERE company_id = ?
    """, (companies[0][0],))
    
    results = cursor.fetchall()
    
    if not results:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_group_notest"))
        return
    
    # Initialize aggregation variables
    all_scores = {}
    industry = None
    team_size = None
    person_cost = None
    
    # Get category names for mapping
    cursor.execute("SELECT id, name FROM categories")
    categories = {row[0]: row[1] for row in cursor.fetchall()}
    
    # Process each result
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("group_test_results_amount").format(len(results)))
    context.user_data["group_amount"] = len(results)
    for result in results:
        result_id = result[0]
        
        # Get numerical answers for this result
        cursor.execute("""
            SELECT nq.category_id, na.answer 
            FROM num_answers na
            JOIN num_questions nq ON na.question_id = nq.id
            WHERE na.id = ?
        """, (result_id,))
        
        num_answers = cursor.fetchall()
        
        # Aggregate scores by category
        for category_id, answer in num_answers:
            category_name = categories.get(category_id, f"Category_{category_id}")
            if category_name not in all_scores:
                all_scores[category_name] = []
            all_scores[category_name].append(answer)
        
        # Get first non-null values for industry, team_size, person_cost
        if industry is None and result[4]:  # industry field
            industry = result[4]
        if team_size is None and result[5]:  # team_size field
            team_size = result[5]
        if person_cost is None and result[6]:  # person_cost field
            person_cost = result[6]
    
    # Calculate average scores per category
    average_scores = {}
    for category, scores in all_scores.items():
        average_scores[category] = sum(scores) / len(scores)
    
    # Create aggregated test result
    aggregated_test = Test(user_id)
    aggregated_test.industry = industry or None
    aggregated_test.team_size = team_size or None
    aggregated_test.person_cost = person_cost or None
    aggregated_test.role = "Manager"
    aggregated_test.score = average_scores
    aggregated_test.force_average_by_score = True
    
    await finish_test(update, context, aggregated_test)

async def finish_test(update: Update, context: ContextTypes.DEFAULT_TYPE, group:Test|None=None) -> int:
    """Calculate and display results"""
    user_id = update.effective_user.id
    if group: test = group
    else: test = Settings.ongoing_tests.pop(user_id, None)
    
    if not test:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error"))
        return NO
    
    role_data = Settings.roles[test.role]
    results = {}

    company_id = context.user_data.get('company_id')
    username = update.effective_user.username or update.effective_user.full_name
    if not group:
        Settings.db.save_results(test, username, company_id)

    average_unrounded = test.average
    average = round(average_unrounded,2)
    
    for cat_id, score in test.score.items():
        category = role_data.questions[cat_id]
        results[category.display_name] = score / len(category.questions) if len(category.questions) > 0 else 0
    
    img_buffer = None
    
    if not group:
        img_buffer = generate_spidergram(list(results.keys()), list(results.values()),
                               f"Индекс максимума команды. Роль: {Settings.roles[test.role].display_name}",
                               "darkred" if test.role=="Manager" else "darkblue")
    else:
        cursor = Settings.db.conn.cursor()
        cursor.execute("SELECT id FROM results WHERE telegram_username = ? AND role = 'Manager' ORDER BY timestamp DESC LIMIT 1",(update.effective_user.username,))
        rid = cursor.fetchall()
        if rid:
            cursor.execute("""
                SELECT c.id, c.name, AVG(na.answer)
                FROM results r
                JOIN num_answers na ON r.id = na.id
                JOIN num_questions nq ON na.question_id = nq.id
                JOIN categories c ON nq.category_id = c.id
                WHERE r.role = 'Manager' AND r.id = ?
                GROUP BY c.id, c.name
                ORDER BY c.id
            """,(rid[0][0],))

        manager_results = {}
        for row in cursor.fetchall():
            category_id, category_name, avg_score = row
            manager_results[category_name] = avg_score
        if len(list(manager_results.keys()))==0:
            img_buffer = generate_spidergram(list(results.keys()), list(group.score.values()),
                               f"Индекс максимума команды", "darkblue")
        else:
            # manager_result * 1 + x * (amount - 1) = group_score * amount
            # x = (group_score * amount - manager_result)/(amount - 1)
            amount = context.user_data["group_amount"]
            if amount <= 1:
                img_buffer = generate_spidergram(list(results.keys()), list(group.score.values()),
                               f"Индекс максимума команды", "darkred")
            else:
                employee_results = {}
                for i in group.score.keys():
                    employee_results[i] = (group.score[i] * amount - manager_results[i])/(amount-1)
                img_buffer = generate_double_spidergram(
                    Settings.categories_locales,
                    employee_results,
                    manager_results,
                    f"Индекс максимума команды"
                )
            del context.user_data["group_amount"]
    sum_up_text=""
    loss_text=""
    recomms_text = ""
    result_text = None
    markup = Settings.get_locale("button_getrecommendations")
    callbackstr = "getrecommendations"
    if test.role=="Manager" or not context.user_data.get("company_id"):
        sum_up_text = "\n"+Settings.get_locale("results_score_sum_up") if company_id is None or average_unrounded<10 else "\n"
        if group:
            markup = Settings.get_locale("button_getgrouprecommendations")
            callbackstr = "getgrouprecommendations"
        elif context.user_data.get("company_id"):
            markup = Settings.get_locale("button_grouptestresults")
            callbackstr = "grouptestresults"
            sum_up_text += "\n"+Settings.get_locale("results_button_explanation")
        if test.person_cost and test.person_cost not in Settings.skip_locales and (isinstance(test.person_cost,(float,int)) or test.person_cost.isdigit()):
            person_cost = float(test.person_cost)
            loss = (1 - average_unrounded/10) * person_cost
            total_loss = loss * float(test.team_size)
            loss_text=Settings.get_locale("results_losscalc").format(round(total_loss))
    
        if min(results.values())<10:
            additions = [k for k,v in results.items() if v<10]
            if not additions: additions = [k for k,v in results.items() if v<=min(results.values())]
            recomms_text+="\n• "+";\n• ".join(additions)
            recomms_text+="."
            result_text = Settings.get_locale("results").format(average,round(100-average_unrounded*10,1),loss_text)
        else:
            result_text = Settings.get_locale("results_perfect")

    if not context.user_data.get("company_id"):
        buttons = [[InlineKeyboardButton(Settings.get_locale("button_start"),callback_data="start")],[InlineKeyboardButton(markup,callback_data=callbackstr)]]
        
        await context.bot.send_photo(
                update.effective_message.chat_id,
                img_buffer,
                result_text+recomms_text+sum_up_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML",
                message_thread_id=update.effective_message.message_thread_id,
             )
        message = await context.bot.send_message(update.effective_message.chat_id,".",reply_markup=ReplyKeyboardRemove())
        await message.delete()
    else:
        markup_buttons = [[InlineKeyboardButton(Settings.get_locale("button_start"),callback_data="start")]]
        if test.role=="Manager": markup_buttons.append([InlineKeyboardButton(markup,callback_data=callbackstr)])
        await context.bot.send_photo(
                update.effective_message.chat_id,
                img_buffer,
                Settings.get_locale("results_manager_group" if test.role == "Manager" else "results_employee").format(average,"@"+Settings.config["consultation_tg"]),
                reply_markup=InlineKeyboardMarkup(markup_buttons),
                parse_mode="HTML",
                message_thread_id=update.effective_message.message_thread_id,
             )
        message = await context.bot.send_message(update.effective_message.chat_id,".",reply_markup=ReplyKeyboardRemove())
        await message.delete()
    context.user_data.clear()
    return NO
async def stop_group_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark all of this user's tests as finished"""
    user_id = update.effective_user.id
    cursor = Settings.db.conn.cursor()

    cursor.execute("UPDATE companies SET is_active = 0 WHERE created_by = ?", (user_id,))
    cursor = Settings.db.conn.commit()
    await context.bot.send_message(update.effective_message.chat_id,
        Settings.get_locale("company_deleted"),
        reply_markup=ReplyKeyboardRemove()
    )
async def cancel_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the ongoing test"""
    user_id = update.effective_user.id
    if user_id in Settings.ongoing_tests:
        del Settings.ongoing_tests[user_id]
    
    await context.bot.send_message(
        update.effective_user.id,
        Settings.get_locale("cancel"),
        reply_markup=ReplyKeyboardRemove()
    )
    context.user_data["state"] = NO
async def generate_recommendations(test: Test) -> str:
    """Generate recommendation text based on test results"""
    role_data = Settings.roles[test.role]
    average = round(test.average, 2)
    loss_text = ""
    if test.person_cost:
        person_cost = float(test.person_cost)
        loss = (1 - test.average/10) * person_cost
        total_loss = loss * float(test.team_size)
        loss_text=Settings.get_locale("results_losscalc").format(round(total_loss))
    if average==10:
        recs = Settings.get_locale("email_perfect")
    else:
        recs = Settings.get_locale("email_score").format(average, round(100-average*10,2),Settings.get_locale("results_losscalc").format(loss_text))
    free_emoji = Settings.config["free_rec_emoji"]
    paid_emoji = Settings.config["paid_rec_emoji"]
    
    results = {}
    for cat_id, score in test.score.items():
        category = role_data.questions[cat_id]
        category_score = score
        results[category.display_name] = category_score
        if category_score==10: continue
        if category_score == 10:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + "<br>" + Settings.get_locale("category_perfect")
        elif category_score > 7.5:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["paid"]) + "<br><br>"
        elif category_score > 5:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + \
                    f"{free_emoji}{random.choice(Settings.recommendations['weak'][cat_id]['free'])}<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in random.sample(Settings.recommendations["weak"][cat_id]["paid"], 2)) + "<br><br>"
        else:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["paid"]) + "<br><br>"
    
    image = generate_spidergram(list(results.keys()), list(results.values()),
                               f"Индекс максимума команды. Роль: {Settings.roles[test.role].display_name}",
                               "darkred" if test.role=="Manager" else "darkblue")
    return recs,image
async def generate_recommendations_group(test: Test, user_id) -> tuple[str,BytesIO]:
    """Generate recommendation text based on test results"""
    role_data = Settings.roles[test.role]
    average = round(test.average, 2)
    loss_text = ""
    if test.person_cost:
        person_cost = float(test.person_cost)
        loss = (1 - test.average/10) * person_cost
        total_loss = loss * float(test.team_size)
        loss_text=Settings.get_locale("results_losscalc").format(round(total_loss))
    if average==10:
        recs = Settings.get_locale("email_perfect")
    else:
        recs = Settings.get_locale("email_score").format(average, round(100-average*10,2), loss_text)
    
    free_emoji = Settings.config["free_rec_emoji"]
    paid_emoji = Settings.config["paid_rec_emoji"]
    cursor = Settings.db.conn.cursor()
    results = {}
    cursor.execute("SELECT id, name FROM categories")
    categories = {row[0]: row[1] for row in cursor.fetchall()}
    for cat_id, score in test.score.items():
        category = role_data.questions[cat_id]
        category_score = score
        results[cat_id] = category_score
        category_score = round(category_score,1)
        if category_score == 10: continue
        if category_score == 10:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + "<br>" + Settings.get_locale("category_perfect")
        elif category_score > 7.5:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["strong"][cat_id]["paid"]) + "<br><br>"
        elif category_score > 5:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + \
                    f"{free_emoji}{random.choice(Settings.recommendations['weak'][cat_id]['free'])}<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in random.sample(Settings.recommendations["weak"][cat_id]["paid"], 2)) + "<br><br>"
        else:
            recs += Settings.get_locale("aspect_percentage").format(Settings.categories_locales[cat_id], category_score) + \
                    "<br>".join(f"{free_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["free"]) + "<br>" + \
                    "<br>".join(f"{paid_emoji}{i}" for i in Settings.recommendations["weak"][cat_id]["paid"]) + "<br><br>"
    
    cursor.execute("SELECT id FROM results WHERE telegram_username = ? AND role = 'Manager' ORDER BY timestamp DESC LIMIT 1",(user_id,))
    rid = cursor.fetchall()
    manager_results = {}

    if rid:
        cursor.execute("""
            SELECT c.id, c.name, AVG(na.answer)
            FROM results r
            JOIN num_answers na ON r.id = na.id
            JOIN num_questions nq ON na.question_id = nq.id
            JOIN categories c ON nq.category_id = c.id
            WHERE r.role = 'Manager' AND r.id = ?
            GROUP BY c.id, c.name
            ORDER BY c.id
        """,(rid[0][0],))

        for row in cursor.fetchall():
            category_id, category_name, avg_score = row
            manager_results[category_name] = avg_score
    if not manager_results:
        image = generate_spidergram(list(results.keys()), list(results.values()),
                            f"Индекс максимума команды", "darkblue")
    else:
        amount = cursor.execute("SELECT COUNT(*) FROM results").fetchone()
        if not amount: return recs,BytesIO()
        if amount[0] <= 1: image = generate_spidergram(list(results.keys()), list(results.values()),
                            f"Индекс максимума команды", "darkred")
        else:
            employee_results = {}
            for i in results.keys():
                employee_results[i] = (results[i] * amount[0] - manager_results[i])/(amount[0]-1)
            image = generate_double_spidergram(Settings.categories_locales, employee_results, manager_results,f"Индекс максимума команды.")
    return recs,image

async def get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the email conversation - just verify test exists"""
    user_id = update.effective_user.username
    
    cursor = Settings.db.conn.cursor()
    cursor.execute("""
        SELECT average_ti FROM results 
        WHERE telegram_username = ?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))

    res = cursor.fetchone()
    if not res:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_notest"))
        return NO
    cursor.execute("INSERT INTO recommendations (username) VALUES (?)",(user_id,))
    Settings.db.conn.commit()
    if res[0]==10:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_perfect").format("@"+Settings.config["consultation_tg"]))
        return NO
    
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("request_email"))
    context.user_data["state"]=GETTING_EMAIL
def is_valid_email(email):
    if not email or len(email) > 320: return False
    pattern = r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$"
    return bool(re.fullmatch(pattern, email, re.VERBOSE))
async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Now that we have email, generate and send recommendations"""
    if not is_valid_email(update.message.text):
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("bad_email_address"))
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
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_notest"))
        return NO
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_generating"))
    
    result_id, role, industry, team_size, person_cost, average_ti = result
    
    test = Test(update.effective_user.id)
    test.role = role
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
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_sent"))

    return NO
async def get_group_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the email conversation for group results - verify test exists"""
    user_id = update.effective_user.username or update.effective_user.full_name
    
    cursor = Settings.db.conn.cursor()
    
    # Get the latest company created by this user
    cursor.execute("""
        SELECT id FROM companies 
        WHERE created_by = ? 
        ORDER BY id DESC 
        LIMIT 1
    """, (update.effective_user.id,))
    
    company = cursor.fetchone()
    if not company:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_nogrouptest"))
        return NO
    
    company_id = company[0]
    
    # Count number of people who took the test in this company
    cursor.execute("""
        SELECT COUNT(*) FROM results 
        WHERE company_id = ?
    """, (company_id,))
    
    participant_count = cursor.fetchone()[0]
    context.user_data["participant_count"] = participant_count
    
    if participant_count == 0:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_group_notest"))
        return NO
    cursor.execute("INSERT INTO recommendations (username,group_id) VALUES (?,?)",(user_id,company_id))
    Settings.db.conn.commit()
    # Get average score for the group
    cursor.execute("""
        SELECT AVG(average_ti) FROM results 
        WHERE company_id = ?
    """, (company_id,))
    
    avg_score = cursor.fetchone()[0] or 0
    
    if avg_score == 10:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_perfect").format("@"+Settings.config["consultation_tg"]))
        return NO
    
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("request_email"))
    
    # Store company_id and participant_count in context for email generation
    context.user_data['group_email_data'] = {
        'company_id': company_id,
        'participant_count': participant_count
    }
    
    context.user_data["state"]=GETTING_GROUP_EMAIL

async def results_to_pdf(is_group_test: bool,
                         team_size: int,
                         respondents: int,
                         tmi: float,
                         main_recommendation: str,
                         open_answers: list[str],
                         email_recommendation: str,
                         image: BytesIO | None = None
                         ):
    form_data = {
        'type': f'{"Групповой" if is_group_test else "Индивидуальный"}\nотчет',
        'team_size': str(team_size),
        'amnt': str(respondents),
        'tmi': str(round(tmi,2)),
        'main_text': pdf_generator.FillinElement(main_recommendation,12),
        'open_answers': pdf_generator.FillinElement("• "+"\n• ".join(open_answers) if open_answers else "Ни один сотрудник не отправил своего ответа.",16), #'• помидор\n\n• огурец',
        'recommendations':  pdf_generator.FillinElement(email_recommendation,12,False),
        'contacts':Settings.get_locale("pdf_contacts").format("@"+Settings.config["consultation_tg"],Settings.config["owner_mail"]),
        'link': Settings.config["link"],
        'date': datetime.datetime.today().strftime('%d.%m.%Y'),
        'image':'ewfewe'
    }
    return pdf_generator.replace_placeholders_htmlbox('pdf_template.pdf', form_data, image=image)

async def receive_group_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generate and send group recommendations email"""
    if not is_valid_email(update.message.text):
        await context.bot.send_message(update.effective_chat.id,Settings.get_locale("bad_email_address"))
        return GETTING_GROUP_EMAIL

    email = update.message.text
    group_data = context.user_data.get('group_email_data', {})
    company_id = group_data.get('company_id')
    participant_count = group_data.get('participant_count', 0)
    
    if not company_id:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_nogrouptest"))
        return NO

    cursor = Settings.db.conn.cursor()
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_generating"))
    
    # Get aggregated results for the company (similar to stop_group_test logic)
    cursor.execute("""
        SELECT r.role, r.industry, r.team_size, r.person_cost, 
               c.name, AVG(na.answer)
        FROM results r
        JOIN num_answers na ON r.id = na.id
        JOIN num_questions nq ON na.question_id = nq.id
        JOIN categories c ON nq.category_id = c.id
        WHERE r.company_id = ?
        GROUP BY c.id, c.name, r.role, r.industry, r.team_size, r.person_cost
        ORDER BY c.id
    """, (company_id,))

    # Calculate category averages across all participants
    category_scores = {}
    results = cursor.fetchall()
    person_cost_group = None
    team_size_group = None
    industry_group = None
    for row in results:
        role, industry, team_size, person_cost, category_name, avg_score = row
        if category_name not in category_scores:
            category_scores[category_name] = []
        if team_size and not team_size_group: team_size_group=team_size
        if industry and not industry_group: industry_group=industry
        if person_cost and not person_cost_group: person_cost_group=person_cost
        category_scores[category_name].append(avg_score)

    # Create aggregated test object
    test = Test(update.effective_user.id)
    test.role = "Manager"
    test.industry = industry_group
    test.team_size = team_size_group
    test.person_cost = person_cost_group
    test.score = {}
    test.force_average_by_score = True

    # Calculate final category averages
    for category_name, scores in category_scores.items():
        test.score[category_name] = sum(scores) / len(scores) if scores else 0

    # Generate recommendations
    recs, image = await generate_recommendations_group(test,update.effective_user.username)
    
    # Add group-specific information to the email
    group_info = Settings.get_locale("group_test_results_amount").format(participant_count)
    recs_email = group_info + "\n<br>" + recs

    loss_text = ""
    if test.person_cost:
        person_cost = float(test.person_cost)
        loss = (1 - test.average/10) * person_cost
        total_loss = loss * float(test.team_size)
        loss_text=Settings.get_locale("results_losscalc").format(round(total_loss))
    result_text = ""
    recomms_text = ""
    if min(test.score.values())<10:
        additions = [k for k,v in test.score.items() if v<10]
        if not additions: additions = [k for k,v in test.score.items() if v<=min(test.score.values())]
        additions = map(lambda x:Settings.categories_locales[x], additions)
        recomms_text+="\n• "+";\n• ".join(additions)
        recomms_text+="."
        result_text = loss_text+" "+Settings.get_locale("pdf_results").format(round(100-test.average*10,1))
    else:
        result_text = Settings.get_locale("results_perfect")


    cursor.execute("""
        SELECT sq.text, sa.answer 
        FROM str_answers sa
        JOIN str_questions sq ON sa.question_id = sq.id
        JOIN results r ON sa.id = r.id
        WHERE r.company_id = ?
    """, (company_id,))
    
    open_answers = cursor.fetchall()
    
    # Process open answers into a list of strings
    open_answers_list = []
    for question, answer in open_answers:
        open_answers_list.append(answer)

    legend = Settings.get_locale("email_legend").replace("EMOJIFREE",Settings.config["free_rec_emoji"]).replace("EMOJIPAID",Settings.config["paid_rec_emoji"])
    pdf = await results_to_pdf(True,team_size_group,context.user_data["participant_count"],test.average,result_text+recomms_text,open_answers_list,recs+"<br>"+legend,image)
    await send_results_by_email(recs_email, email, image, None, pdf)
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_sent"))

    # Clean up
    context.user_data.pop('group_email_data', None)
    
    return NO
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
        await context.bot.send_message(update.effective_message.chat_id,f"Error getting logs:\n{stderr.decode()}")
        return
    
    logs = stdout.decode()
    for i in range(0, len(logs), 4096):
        await context.bot.send_message(update.effective_message.chat_id,logs[i:i+4096])

async def exec_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute remote command (admin only)"""
    if not await check_admin(update):
        return
    
    if not context.args:
        await context.bot.send_message(update.effective_message.chat_id,"Usage: /exec <command>")
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
            
        await context.bot.send_message(update.effective_message.chat_id,output[:4000])  # Truncate if too long
        
    except Exception as e:
        await context.bot.send_message(update.effective_message.chat_id,f"Error: {str(e)}")

async def get_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send requested file (admin only)"""
    if not await check_admin(update):
        return
    
    if not context.args:
        await context.bot.send_message(update.effective_message.chat_id,"Usage: /getfile <path>")
        return
    
    path = ' '.join(context.args)
    
    try:
        with open(path, 'rb') as f:
            await update.message.reply_document(f)
    except Exception as e:
        await context.bot.send_message(update.effective_message.chat_id,f"Error: {str(e)}")

async def data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_admin(update):
        return
    if not context.args:
        await context.bot.send_message(update.effective_message.chat_id,datascience.main(),reply_markup=ReplyKeyboardMarkup(datascience.keyboard(),resize_keyboard=True))
        return
    if not context.args[0].isidentifier(): await context.bot.send_message(update.effective_message.chat_id,"404")
    val = eval(f"datascience.{context.args[0]}.{context.args[0]}()")
    if isinstance(val,str):
        await context.bot.send_message(update.effective_message.chat_id,val,parse_mode='Markdown')
    elif isinstance(val,BytesIO):
        await update.message.reply_photo(val)
    elif isinstance(val,io.StringIO):
        await update.message.reply_document(val,filename="answers.txt")
    else: await context.bot.send_message(update.effective_message.chat_id,"500")
    
async def put_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Receive and save file (admin only)"""
    if not await check_admin(update):
        return
    
    if not update.message.document and not update.message.reply_to_message:
        await context.bot.send_message(update.effective_message.chat_id,"Reply to a file message with /putfile <destination_path>")
        return
    
    try:
        # Get destination path from command args
        if not context.args:
            await context.bot.send_message(update.effective_message.chat_id,"Usage: /putfile <destination_path>")
            return
        
        dest_path = ' '.join(context.args)
        
        message = update.message.reply_to_message or update.message
        file = await message.document.get_file()
        
        await file.download_to_drive(dest_path)
        await context.bot.send_message(update.effective_message.chat_id,f"File saved to {dest_path}")
        
    except Exception as e:
        await context.bot.send_message(update.effective_message.chat_id,f"Error: {str(e)}")
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(update.effective_message.chat_id,repr(context.user_data))

async def update_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Safe update command that avoids multiple instances"""
    if not await check_admin(update):
        return

    await context.bot.send_message(update.effective_message.chat_id,"🔄 Starting update...")
    
    exit(1)

async def send_launch_message(application):
    startup_msg = f"Bot started successfully!\n• Environment: {sys.platform}"
    await application.bot.send_message(chat_id=Settings.config["main_admin_id"], text=startup_msg)
    await clear_webhook(application)

async def shutdown(application: Application):
    """Gracefully shutdown the bot"""
    try:
        admin_id = Settings.config["main_admin_id"]
        await application.bot.send_message(
            chat_id=admin_id,
            text="🔴 Bot is shutting down"
        )
    except Exception as e:
        print(f"Could not send shutdown message: {e}")
    
    await application.stop()
    await application.shutdown()
    exit(1)

async def sudo_get_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin only: get other user's recommendations."""
    if not await check_admin(update) or len(context.args)<2:
        return
    user_id = context.args[0]
    
    cursor = Settings.db.conn.cursor()
    cursor.execute("""
        SELECT average_ti FROM results 
        WHERE telegram_username = ?
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))

    res = cursor.fetchone()
    if not res:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_notest"))
        return NO
    if res[0]==10:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_perfect").format("@"+Settings.config["consultation_tg"]))
        return NO
    
    """Now that we have email, generate and send recommendations"""
    if not is_valid_email(context.args[1]):
        return NO

    # Fetch data and generate recommendations only now
    cursor = Settings.db.conn.cursor()
    
    # Get basic result info
    cursor.execute("""
        SELECT id, role, industry, team_size, person_cost, average_ti 
        FROM results 
        WHERE telegram_username = ?
        ORDER BY timestamp DESC 
        LIMIT 1
    """, (user_id,))
    result = cursor.fetchone()
    
    if not result:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_notest"))
        return NO
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_generating"))
    
    result_id, role, industry, team_size, person_cost, average_ti = result
    
    test = Test(update.effective_user.id)
    test.role = role
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
    await send_results_by_email(recs, context.args[1], image)
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_sent"))

    return NO

async def sudo_get_group_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the email conversation for group results - verify test exists"""
    user_id = context.args[0]
    cursor = Settings.db.conn.cursor()

    cursor.execute("""
        SELECT company_id FROM results
        WHERE telegram_username = ? AND company_id NOT NULL
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))
    
    company = cursor.fetchone()
    if not company:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_nogrouptest"))
        return NO
    
    company_id = company[0]
    
    # Count number of people who took the test in this company
    cursor.execute("""
        SELECT COUNT(*) FROM results 
        WHERE company_id = ?
    """, (company_id,))
    
    participant_count = cursor.fetchone()[0]
    
    if participant_count == 0:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_group_notest"))
        return NO
    # Get average score for the group
    cursor.execute("""
        SELECT AVG(average_ti) FROM results 
        WHERE company_id = ?
    """, (company_id,))
    
    avg_score = cursor.fetchone()[0] or 0
    
    if avg_score == 10:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_perfect").format("@"+Settings.config["consultation_tg"]))
        return NO

    """Generate and send group recommendations email"""
    email = context.args[1]
    if not is_valid_email(email):
        return NO
    
    if not company_id:
        await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("error_nogrouptest"))
        return NO

    cursor = Settings.db.conn.cursor()
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_generating"))
    
    # Get aggregated results for the company (similar to stop_group_test logic)
    cursor.execute("""
        SELECT r.role, r.industry, r.team_size, r.person_cost, 
               c.name, AVG(na.answer)
        FROM results r
        JOIN num_answers na ON r.id = na.id
        JOIN num_questions nq ON na.question_id = nq.id
        JOIN categories c ON nq.category_id = c.id
        WHERE r.company_id = ?
        GROUP BY c.id, c.name, r.role, r.industry, r.team_size, r.person_cost
        ORDER BY c.id
    """, (company_id,))

    # Calculate category averages across all participants
    category_scores = {}
    results = cursor.fetchall()
    person_cost_group = None
    team_size_group = None
    industry_group = None
    for row in results:
        role, industry, team_size, person_cost, category_name, avg_score = row
        if category_name not in category_scores:
            category_scores[category_name] = []
        if team_size and not team_size_group: team_size_group=team_size
        if industry and not industry_group: industry_group=industry
        if person_cost and not person_cost_group: person_cost_group=person_cost
        category_scores[category_name].append(avg_score)

    # Create aggregated test object
    test = Test(company_id)
    test.role = "Manager"
    test.industry = industry_group
    test.team_size = team_size_group
    test.person_cost = person_cost_group
    test.score = {}
    test.force_average_by_score = True

    # Calculate final category averages
    for category_name, scores in category_scores.items():
        test.score[category_name] = sum(scores) / len(scores) if scores else 0

    # Generate recommendations
    recs, image = await generate_recommendations_group(test,user_id)
    
    # Add group-specific information to the email
    group_info = Settings.get_locale("group_test_results_amount").format(participant_count)
    recs_email = group_info + "\n<br>" + recs

    loss_text = ""
    if test.person_cost:
        person_cost = float(test.person_cost)
        loss = (1 - test.average/10) * person_cost
        total_loss = loss * float(test.team_size)
        loss_text=Settings.get_locale("results_losscalc").format(round(total_loss))
    result_text = ""
    recomms_text = ""
    if min(test.score.values())<10:
        additions = [k for k,v in test.score.items() if v<10]
        if not additions: additions = [k for k,v in test.score.items() if v<=min(test.score.values())]
        additions = map(lambda x:Settings.categories_locales[x], additions)
        recomms_text+="\n• "+";\n• ".join(additions)
        recomms_text+="."
        result_text = loss_text+" "+Settings.get_locale("pdf_results").format(round(100-test.average*10,1))
    else:
        result_text = Settings.get_locale("results_perfect")


    cursor.execute("""
        SELECT sq.text, sa.answer 
        FROM str_answers sa
        JOIN str_questions sq ON sa.question_id = sq.id
        JOIN results r ON sa.id = r.id
        WHERE r.company_id = ?
    """, (company_id,))
    
    open_answers = cursor.fetchall()
    
    # Process open answers into a list of strings
    open_answers_list = []
    for question, answer in open_answers:
        open_answers_list.append(answer)

    legend = Settings.get_locale("email_legend").replace("EMOJIFREE",Settings.config["free_rec_emoji"]).replace("EMOJIPAID",Settings.config["paid_rec_emoji"])
    pdf = await results_to_pdf(True,team_size_group,participant_count,test.average,result_text+recomms_text,open_answers_list,recs+"<br>"+legend,image)
    await send_results_by_email(recs_email, email, image, None, pdf)
    await context.bot.send_message(update.effective_message.chat_id,Settings.get_locale("email_sent"))

    # Clean up
    context.user_data.pop('group_email_data', None)
    
    return NO
    
async def clear_webhook(application: Application):
    try:
        await application.bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        print(f"Failed to delete webhook: {e}")

def main() -> None:
    """Start the bot."""
    # pip.main(['install','-r','requirements.txt'])
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
                                                            .post_init(send_launch_message)\
                                                            .build()

    application.add_handler(CommandHandler("logs", get_logs))
    application.add_handler(CommandHandler("exec", exec_command))
    application.add_handler(CommandHandler("getfile", get_file))
    application.add_handler(CommandHandler("putfile", put_file))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("pong", ping))
    application.add_handler(CommandHandler("update", update_command))
    application.add_handler(CommandHandler("sudo_grouprecomm", sudo_get_group_recommendations))
    application.add_handler(CommandHandler("sudo_recomm", sudo_get_recommendations))
    application.add_handler(CommandHandler("data", data))
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    application.add_handler(CommandHandler("cancel", cancel_test))
    application.add_handler(CommandHandler("starttest", start_test))
    application.add_handler(CommandHandler("getrecommendations", get_recommendations))
    application.add_handler(CommandHandler("getgrouprecommendations", get_group_recommendations))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("grouptest", group_test))
    application.add_handler(CommandHandler("stopgrouptest", stop_group_test))
    application.add_handler(CommandHandler("grouptestresults", group_test_results))
    application.add_handler(CallbackQueryHandler(handle_inline))
    
    # Run the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
