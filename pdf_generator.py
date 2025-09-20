from dataclasses import dataclass
import io
import fitz
from pathlib import Path
import html
@dataclass()
class FillinElement:
    data: str
    fontsize: int |None = None
    sanitise_html: bool = True

def replace_placeholders_htmlbox(
    input_pdf: str,
    mapping: dict[str, str],
    fontfile: str = "montserrat.ttf",
    height_ratio: float = 0.90,
    left_padding_px: int = 0,
):
    doc = fitz.open(input_pdf)
    font_path = Path(fontfile).resolve().as_posix()  # for CSS url()

    # CSS: register the font and a class we’ll use for the value
    # white-space: nowrap ensures no wrapping (you earlier asked to overflow horizontally)
    base_css = f"""
    .val {{
        color: #000000;
        line-height: 1;
        margin: 0; padding: 0;
    }}
    """

    for page in doc:
        jobs: list[tuple[fitz.Rect, str]] = []

        # 1) Find all {key} occurrences on this page
        for k, v in mapping.items():
            needle = f"{{{{{k}}}}}"
            for rect in page.search_for(needle):
                jobs.append((rect, v))
                # Mask the original placeholder with a white redaction
                page.add_redact_annot(rect, fill=(1, 1, 1))

        if not jobs:
            continue

        # 2) Apply redactions (actually removes original text)
        page.apply_redactions()

        # 3) Re-insert values using HTML (Unicode-safe)
        for rect, value in jobs:
            # Font-size based on the placeholder height
            fontsize_px = max(10, rect.height * height_ratio)

            # Build per-item HTML with computed font-size and small left padding
            html_value = None
            if isinstance(value,str):
                html_value = html.escape(value).replace("\n","<br>")
            if isinstance(value,FillinElement):
                if value.fontsize: fontsize_px = value.fontsize
                html_value = (html.escape(value.data) if value.sanitise_html else value.data).replace("\n","<br>")
            html_snippet = (
                f"<div class='val' style='font-size:{fontsize_px}px; "
                f"padding-left:{left_padding_px}px'>{html_value}</div>"
            )

            # IMPORTANT: insert_htmlbox clips to its rectangle.
            # To allow “overflow to the right”, expand the rect to the page’s right edge.
            target = fitz.Rect(rect.x0, rect.y0, page.rect.x1, page.rect.y1)

            page.insert_htmlbox(target, html_snippet, css=base_css)

    file = io.BytesIO()
    doc.save(file)
    return file

if __name__ == "__main__":
    form_data = {
        'role': 'Руководитель',
        'type': 'Групповой\nотчет',
        'team_size': '24',
        'respondents': '2',
        'tmi': '1.2',
        'main_text': 'Ваши потери составляют 2 100 000 рублей в месяц. Ваша команда может быть на 51% эффективнее за счет реализации мероприятий в рамках следующих категорий: • Технологичность',
        'open_answers': '• помидор\n\n• огурец',
        'recommendations': 'Целеполагание (1.0/10)\n✅Проводите еженедельные сессии приоритезации: “что нас реально приближает к цели?”\n✅Внедрите простую матрицу ответственности (кто за что отвечает)\n💰Внедрите OKR или каскадные KPI, привязанные к бизнес-результатам\n💰Проведите сессию с топ-менеджментом: как мы интерпретируем стратегию и задачи на уровне команд\n\nВзаимодействие (1.0/10)\n✅Введите короткие sync-митинги без формальностей, фокус на высказывании рисков и сложностей\n✅Договоритесь, как давать обратную связь (по модели SBI или ECO)\n💰Проведите фасилитированную командную сессию по коммуникациям и принципам взаимодействия\n💰Закажите оценку климата или сделайте простой pulse-опрос доверия в команде\n\nРоли и вклад (1.0/10)\n✅Проведите инвентаризацию: кто что умеет и где используется меньше своего потенциала\n✅Назначьте внутри команды “мастеров компетенций” — делиться опытом в рамках мини-воркшопов\n💰Запустите mini-программу развития через внутренние проекты / наставничество / внешнее обучение\n💰Проведите GAP-анализ: чего не хватает для задач следующего квартала\n\nТехнологичность (1.0/10)\n✅Назначьте “технологического лидера” — исследовать и внедрять 1 новый инструмент в месяц\n✅Соберите список ручных операций и задач, которые повторяются чаще всего\n💰Закажите аудит: что можно автоматизировать (таблицы, шаблоны, AI-ассистенты)\n💰Подключите AI-инструмент или no-code решение на пилот (1 задача = 1 неделя теста)\n\nНормы и культура (1.0/10)\n✅Сформулируйте и озвучьте 3–5 негласных правил, по которым реально живёт команда — зафиксируйте их\n✅Введите правило: обсуждать ошибки без персональных упрёков, с фокусом на выводах\n💰Проведите сессию с командой: что для нас нормально, а что тормозит результат\n💰Запустите серию “нормообразующих” встреч: кейсы, разборы, примеры поведения\n\nВнизу мы приложили файл results.csv, в котором можно подробнее рассмотреть результаты вашей команды. Этот файл можно открыть в Microsoft Excel, LibreOffice Calc и похожих приложениях.\nЛегенда:\n✅ - мероприятие может быть реализовано собственными силами\n💰 - мероприятие может потребовать инвестиций',
        'tg': '@telegram',
        'email': 'ira@example.org',
        'link': 'example.org',
        'date': '19.09.2025',
        'amnt': '12'
    }
    replace_placeholders_htmlbox('pdf_template.pdf', 'filled_form.pdf', form_data, "montserrat.ttf")

