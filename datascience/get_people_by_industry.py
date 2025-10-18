import pandas as pd
import sqlite3

def get_people_by_industry():
    with sqlite3.connect('database.db') as conn:
        restr = 'WHERE telegram_username NOT IN ("irsamo","mironsam1405","vvitalieva","ya_lukyanova")'
        df = pd.read_sql_query(f"SELECT * FROM results {restr}",conn)
    # df.drop_duplicates("telegram_username",inplace=True)

    df_companies = df[df["company_id"].notna()]
    df_single = df[df["company_id"].isna()].drop_duplicates("telegram_username")
    df_single_vc = df_single["industry"].value_counts()
    df_company_managers = df_companies[df_companies["role"]=="Manager"]
    df_company_employees = df_companies[df_companies["role"]!="Manager"]
    df_companies_people = df_company_employees["company_id"].value_counts()
    df_company_vc = pd.Series({k:0 for k in df_single_vc.keys()})
    # print(50 in df_company_managers["company_id"].values)
    for i,v in df_companies_people.items():
        if i not in df_company_managers["company_id"].values: continue
        df_company_vc[df_company_managers[df_company_managers["company_id"]==i]["industry"].iloc[0]]+=v
    return (df_company_vc+df_single_vc).to_string()