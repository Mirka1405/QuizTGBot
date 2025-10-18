import pandas as pd
import sqlite3
import spidergram

def get_full_spidergram():
    with sqlite3.connect('database.db') as conn:
        df = pd.read_sql_query("""
            SELECT r.role, c.name, AVG(na.answer)
            FROM results r
            JOIN num_answers na ON r.id = na.id
            JOIN num_questions nq ON na.question_id = nq.id
            JOIN categories c ON nq.category_id = c.id
            WHERE r.telegram_username NOT IN ("irsamo","mironsam1405","vvitalieva","ya_lukyanova")
            GROUP BY c.id, c.name, r.role
            ORDER BY c.id
        """,conn)
        cat_locale = {
            "Thrust": "Целеполагание",
            "Trust": "Взаимодействие",
            "Talent & Skills": "Роли и вклад",
            "Technology & AI": "Технологичность",
            "Tenets": "Нормы и культура"
        }

    man = df[df["role"]=="Manager"]
    man.drop(columns="role")
    emp = df[df["role"]=="Employee"]
    emp.drop(columns="role")
    man.set_index("name",inplace=True)
    emp.set_index("name",inplace=True)
    return spidergram.generate_double_spidergram(cat_locale,
                                                man["AVG(na.answer)"].to_dict(),
                                                emp["AVG(na.answer)"].to_dict(),
                                                "Среднее значение")