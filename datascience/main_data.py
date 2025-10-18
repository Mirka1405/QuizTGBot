import pandas as pd
import sqlite3
import datascience.get_people_by_industry as get_people_by_industry

def main_data():
    with sqlite3.connect('database.db') as conn:
        restr = 'WHERE telegram_username NOT IN ("irsamo","mironsam1405","vvitalieva","ya_lukyanova")'
        df = pd.read_sql_query(f"SELECT * FROM results {restr}",conn)
        df_stransw = pd.read_sql_query(f"SELECT * FROM str_answers",conn)

    passed_test = df["telegram_username"].size
    groups = df['company_id'].dropna(inplace=False).value_counts()
    r = f"""```
======== Статистика за все время ========
Всего прохождений теста: {passed_test}
Уникальных пользователей: {df["telegram_username"].nunique()}
Процент прохождения открытого вопроса: {df_stransw["id"].size/passed_test*100:.2f}%
Групповых тестов всего: {groups.size}
Человек в групповом тесте, в среднем: {groups.mean()}
====== Самые популярные индустрии =======
{get_people_by_industry.get_people_by_industry()}
========== Роли пользователей ===========
Руководители: {df[df['role']=='Manager']['role'].size}
Сотрудники: {df[df['role']!='Manager']['role'].size}```"""
    return r