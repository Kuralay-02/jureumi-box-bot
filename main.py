import json
import os
import gspread
from google.oauth2.service_account import Credentials

print("Starting Google Sheets check...")

# 1. Берём credentials из переменной окружения
creds_json = os.getenv("GOOGLE_CREDENTIALS")
if not creds_json:
    raise Exception("GOOGLE_CREDENTIALS not found")

creds_dict = json.loads(creds_json)

# 2. Настраиваем доступ
scopes = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly"
]

credentials = Credentials.from_service_account_info(
    creds_dict,
    scopes=scopes
)

gc = gspread.authorize(credentials)

# 3. Открываем реестр коробок
SPREADSHEET_ID = "ВСТАВЬ_ID_РЕЕСТРА"

sh = gc.open_by_key(SPREADSHEET_ID)
worksheet = sh.sheet1

rows = worksheet.get_all_records()

print("УСПЕШНО!")
print("Найдено строк:", len(rows))
print("Первая строка:", rows[0] if rows else "Таблица пустая")
