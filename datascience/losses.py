import pandas as pd
import sqlite3
import numpy as np

def losses():
    with sqlite3.connect('database.db') as conn:
        restr = 'WHERE telegram_username NOT IN ("irsamo","mironsam1405","vvitalieva","ya_lukyanova")'
        df = pd.read_sql_query(f"SELECT * FROM results {restr}",conn)

    avg_ti = df[df["estimated_losses"].notna()]["average_ti"].mean()
    df1,df2 = df[df["company_id"].notna()],df[df["company_id"].isna()]
    avg_ti1 = df1[df1["estimated_losses"].notna()]["average_ti"].mean()
    avg_ti2 = df2[df2["estimated_losses"].notna()]["average_ti"].mean()
    r = f"""```
--- Потери у всех: ---
Потери в рублях: {df["estimated_losses"].dropna(inplace=False).mean():.2f}
Потери в процентах: {100-avg_ti*10:.2f}%
Средний ИМК (кто указывал стоимость): {avg_ti:.2f}/10
--- Потери в командах: ---
Потери в рублях: {df1["estimated_losses"].dropna(inplace=False).mean():.2f}
Потери в рублях на человека: {df1["estimated_losses"].dropna(inplace=False).mean():.2f}
Потери в процентах: {100-avg_ti1*10:.2f}%
Средний ИМК (кто указывал стоимость): {avg_ti1:.2f}/10
--- Потери индивидуально: --- 
Потери в рублях: {df2["estimated_losses"].dropna(inplace=False).mean():.2f}
Потери в процентах: {100-avg_ti2*10:.2f}%
Средний ИМК (кто указывал стоимость): {avg_ti2:.2f}/10```"""
    return r