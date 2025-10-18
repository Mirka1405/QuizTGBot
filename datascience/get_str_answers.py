import pandas as pd
import sqlite3
from io import StringIO

def get_str_answers():
    with sqlite3.connect('database.db') as conn:
        df = pd.read_sql_query("SELECT r.company_id,sa.answer FROM results r JOIN str_answers sa ON r.id = sa.id",conn)

    df_sorted = df.dropna().sort_values("company_id")
    r = StringIO()
    for i in df_sorted["company_id"].unique():
        r.write(f"\n========== {int(i)} ==========\n")
        group = df_sorted[df_sorted["company_id"]==i].set_index("company_id")
        for i in group["answer"]: r.write(i+"\n")
    r.seek(0)
    return r