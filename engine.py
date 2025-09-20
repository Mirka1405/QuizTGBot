import json
from os import listdir
import sqlite3
from os.path import join, exists
import time
from typing import Optional, Dict, List, Tuple

class Test:
    def __init__(self, userid: int):
        self.userid = userid
        self.score: dict[str, int] = {}  # category: score
        self.role: str = None
        self.industry: str = None
        self.team_size: int = None
        self.person_cost: str = None
        self.questions_left: list[tuple[str, str]] = []  # (category_id, question)
        self.open_questions_left: list[str] = []  # List of open questions
        self.current_category: str = None
        self.answers: dict[str, tuple[int, str]] = {}
        self.open_answers: dict[str, str] = {}  # {question: answer}
        self.force_average_by_score: bool = False
        self.last_active = time.time()
    
    @property
    def average(self):
        if self.force_average_by_score: return 0 if not self.score else sum(self.score.values())/len(self.score.values())
        if len(self.answers)+len(self.questions_left)==0: return 0
        return sum(self.score.values())/(len(self.answers)+len(self.questions_left))

class QuestionCategory:
    def __init__(self, display: str, questions: list[str] | None = None):
        self.display_name = display
        self.questions = questions or []
    
    def __repr__(self):
        return f"QuestionCategory({self.display_name}, {self.questions})"

class Role:
    def __init__(self, display: str, questions: dict[str, QuestionCategory] | None = None, open_questions: list[str] | None = None):
        self.display_name = display
        self.questions = questions or {}
        self.open_questions = open_questions or []
    
    def __repr__(self):
        return f"Role({self.display_name}, {self.questions}, open_questions={self.open_questions})"

class Settings:
    config: dict[str, str] = {}
    locales: dict[str, dict[str, str]] = {}
    ongoing_tests: dict[int, Test] = {}
    roles: dict[str, Role] = {}
    industries: list[str] = []
    db: "DatabaseManager" = None
    recommendations: dict[str,dict[str,dict[str,str]]] = {}
    html: str = "{0}{1}"
    categories_locales: dict[str,str] = {}
    role_locales: dict[str,str] = {}
    admins: set[str] = {}

    button_callbacks: dict = {}
    skip_locales: set = {"/skip"}

    @classmethod
    def load_admins(cls,filename:str="admins.txt"):
        with open(filename,"r",encoding="utf-8") as f:
            cls.admins = {i.strip() for i in f.readlines()}
    @classmethod
    def load_html_template(cls,filename:str="email_template.html"):
        with open(filename,"r",encoding="utf-8") as f:
            cls.html = f.read()
    @classmethod
    def init_db(cls,filename:str="quiz_results.db"):
        cls.db = DatabaseManager(filename)
    @classmethod
    def get_config(cls, file: str = "config.json"):
        try:
            with open(file, "r", encoding="utf-8") as f:
                cls.config = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError("Config file not found.")
    
    @classmethod
    def load_locales(cls, dir: str):
        for filename in listdir(dir):
            if filename.endswith(".json"):
                name = filename[:-5]
                try:
                    with open(join(dir, filename), "r", encoding="utf-8") as f:
                        cls.locales[name] = json.load(f)
                except Exception as e:
                    print(f"Error loading locale {filename}: {e}")
    
    @classmethod
    def get_locale(cls, string: str, locale: str = "ru_RU"):
        return cls.locales.get(locale, {}).get(string, string)
    
    @classmethod
    def get_questions(cls, file: str):
        try:
            with open(file, "r", encoding="utf-8") as f:
                content = json.load(f)
        except FileNotFoundError:
            raise FileNotFoundError("Question file not found.")
        
        cls.categories_locales = content["categories"]
        cls.role_locales = content["roles"]
        roles: dict[str, str] = content.get("roles", {})
        categories: dict[str, str] = content.get("categories", {})
        open_questions: list[str] = content.get("open_questions", [])
        
        cls.roles = {role_id: Role(display_name) for role_id, display_name in roles.items()}
        
        for role_id in cls.roles:
            role_data = content.get(role_id, {})
            category_obj: dict[str, QuestionCategory] = {}
            
            for cat_id, questions in role_data.items():
                if cat_id in categories:
                    category_obj[cat_id] = QuestionCategory(
                        display=categories[cat_id],
                        questions=questions
                    )
            
            cls.roles[role_id].questions = category_obj
            cls.roles[role_id].open_questions = open_questions
        
        # Initialize categories in database
        cls.db._init_categories()
    
    @classmethod
    def load_industries(cls, file: str = "industries.txt"):
        if exists(file):
            with open(file, "r", encoding="utf-8") as f:
                cls.industries = [line.strip() for line in f if line.strip()]
        else:
            raise FileNotFoundError(f"Нет файла индустрий {file}")
    
    @classmethod
    def load_recommendations(cls, file: str = "recommendations.json"):
        with open(file,"r",encoding="utf-8") as f:
            cls.recommendations = json.load(f)
    @classmethod
    def get_score_keyboard(cls):
        return [[str(i) for i in range(1, 11)]]
    
    @classmethod
    def cleanup_old_tests(cls):
        now = time.time()
        for user_id, test in list(cls.ongoing_tests.items()):
            if now - test.last_active > 3600:
                del Settings.ongoing_tests[user_id]

    @classmethod
    def add_button_locales(cls,buttons:dict,locale:str="ru_RU"):
        for k in buttons:
            cls.button_callbacks[k] = buttons[k]
            loc = cls.get_locale("button_"+k,locale)
            if loc in cls.button_callbacks.keys(): return
            cls.button_callbacks[loc] = buttons[k]
