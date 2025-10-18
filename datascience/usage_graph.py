import pandas as pd
import sqlite3
import matplotlib.pyplot as plt
from io import BytesIO

def usage_graph():
    with sqlite3.connect('database.db') as conn:
        restr = 'WHERE telegram_username NOT IN ("irsamo","mironsam1405","vvitalieva","ya_lukyanova")'
        df = pd.read_sql_query(f"SELECT * FROM results {restr}",conn)

    df.drop_duplicates("telegram_username",inplace=True)
    df["timestamp"] = df["timestamp"].astype(str).str[5:10]
    plt.plot(df['timestamp'].value_counts().sort_index())
    plt.xticks(rotation=90)
    img_buffer = BytesIO()
    plt.savefig(img_buffer, format='png', dpi=96, bbox_inches='tight')
    img_buffer.seek(0)
    plt.close()
    return img_buffer
