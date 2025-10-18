from . import main_data
from . import get_people_by_industry
from . import get_str_answers
from . import losses
from . import get_full_spidergram
from . import usage_graph
def main():
    return """`/data main_data` - самые главные данные
`/data get_people_by_industry` - пользователей в каждой индустрии
`/data get_str_answers` - открытые ответы от всех пользователей
`/data losses` - потери в рублях
`/data get_full_spidergram` - полная картинка со средними результатами
`/data usage_graph` - график пользования ботом
"""
def keyboard():
    return [["/data main_data"],["/data get_people_by_industry"],["/data get_str_answers"],["/data losses"],["/data get_full_spidergram"],["/data usage_graph"]]