class DatabaseManager:
    def __init__(self, db_path: str = "quiz_results.db"):
        self.conn = sqlite3.connect(db_path,timeout=30)
        self.create_tables()
        self._init_categories()
    
    def create_tables(self):
        """Ensure all tables exist with proper schema"""
        cursor = self.conn.cursor()
        
        cursor.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER,
            is_active BOOLEAN DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS num_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE NOT NULL,
            text TEXT NOT NULL UNIQUE,
            category_id INTEGER REFERENCES categories(id)
        );
        
        CREATE TABLE IF NOT EXISTS str_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE NOT NULL,
            text TEXT NOT NULL UNIQUE
        );
        
        CREATE TABLE IF NOT EXISTS role_categories (
            role TEXT NOT NULL,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            PRIMARY KEY (role, category_id)
        );
        
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_username TEXT,
            company_id INTEGER REFERENCES companies(id),
            role TEXT NOT NULL,
            industry TEXT,
            team_size INTEGER,
            person_cost INTEGER,
            average_ti FLOAT NOT NULL,
            estimated_losses FLOAT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS num_answers (
            id INTEGER NOT NULL REFERENCES results(id),
            question_id INTEGER NOT NULL REFERENCES num_questions(id),
            answer INTEGER NOT NULL,
            PRIMARY KEY (id, question_id)
        );
        
        CREATE TABLE IF NOT EXISTS str_answers (
            id INTEGER REFERENCES results(id),
            question_id INTEGER REFERENCES str_questions(id),
            answer TEXT,
            PRIMARY KEY (id, question_id)
        );
        """)
        self.conn.commit()
    def _init_categories(self):
        """Initialize categories from Settings if they don't exist"""
        cursor = self.conn.cursor()
        
        for role_id, role in Settings.roles.items():
            for cat_id, category in role.questions.items():
                # Insert category if not exists (only name now)
                cursor.execute(
                    "INSERT OR IGNORE INTO categories (name) VALUES (?)",
                    (cat_id,)  # Only storing the ID/name, not display_name
                )
                # Link category to role
                cursor.execute(
                    """INSERT OR IGNORE INTO role_categories (role, category_id)
                    VALUES (?, (SELECT id FROM categories WHERE name = ?))""",
                    (role_id, cat_id)
                )
        
        self.conn.commit()
    
    def create_company(self, creator_id: int) -> int:
        """Create a new company and return its ID"""
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO companies (created_by) VALUES (?)", (creator_id,))
        self.conn.commit()
        return cursor.lastrowid
    
    def save_results(self, test: Test, telegram_username: str, company_id: Optional[int] = None):
        cursor = self.conn.cursor()
        
        estimated_losses = None
        if test.person_cost is not None:
            estimated_losses = ((1-test.average/10) * float(test.person_cost)*float(test.team_size))
        
        # Insert main result
        cursor.execute("""
        INSERT INTO results (
            telegram_username, company_id, role, industry, 
            team_size, person_cost, average_ti, estimated_losses
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            telegram_username, company_id,
            test.role,
            test.industry,
            test.team_size,
            int(test.person_cost) if test.person_cost and test.person_cost.isdigit() else None,
            test.average,
            estimated_losses
        ))
        result_id = cursor.lastrowid
        
        # Save numerical answers with their actual categories
        for question_text, (answer, category_name) in test.answers.items():
            # Get category ID from name
            cursor.execute("SELECT id FROM categories WHERE name = ?", (category_name,))
            category_id = cursor.fetchone()[0]
            
            # Insert/update question with proper category ID
            cursor.execute("""
            INSERT INTO num_questions (text, category_id)
            VALUES (?, ?)
            ON CONFLICT(text) DO UPDATE SET category_id=excluded.category_id
            RETURNING id
            """, (question_text, category_id))
            
            question_id = cursor.fetchone()[0]
            
            cursor.execute(
                "INSERT INTO num_answers VALUES (?, ?, ?)",
                (result_id, question_id, answer)
            )
        
        # Save string answers (no categories)
        for question_text, answer in test.open_answers.items():
            cursor.execute("""
            INSERT INTO str_questions (text)
            VALUES (?)
            ON CONFLICT(text) DO NOTHING
            RETURNING id
            """, (question_text,))
            
            row = cursor.fetchone()
            if row:
                question_id = row[0]
            else:
                cursor.execute("SELECT id FROM str_questions WHERE text = ?", (question_text,))
                question_id = cursor.fetchone()[0]
            
            cursor.execute(
                "INSERT INTO str_answers VALUES (?, ?, ?)",
                (result_id, question_id, answer)
            )
        
        self.conn.commit()
        return result_id
    
    def get_company_results_csv(self, company_id: int) -> str:
        """Generate CSV data for company results with categories"""
        cursor = self.conn.cursor()
        
        cursor.execute("""
        SELECT nq.text, c.name as category_name
        FROM num_questions nq
        JOIN categories c ON nq.category_id = c.id
        JOIN num_answers n ON nq.id = n.question_id
        JOIN results r ON n.id = r.id
        WHERE r.company_id = ?
        GROUP BY nq.id
        
        UNION
        
        SELECT sq.text, NULL as category_name
        FROM str_questions sq
        JOIN str_answers s ON sq.id = s.question_id
        JOIN results r ON s.id = r.id
        WHERE r.company_id = ?
        GROUP BY sq.id
        """, (company_id, company_id))
        questions = cursor.fetchall()
        categories = set(q[1] for q in questions if q[1] is not None)
        
        # 3. Prepare CSV headers
        headers = [
            "username", "role", "industry", "team_size", 
            "person_cost", "average_ti"
        ]
        headers.extend(f"ti_{cat}" for cat in categories)  # Category scores
        headers.extend(f'"{q[0]}"' for q in questions)     # Questions (quoted)
        
        cursor.execute("""
        SELECT 
            r.id,
            c.name as category_name,
            AVG(n.answer) as category_avg
        FROM results r
        JOIN num_answers n ON r.id = n.id
        JOIN num_questions nq ON n.question_id = nq.id
        JOIN categories c ON nq.category_id = c.id
        WHERE r.company_id = ?
        GROUP BY r.id, c.name
        """, (company_id,))
        
        category_scores = {}
        for row in cursor.fetchall():
            result_id = row[0]
            if result_id not in category_scores.keys():
                category_scores[result_id] = {}
            category_scores[result_id][row[1]] = row[2]
        
        # 5. Get all results and their answers
        cursor.execute("""
        SELECT 
            r.id, r.telegram_username, r.role, r.industry, 
            r.team_size, r.person_cost, r.average_ti
        FROM results r
        WHERE r.company_id = ?
        """, (company_id,))
        
        # 6. Get all answers
        cursor.execute("""
        SELECT r.id, nq.text, n.answer
        FROM results r
        JOIN num_answers n ON r.id = n.id
        JOIN num_questions nq ON n.question_id = nq.id
        WHERE r.company_id = ?
        
        UNION ALL
        
        SELECT r.id, sq.text, s.answer
        FROM results r
        JOIN str_answers s ON r.id = s.id
        JOIN str_questions sq ON s.question_id = sq.id
        WHERE r.company_id = ?
        """, (company_id, company_id))
        
        answers = {}
        for row in cursor.fetchall():
            if row[0] not in answers:
                answers[row[0]] = {}
            answers[row[0]][row[1]] = row[2]
        
        # 7. Generate CSV
        csv_data = [",".join(headers)]
        
        cursor.execute("""
        SELECT id FROM results WHERE company_id = ?
        """, (company_id,))
        
        for result_id, in cursor.fetchall():
            # Get basic result info
            cursor.execute("""
            SELECT 
                telegram_username, role, industry, 
                team_size, person_cost, average_ti
            FROM results
            WHERE id = ?
            """, (result_id,))
            user_data = cursor.fetchone()
            
            if not user_data:
                continue
                
            # Build the row
            row = [
                user_data[0],  # username
                user_data[1],  # role
                user_data[2] if user_data[2] else "-",  # industry
                str(user_data[3]) if user_data[3] else "0",  # team_size
                str(user_data[4]) if user_data[4] else "0",  # person_cost
                str(user_data[5])  # average_ti
            ]
            
            # Add category scores
            for cat in categories:
                score = category_scores.get(result_id, {}).get(cat, "")
                row.append(str(score) if score is not None else "")
            
            # Add answers
            result_answers = answers.get(result_id, {})
            for q in questions:
                row.append(str(result_answers.get(q[0], "")))
            
            csv_data.append(",".join(row))
        
        return "\n".join(csv_data)
    def close(self):
        self.conn.close()