# ============================================================
# داشبورد پایش کیت‌ها — چندبخشی (هورمون / بیوشیمی و قابل توسعه)
# اجرا مستقیماً از روی دیتابیس‌های آرشیوشده؛ بدون تماس با API.
#
# نسخه‌ی «فقط کیت»: بدون هیچ‌گونه هزینه/قیمت/درآمد.
# ============================================================

import os
import re
import json
import sqlite3
import traceback
import urllib.request
import urllib.error
import jdatetime
import pandas as pd
from flask import Flask, render_template, jsonify, request
from werkzeug.utils import secure_filename

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except Exception:
    _HAS_SKLEARN = False

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except Exception:
    _HAS_BM25 = False

app = Flask(__name__)

# اگر مانده‌ی روز انقضا کمتر از این باشد، در پارتی‌ها هشدار داده می‌شود
NEAR_EXPIRY_DAYS = 30

# ---- تنظیمات دستیار هوشمند (تب ۴) — مدل زبانی محلی و رایگان (Ollama) ----
# پیش‌فرض روی 127.0.0.1 (نه localhost) تا روی ویندوز مشکل IPv6 پیش نیاد
OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')
OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'qwen2.5:7b-instruct')
CHAT_TOP_K = 8

# کلمات رایج فارسی که در بازیابی نویز ایجاد می‌کنند
FA_STOPWORDS = {
    'از', 'به', 'با', 'که', 'این', 'آن', 'را', 'در', 'و', 'یا', 'برای',
    'است', 'هست', 'بود', 'شد', 'می', 'هم', 'چه', 'کدام', 'چند', 'چطور',
    'چگونه', 'کجا', 'کی', 'آیا', 'تا', 'بر', 'همه', 'هر', 'یک', 'دو',
    'می‌شود', 'می‌شود؟', 'شده', 'شده‌اند', 'دارد', 'دارند', 'بوده',
    'کدام‌اند', 'کدامند', 'بیشترین', 'کمترین', 'چقدر', 'چیست',
    'بگیر', 'بپرس',
}

EXCLUDE_DEVICE_PATTERNS = ['P2P', 'وب سرويس', 'وب سرویس', 'دريافت جواب', 'دریافت جواب', 'WorkList']


# ------------------------------------------------------------
# کمک‌کننده‌های عمومی استخراج نام دستگاه (مشترک بین همه‌ی بخش‌ها)
# ------------------------------------------------------------
def extract_device_name(log_desc):
    """
    نام دستگاه را از logDesc استخراج می‌کند بدون گم‌کردن پسوند نمونه‌ی
    دستگاه (مثل '-1' در "MindrayBS800ASTM-1-نسخه: ..."). یک split('-')[0]
    ساده این پسوند را حذف می‌کند و باعث ادغام دو دستگاه فیزیکی جدا زیر یک
    نام می‌شود.
    """
    log_desc = log_desc or ''
    if not log_desc:
        return 'Unknown'
    first_line = log_desc.split('\n')[0].strip()
    if 'نسخه' in first_line:
        return first_line.split('نسخه')[0].rstrip('-').strip()
    return first_line.split('-')[0].strip()


def ul_to_ml(ul):
    return ul / 1000


# ------------------------------------------------------------
# کانفیگ هر بخش — برای اضافه‌کردن بخش جدید فقط یک ورودی دیگر به
# این دیکشنری اضافه کنید.
# ------------------------------------------------------------
DEPARTMENTS = {

    'hormone': {
        'label': 'هورمون',
        'db_path': os.environ.get('HORMONE_DB_PATH', 'historical_database_hafez.db'),
        'repeats_cache_path': os.environ.get('HORMONE_REPEATS_CACHE_PATH', 'repeats_cache_hormone.xlsx'),

        'test_names': {
            '1005': 'TSH', '1001': 'T3', '1003': 'T3 Uptake', '1002': 'T4',
            '1006': 'Free T3', '1007': 'Free T4', '1008': 'FSH', '1009': 'LH',
            '1010': 'Prolactin', '1011': 'Testosterone', '1014': 'DHEA',
            '1015': 'Progesterone', '1018': 'Estradiol', '1019': 'Ferritin',
            '1042': 'Pro-BNP', '1057': 'Beta (بجز غربالگری)', '1032': 'Beta',
            '53': 'Procalcitonin (PCT)', '697': 'TPO', '1136': 'PSA', '1148': 'FPSA',
            '102': 'Vit B12', '103': 'Folate (Vit BC)', '777': 'HCV', '788': 'HTLV',
            '768': 'HBSAg', '769': 'HBSAb', '151': 'Digoxin', '105': 'Vit D',
            '43': 'Troponin', '783': 'HIV', '1021': 'Cortisol 8AM', '1024': 'Cortisol Random',
        },

        'device_types': {
            'Cobas_e411': 'Cobas', 'Cobas_e411_2': 'Cobas', 'Cobas_e411_3': 'Cobas',
            'ARCHITECTPlus': 'Architect', 'ARCHITECTPlus_FullQuery': 'Architect',
        },

        'capacity_mode': 'direct',
    },

    'biochemistry': {
        'label': 'بیوشیمی',
        'db_path': os.environ.get('BIOCHEM_DB_PATH', 'historical_database_biochemistry_hafez.db'),
        'repeats_cache_path': os.environ.get('BIOCHEM_REPEATS_CACHE_PATH', 'repeats_cache_biochemistry.xlsx'),

        'test_names': {
            '1': 'FBS', '2': 'Glu 2hpp', '3': 'BS', '4': 'Glu 4pm', '5': 'Glu5pm',
            '6': 'Glu 6pm', '8': 'GTT 30min', '9': 'GTT 60', '10': 'GTT Fast',
            '11': 'GTT 120', '12': 'GTT 180', '42': 'Glu 1hpp', '13': 'BUN',
            '14': 'Creat', '15': 'U-A', '16': 'Chol', '17': 'TG', '18': 'HDL',
            '19': 'LDL', '30': 'BioT', '31': 'BiliD', '33': 'SGOT', '34': 'SGPT',
            '36': 'ALP', '23': 'Ca', '24': 'Phos', '39': 'CPK', '40': 'CK-MB',
            '38': 'LDH', '25': 'Na', '26': 'K', '46': 'GCT', '50': 'GGT',
            '56': 'Fe', '57': 'TIBC', '29': 'Mg', '603': 'CRPQ', '602': 'CRP',
            '605': 'RF', '606': 'RFQ', '81': 'Zn', '62': 'ALB', '61': 'ProT',
            '113': 'GTT 90min', '72': 'GTT Fast P', '73': 'Glu 2pm',
            '74': 'GTT 120 P', '99': 'Glu 8pm',
        },

        'device_types': {
            'MindrayBS800ASTM': 'Mindray BS-800',
            'MindrayBS800ASTM-1': 'Mindray BS-800-1',
            'MindrayBS800': 'Mindray BS-800',
            'BS800': 'Mindray BS-800',
            'EasyLite-1': 'EasyLite',
            'EasyLite-2': 'EasyLite',
            'EasyLite': 'EasyLite',
            'Easy Lite': 'EasyLite',
        },

        'capacity_mode': 'volume_ml',
        'shared_kit_groups': {
            'glucose_group': {
                'tests': ['1', '2', '3', '4', '5', '6', '8', '9', '10', '11', '12',
                          '42', '72', '73', '74', '99', '113'],
                'consumption_per_test_ul': 200,
            },
            'rf_group': {'tests': ['605', '606'], 'consumption_per_test_ul': 200},
            'crp_group': {'tests': ['602', '603'], 'consumption_per_test_ul': 180},
        },
        'test_consumption_ul': {
            '13': 150, '14': 200, '15': 200, '16': 150, '17': 150, '18': 200,
            '19': 200, '30': 200, '31': 150, '33': 150, '34': 150, '36': 200,
            '23': 200, '24': 200, '39': 200, '40': 150, '38': 200, '25': 150,
            '26': 150, '46': 200, '50': 180, '56': 200, '57': 300, '29': 200,
            '62': 120, '61': 120, '81': 200,
        },
    },
    'hematology': {
        'label': 'هماتولوژی',
        'db_path': os.environ.get('HEMATOLOGY_DB_PATH', 'historical_database_hematology_hafez.db'),
        'repeats_cache_path': os.environ.get('HEMATOLOGY_REPEATS_CACHE_PATH', 'repeats_cache_hematology.xlsx'),

        'test_names': {
            '301': 'CBC',
            '301001': 'WBC', '301002': 'RBC', '301003': 'Hemoglobin', '301004': 'Hematocrite',
            '301005': 'MCV', '301006': 'MCH', '301007': 'MCHC', '301008': 'Neutrophils',
            '301009': 'Lymphocyte', '301010': 'Monocyte', '301011': 'Basophil', '301012': 'Band Cells',
            '301013': 'NRBC', '301014': 'Metamyelocyte', '301015': 'Myelocyte', '301016': 'Promylocyte',
            '301017': 'Blast', '301018': 'Prolymphocyte', '301019': 'Promonocyte',
            '301020': 'RBC morphology', '301021': 'Anisocytosis', '301022': 'microcyte',
            '301023': 'Hypochromia', '301024': 'Anisochromia', '301025': 'Poikilocytosis',
            '301026': 'Target Cell', '301027': 'Basophilic Stippling', '301028': 'Polychromasia',
            '301029': 'Macrocyte', '301030': 'Schistocyte', '301031': 'Helmet Cell', '301032': 'Tear Drop',
            '301033': 'Acanthocyte', '301034': 'Stomatocyte', '301035': 'Super Cell', '301036': 'Bliter Cell',
            '301037': 'Burr Cell', '301038': 'Bite Cell', '301039': 'Spherocyte', '301040': 'Ovalocyte',
            '301041': 'Marco Ovalocyte', '301042': 'Elliptocyte', '301043': 'Pencil Shape',
            '301044': 'Sickle Cell', '301045': 'Cabot Ring', '301046': 'Howel joly Body',
            '301047': 'Pappen Heimer body', '301048': 'Heinz body', '301049': 'Golf Ball',
            '301050': 'Rouleaux formation', '301051': 'WBC Morphology', '301052': 'Reaction lym',
            '301053': 'Toxic Granulation of PMN', '301054': 'Dohle body',
            '301055': 'Super segmented neutrophils', '301056': 'Cytoplasmic vaculization Next',
            '301057': 'Smudge Cell', '301058': 'Basket Cell', '301059': 'Giant metamyelocyte',
            '301060': 'Apoptotic', '301061': 'Mast Cell', '301062': 'Plasma Cell', '301063': 'RDW-CD',
            '301064': 'Platelet', '301065': 'Platelet-count', '301066': 'PLT morph',
            '301067': 'Giant Platelet', '301068': 'Large Platelets', '301069': 'Hypo Granular Platelets',
            '301070': 'Dwarf megakarcyocute', '301071': 'Eosinophil', '301072': 'RDW-SD', '301073': 'PDW',
            '301074': 'MPV', '301075': 'P-LCR', '301076': 'PCT', '301077': 'HDW', '301078': 'Abnormal Cell',
            '301079': 'Atypical lymph', '301080': 'LI', '301081': 'MPXI', '301082': 'Abnormal Lymph',
            '301083': 'Abnormal Cell', '301084': 'Neutrophil#', '301085': 'Lymphocyte#',
            '301086': 'monocyte#', '301087': 'Eosinophil#', '301088': 'Basophils#',
            '301089': 'Hypo Segmented Mectrophils', '301090': 'P-LCC', '301091': 'IMG#', '301092': 'IMG%',
            '301093': 'NRBC#', '301094': 'PLT-I', '301095': 'ipf',
            '619': 'Co.D', '620': 'Co.i',
            '389': 'Retic', '389001': 'RET#', '389002': 'RET%', '389003': 'iRF', '389004': 'LFR',
            '389005': 'MFR', '389006': 'HFR', '389007': 'RHE',
            '386': 'ESR', '453': 'PT', '453001': 'PT patient', '453002': 'PT control', '453003': 'PT activity',
            '453004': 'INR', '453006': 'Ratio',
            '457': 'PTT', '457001': 'PTT control', '457002': 'PTT patient',
            '469': 'Factor 13', '488': 'SCSA', '1457': 'SDF',
            '270': 'CD-16', '284': 'CD-4', '285': 'CD-5', '297': 'CD-23', '287': 'CD-56', '271': 'CD-8',
            '290': 'CD-19', '324': 'CD-59', '323': 'CD-55', '482': 'CD-10', '272': 'CD-45', '274': 'CD-11a',
            '276': 'CD-11b', '277': 'CD-11c', '251': 'CD-117', '269': 'CD-14', '493': 'CD-200',
            '279': 'CD-33', '250': 'CD-64', '325': 'PE-TDT', '255': 'CD-138', '283': 'CD-3',
            '286': 'CD-34', '267': 'CD-103', '266': 'CD-13', '292': 'CD-20', '291': 'CD-21',
            '265': 'HLA-DR', '268': 'CD-22', '298': 'CD-25', '299': 'CD-7', '278': 'CD-15', '275': 'CD-18',
            '300': 'CD-38', '288': 'CD-41', '494': 'CD-42a', '256': 'CD-42b', '289': 'CD-61',
            '491': 'Kappa', '492': 'Landa', '487': 'DHR', '264': 'FMC 7',
            '391': 'BgRH', '391001': 'Blood Group', '391002': 'RH',
            '456003': 'Collagen', '456005': 'Epinephrine', '456001': 'ADP', '456002': 'Ristocetin',
            '456004': 'Arachidonic Acid',
        },

        'device_types': {
            'SysmexXT2000-2': 'SysmexXT2000-2',
            'MindrayBC6200ASTM': 'MindrayBC6200ASTM',
            'Mythic70': 'Mythic70',
            'Lena': 'Lena',
            'SysmexCA600': 'SysmexCA600',
            'Sysmex-Cs2400': 'Cs2400',
        },

        'capacity_mode': 'volume_ml',
        'shared_kit_groups': {
            'coag_group': {'tests': ['619', '620'], 'consumption_per_test_ul': 75},
        },
        'test_consumption_ul': {
            '457': 50,
            '453': 100,
            '469': 180,
            '389': 100,
        },
        'count_based_tests': {
            '270', '284', '285', '297', '287', '271', '290', '324', '323', '482', '272', '274',
            '276', '277', '251', '269', '493', '279', '250', '325', '255', '283', '286', '267',
            '266', '292', '291', '265', '268', '298', '299', '278', '275', '300', '288', '494',
            '256', '289', '491', '492', '487', '264',
            '391', '391001', '391002',
            '301001', '301',
            '456003', '456005', '456001', '456002', '456004',
        },

        'device_groups': {
            '301': [
                {
                    'group_name': 'Mindray',
                    'device_keywords': ['mindray'],
                    'device_excludes': [],
                    'count_source_code': '301001',
                    'item_keywords': None,
                },
            ],
            '301001': [
                {
                    'group_name': 'Sysmex (اصلی + جایگزین Fara)',
                    'device_keywords': ['xt2000', 'sysmex'],
                    'device_excludes': ['ca600', 'cs2400'],
                    'count_source_code': '301001',
                    'item_keywords': ['sysmex', 'ffs', 'fara'],
                },
                {
                    'group_name': 'Orphee / Mythic',
                    'device_keywords': ['mythic'],
                    'device_excludes': [],
                    'count_source_code': '301001',
                    'item_keywords': ['orphee', 'mythic'],
                },
            ],
        },

        'test_count_source': {
            '457': '457002',
            '453': '453001',
            '391': '391001',
            '389': '389001',
        },

        'shared_capacity_groups': [
            {'group_name': 'کیت مشترک Co.D/Co.i', 'codes': ['619', '620']},
        ],
    },
}

DEFAULT_DEPARTMENT = 'hormone'


def get_department(key):
    dept = DEPARTMENTS.get(key)
    if dept is None:
        raise ValueError(f"بخش نامعتبر: {key}")
    return dept


def get_consumption_ml_for_test(dept, code):
    for group in dept.get('shared_kit_groups', {}).values():
        if code in group['tests']:
            return ul_to_ml(group['consumption_per_test_ul'])
    ul = dept.get('test_consumption_ul', {}).get(code)
    return ul_to_ml(ul) if ul is not None else None


def is_count_based(dept, code):
    return code in dept.get('count_based_tests', set())


def _device_matches_group(device_name, group_conf):
    dn = (device_name or '').lower()
    if any(p in dn for p in (group_conf.get('device_excludes') or [])):
        return False
    keywords = group_conf.get('device_keywords')
    if keywords is None:
        return True
    return any(kw in dn for kw in keywords)


def _item_matches_group(kit_name, group_conf):
    keywords = group_conf.get('item_keywords')
    if keywords is None:
        return True
    g = (kit_name or '').lower()
    return any(kw.lower() in g for kw in keywords)


def _waste_stats(capacity, total_tests, capacity_unknown):
    if capacity_unknown:
        return None, None
    waste = capacity - total_tests
    waste_percent = round((waste / capacity) * 100, 1) if capacity > 0 else None
    return waste, waste_percent


# ------------------------------------------------------------
# کمک‌کننده‌های دیتابیس (پارامتری بر اساس بخش)
# ------------------------------------------------------------
def get_db(dept):
    conn = sqlite3.connect(dept['db_path'])
    conn.row_factory = sqlite3.Row
    return conn


def db_exists(dept):
    return os.path.exists(dept['db_path'])


def get_period_test_count(conn, code, start, end):
    row = pd.read_sql(
        """SELECT COUNT(DISTINCT request_date) as days
           FROM daily_test_stats
           WHERE external_num = ? AND request_date BETWEEN ? AND ?""",
        conn, params=(code, start, end)
    )
    days = row['days'].iloc[0]
    return int(days) if days is not None else 0


def get_kit_rows(conn, dept, code, start, end):
    items_df = pd.read_sql(
        "SELECT item_no, gdesc, qty_per_kit, unit, current_stock FROM items WHERE external_num = ?",
        conn, params=(code,)
    )
    mov_df = pd.read_sql(
        """SELECT item_no, doc_date, doc_type, party_no, qty_in, qty_out FROM movements
           WHERE external_num = ? AND doc_date BETWEEN ? AND ? AND source = 'items_movements'
           ORDER BY doc_date DESC""",
        conn, params=(code, start, end)
    )

    consumption_ml = None
    count_based = False
    if dept['capacity_mode'] == 'volume_ml':
        count_based = is_count_based(dept, code)
        if not count_based:
            consumption_ml = get_consumption_ml_for_test(dept, code)

    kits = []
    for _, item in items_df.iterrows():
        item_movs = mov_df[mov_df['item_no'] == item['item_no']]
        qty_out = float(item_movs['qty_out'].sum())
        qty_in = float(item_movs['qty_in'].sum())
        packet_size = float(item['qty_per_kit'] or 0)

        if dept['capacity_mode'] == 'volume_ml' and not count_based:
            total_volume_ml = qty_out * packet_size
            if consumption_ml:
                capacity = total_volume_ml / consumption_ml
                capacity_unknown = False
            else:
                capacity = 0
                capacity_unknown = True
        else:
            capacity = qty_out * packet_size
            capacity_unknown = False

        movements = _movements_by_day(item_movs)

        kits.append({
            'item_no': item['item_no'],
            'kit_name': item['gdesc'],
            'unit': item['unit'],
            'packet_size': packet_size,
            'qty_in': qty_in,
            'qty_out': qty_out,
            'capacity': capacity,
            'capacity_unknown': capacity_unknown,
            'current_stock': float(item['current_stock'] or 0),
            'movements': movements,
        })

    return kits


def _movements_by_day(item_movs):
    if item_movs.empty:
        return []
    grouped = (
        item_movs.groupby('doc_date', as_index=False)
        .agg(qty_in=('qty_in', 'sum'), qty_out=('qty_out', 'sum'))
        .sort_values('doc_date', ascending=False)
    )
    return [
        {'date': r['doc_date'], 'qty_in': float(r['qty_in']), 'qty_out': float(r['qty_out'])}
        for _, r in grouped.iterrows()
    ]


def get_general_consumables(conn, known_codes, start, end):
    all_codes = pd.read_sql("SELECT DISTINCT external_num FROM items", conn)
    consumable_codes = [c for c in all_codes['external_num'].astype(str) if c not in known_codes]
    if not consumable_codes:
        return []

    placeholders = ','.join('?' * len(consumable_codes))
    items_df = pd.read_sql(
        f"SELECT external_num, item_no, gdesc, unit, current_stock FROM items WHERE external_num IN ({placeholders})",
        conn, params=consumable_codes
    )
    mov_df = pd.read_sql(
        f"""SELECT external_num, item_no, doc_date, qty_in, qty_out FROM movements
            WHERE external_num IN ({placeholders}) AND doc_date BETWEEN ? AND ? AND source = 'items_movements'""",
        conn, params=consumable_codes + [start, end]
    )

    rows = []
    for _, item in items_df.iterrows():
        item_movs = mov_df[(mov_df['external_num'] == item['external_num']) & (mov_df['item_no'] == item['item_no'])]
        qty_out = float(item_movs['qty_out'].sum())
        qty_in = float(item_movs['qty_in'].sum())
        rows.append({
            'code': item['external_num'],
            'item_no': item['item_no'],
            'name': item['gdesc'],
            'unit': item['unit'],
            'qty_in': qty_in,
            'qty_out': qty_out,
            'current_stock': float(item['current_stock'] or 0),
            'movements': _movements_by_day(item_movs),
        })

    rows.sort(key=lambda r: -r['qty_out'])
    return rows


def classify_device(dept, device_name, log_desc):
    device_name = device_name or ''
    log_desc = log_desc or ''
    if any(p in log_desc or p in device_name for p in EXCLUDE_DEVICE_PATTERNS):
        return None

    sorted_keys = sorted(dept['device_types'].keys(), key=len, reverse=True)
    for key in sorted_keys:
        if key.lower() in device_name.lower():
            return dept['device_types'][key]
    return 'سایر'


def get_period_devices(conn, dept, code, start, end):
    df = pd.read_sql(
        """SELECT device_name, log_desc, SUM(count) as count
           FROM daily_device_stats
           WHERE external_num = ? AND request_date BETWEEN ? AND ?
           GROUP BY device_name, log_desc""",
        conn, params=(code, start, end)
    )
    devices = []
    for _, row in df.iterrows():
        name = extract_device_name(row['log_desc']) or (row['device_name'] or 'Unknown')
        dtype = classify_device(dept, name, row['log_desc'])
        if dtype is None:
            continue
        devices.append({
            'device_name': name,
            'log_desc': row['log_desc'] or '',
            'count': int(row['count'] or 0),
            'device_type': dtype,
        })
    return devices


def get_partynos_rows(conn, code):
    try:
        df = pd.read_sql(
            """SELECT item_no, party_no, qty, expire_date_display, status_text, days_remaining
               FROM partynos
               WHERE external_num = ?
               ORDER BY days_remaining ASC""",
            conn, params=(code,)
        )
    except Exception:
        return []

    items_df = pd.read_sql(
        "SELECT item_no, gdesc FROM items WHERE external_num = ?", conn, params=(code,)
    )
    name_map = dict(zip(items_df['item_no'], items_df['gdesc']))

    rows = []
    for _, r in df.iterrows():
        days_remaining = r['days_remaining']
        rows.append({
            'item_no': r['item_no'],
            'kit_name': name_map.get(r['item_no'], r['item_no']),
            'party_no': r['party_no'],
            'qty': float(r['qty'] or 0),
            'expire_date': r['expire_date_display'] or '',
            'status_text': r['status_text'] or '',
            'days_remaining': int(days_remaining) if pd.notna(days_remaining) else None,
        })
    return rows


def get_total_tests_by_name(conn, dept, start, end):
    totals = {}
    for code, name in dept['test_names'].items():
        devices = get_period_devices(conn, dept, code, start, end)
        totals[name] = totals.get(name, 0) + sum(d['count'] for d in devices)
    return totals


# ------------------------------------------------------------
# فایل اکسل «علت تکرار تست‌ها» (تهیه‌شده توسط بخش فنی)
# ------------------------------------------------------------
def find_header_row(raw_df):
    for i in range(min(len(raw_df), 15)):
        values = [str(v).strip() for v in raw_df.iloc[i].tolist()]
        if 'تاریخ' in values and any('پذیرش' in v for v in values):
            return i
    return None


def _to_num(series):
    return pd.to_numeric(series.astype(str).str.strip(), errors='coerce')


def compute_qc_analysis(df):
    test_col = pick_col(df, 'تست')
    r1_col = pick_col(df, 'نتیجه اول')
    r2_col = pick_col(df, 'نتیجه دوم')
    cv_col = pick_col(df, 'CV')
    tcv3_col = pick_col(df, '3cv') or pick_col(df, '3CV')
    dev1_col = pick_col(df, 'دستگاه1') or pick_col(df, 'دستگاه 1')
    dev2_col = pick_col(df, 'دستگاه2') or pick_col(df, 'دستگاه 2')
    reason_col = pick_col(df, 'علت تکرار')
    expert_col = pick_col(df, 'کارشناس')
    tech_col = pick_col(df, 'مسئول فنی')

    if not (test_col and r1_col and r2_col and cv_col and tcv3_col):
        return None

    d = pd.DataFrame({
        'test': df[test_col].astype(str).str.strip(),
        'r1': _to_num(df[r1_col]),
        'r2': _to_num(df[r2_col]),
        'cv': _to_num(df[cv_col]),
        'tcv3': _to_num(df[tcv3_col]),
        'dev1': df[dev1_col].astype(str).str.strip() if dev1_col else '',
        'dev2': df[dev2_col].astype(str).str.strip() if dev2_col else '',
        'reason': df[reason_col].astype(str).str.strip() if reason_col else '',
        'expert': df[expert_col].astype(str).str.strip() if expert_col else '',
        'tech': df[tech_col].astype(str).str.strip() if tech_col else '',
    })

    d['diff'] = (d['r2'] - d['r1']).abs()
    d['diff_percent'] = (d['diff'] / d['r1'].replace(0, pd.NA)).abs() * 100
    d['has_cv'] = d['cv'].notna() & (d['cv'] > 0) & d['tcv3'].notna() & d['r1'].notna() & d['r2'].notna()
    d['significant'] = d['has_cv'] & (d['diff'] > d['tcv3'])

    with_cv = d[d['has_cv']]
    total, total_with_cv = len(d), len(with_cv)

    overall = {
        'total_rows': total,
        'rows_with_cv': total_with_cv,
        'coverage_percent': round(total_with_cv / total * 100, 1) if total else 0,
        'significant_count': int(with_cv['significant'].sum()) if total_with_cv else 0,
        'non_significant_count': int((~with_cv['significant']).sum()) if total_with_cv else 0,
        'significant_percent': round(with_cv['significant'].mean() * 100, 1) if total_with_cv else None,
        'non_significant_percent': round((1 - with_cv['significant'].mean()) * 100, 1) if total_with_cv else None,
    }

    def person_report(key):
        rows = []
        for name, g in with_cv[with_cv[key] != ''].groupby(key):
            n = len(g)
            if n < 3:
                continue
            sig = int(g['significant'].sum())
            rows.append({
                'name': name,
                'total_repeats_with_cv': n,
                'significant': sig,
                'non_significant': n - sig,
                'non_significant_percent': round((n - sig) / n * 100, 1),
            })
        rows.sort(key=lambda r: -r['non_significant_percent'])
        return rows

    by_tech = person_report('tech') if tech_col else []
    by_expert = person_report('expert') if expert_col else []

    by_test = []
    for name, g in d[d['test'] != ''].groupby('test'):
        gcv = g[g['has_cv']]
        by_test.append({
            'test': name,
            'total_repeats': len(g),
            'avg_diff_percent': round(g['diff_percent'].dropna().mean(), 1) if g['diff_percent'].notna().any() else None,
            'with_cv': len(gcv),
            'significant_percent': round(gcv['significant'].mean() * 100, 1) if len(gcv) else None,
        })
    by_test.sort(key=lambda r: (r['significant_percent'] is None, -(r['significant_percent'] or 0)))

    by_device_pair = []
    if dev1_col and dev2_col:
        mask = (d['dev1'] != '') & (d['dev2'] != '')
        for (test, dev1, dev2), g in d[mask].groupby(['test', 'dev1', 'dev2']):
            if len(g) < 3 or not g['diff_percent'].notna().any():
                continue
            by_device_pair.append({
                'test': test,
                'device1': dev1,
                'device2': dev2,
                'count': len(g),
                'avg_diff_percent': round(g['diff_percent'].dropna().mean(), 1),
            })
        by_device_pair.sort(key=lambda r: r['avg_diff_percent'])

    reason_outcome = []
    if reason_col:
        for name, g in with_cv[with_cv['reason'] != ''].groupby('reason'):
            n = len(g)
            sig = int(g['significant'].sum())
            reason_outcome.append({
                'reason_code': name,
                'count': n,
                'non_significant_percent': round((n - sig) / n * 100, 1),
            })
        reason_outcome.sort(key=lambda r: -r['count'])

    result = {
        'overall': overall,
        'by_tech': by_tech,
        'by_expert': by_expert,
        'by_test': by_test,
        'by_device_pair_best': by_device_pair[:20],
        'by_device_pair_worst': by_device_pair[-10:][::-1] if len(by_device_pair) > 10 else [],
        'reason_outcome': reason_outcome,
        'method_note': (
            'معیار «معنادار بودن تفاوت»: اختلاف نتیجه‌ی دوم و اول بیشتر از ۳CV '
            '(سه‌برابر نوسان مورد انتظار روش، بر پایه‌ی CV% ثبت‌شده در همان ردیف) باشد. '
            'فقط ردیف‌هایی که مقدار CV برایشان ثبت شده، قابل قضاوت آماری‌اند.'
        ),
    }
    result['narrative'] = build_qc_narrative(result)
    return result


def build_qc_narrative(qc):
    lines = []
    ov = qc['overall']

    if ov['total_rows']:
        if ov['coverage_percent'] < 100:
            lines.append(
                f"از مجموع {ov['total_rows']} تکرار ثبت‌شده، فقط {ov['rows_with_cv']} مورد "
                f"({ov['coverage_percent']}٪) مقدار CV٪ تعریف‌شده دارند و قابل قضاوت آماری‌اند؛ "
                f"برای بقیه‌ی تست‌ها CV ثبت نشده و نمی‌توان گفت تکرارشان موجه بوده یا نه."
            )
        if ov['significant_percent'] is not None:
            lines.append(
                f"از میان تکرارهای قابل‌بررسی، {ov['significant_percent']}٪ تفاوت واقعی و معنادار "
                f"داشته‌اند (تکرار موجه بوده) و {ov['non_significant_percent']}٪ در محدوده‌ی نوسان "
                f"طبیعی همان روش قرار داشته‌اند (تکرار عملاً چیزی را تغییر نداده)."
            )

    tech_ranked = [r for r in qc['by_tech'] if r['total_repeats_with_cv'] >= 5]
    if len(tech_ranked) >= 2:
        worst, best = tech_ranked[0], tech_ranked[-1]
        if worst['name'] != best['name']:
            lines.append(
                f"بین مسئولان فنی، «{worst['name']}» بیشترین نرخ تکرار بی‌اثر را دارد "
                f"({worst['non_significant_percent']}٪ از {worst['total_repeats_with_cv']} تکرار)، "
                f"در مقابل «{best['name']}» با کمترین نرخ ({best['non_significant_percent']}٪ از "
                f"{best['total_repeats_with_cv']} تکرار) که نشان می‌دهد تکرارهایش بیشتر بر پایه‌ی "
                f"تفاوت واقعی بوده، نه احتیاط بیش‌ازحد."
            )

    reason_candidates = [r for r in qc['reason_outcome'] if r['count'] >= 5]
    if len(reason_candidates) >= 2:
        worst_r = max(reason_candidates, key=lambda r: r['non_significant_percent'])
        best_r = min(reason_candidates, key=lambda r: r['non_significant_percent'])
        if worst_r['reason_code'] != best_r['reason_code']:
            lines.append(
                f"از نظر علت تکرار، کد «{worst_r['reason_code']}» بیشترین نرخ بی‌اثری را دارد "
                f"({worst_r['non_significant_percent']}٪ از {worst_r['count']} مورد)، در حالی‌که "
                f"کد «{best_r['reason_code']}» تقریباً همیشه به یک تفاوت واقعی رسیده "
                f"({best_r['non_significant_percent']}٪ بی‌اثر از {best_r['count']} مورد) — یعنی "
                f"تکرار به این علت معمولاً موجه بوده است."
            )

    test_ranked = [r for r in qc['by_test'] if r['significant_percent'] is not None and r['with_cv'] >= 5]
    if test_ranked:
        top_test = test_ranked[0]
        lines.append(
            f"تست «{top_test['test']}» بیشترین نرخ تفاوت معنادار را در بین تکرارها دارد "
            f"({top_test['significant_percent']}٪ از {top_test['with_cv']} مورد)، که می‌تواند نشانه‌ی "
            f"نوسان بالای روش یا کیفیت کیت آن تست باشد و ارزش پیگیری دارد."
        )

    if qc['by_device_pair_worst']:
        wp = qc['by_device_pair_worst'][0]
        lines.append(
            f"بیشترین ناهماهنگی بین دو دستگاه برای تست «{wp['test']}» بین {wp['device1']} و "
            f"{wp['device2']} دیده می‌شود (اختلاف نسبی میانگین {wp['avg_diff_percent']}٪ در "
            f"{wp['count']} مورد) — پیشنهاد می‌شود هم‌ارزی/کالیبراسیون این دو دستگاه برای این تست "
            f"بررسی شود."
        )

    if qc['by_device_pair_best']:
        cross_device_best = next(
            (r for r in qc['by_device_pair_best'] if r['device1'] != r['device2']),
            qc['by_device_pair_best'][0],
        )
        bp = cross_device_best
        lines.append(
            f"در مقابل، برای تست «{bp['test']}» بین {bp['device1']} و {bp['device2']} کمترین اختلاف "
            f"دیده می‌شود ({bp['avg_diff_percent']}٪ در {bp['count']} مورد)، یعنی این دو دستگاه برای "
            f"این تست هماهنگی خوبی دارند."
        )

    return lines


def normalize_name(text):
    if pd.isna(text):
        return text
    text = str(text)
    text = text.replace('ي', 'ی').replace('ك', 'ک')
    text = text.replace('\u200c', ' ')
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(دکتر)(?=\S)', r'\1 ', text)
    return text


def parse_repeats_excel(file_storage):
    xls = pd.ExcelFile(file_storage, engine='openpyxl')
    frames = []
    for sheet_name in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet_name, header=None)
        header_idx = find_header_row(raw)
        if header_idx is None:
            continue

        header = [str(v).strip() if pd.notna(v) else f'ستون{i}' for i, v in enumerate(raw.iloc[header_idx].tolist())]
        sheet_df = raw.iloc[header_idx + 1:].copy()
        sheet_df.columns = header
        sheet_df = sheet_df.dropna(axis=1, how='all')

        date_col_here = pick_col(sheet_df, 'تاریخ')
        accept_col_here = pick_col(sheet_df, 'شماره پذیرش')
        if date_col_here and accept_col_here:
            has_date = sheet_df[date_col_here].notna() & (sheet_df[date_col_here].astype(str).str.strip() != '')
            has_accept = sheet_df[accept_col_here].notna() & (sheet_df[accept_col_here].astype(str).str.strip() != '')
            sheet_df = sheet_df[has_date & has_accept]
        elif date_col_here:
            sheet_df = sheet_df[sheet_df[date_col_here].notna() & (sheet_df[date_col_here].astype(str).str.strip() != '')]
        else:
            sheet_df = sheet_df.dropna(how='all')

        sheet_df['_sheet'] = sheet_name
        frames.append(sheet_df)

    if not frames:
        raise ValueError('ستون‌های «تاریخ» و «شماره پذیرش» در هیچ‌کدام از شیت‌های فایل پیدا نشد. لطفاً فرمت فایل را بررسی کنید.')

    df = pd.concat(frames, ignore_index=True, sort=False)
    df = df.fillna('')

    for cand in ('کارشناس', 'مسئول فنی'):
        col = pick_col(df, cand)
        if col:
            df[col] = df[col].apply(normalize_name)

    return df


def pick_col(df, *candidates):
    for cand in candidates:
        for c in df.columns:
            if str(c).strip() == cand:
                return c
    return None


def normalize_jalali_date(value):
    s = str(value).strip().replace('/', '-')
    parts = s.split('-')
    if len(parts) == 3:
        try:
            y, m, d = (int(p) for p in parts)
            return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            return s
    return s


def build_basic_narrative(summary):
    lines = []
    total = summary.get('total_rows', 0)
    if not total:
        return lines

    by_test = summary.get('by_test') or []
    if by_test:
        top = by_test[0]
        share = round(top['count'] / total * 100, 1)
        lines.append(
            f"در مجموع {total} تکرار ثبت شده که بیشترین سهم مربوط به تست «{top['label']}» است "
            f"با {top['count']} مورد ({share}٪ از کل تکرارها)."
        )

    by_reason = summary.get('by_reason') or []
    if by_reason:
        top = by_reason[0]
        share = round(top['count'] / total * 100, 1)
        lines.append(
            f"شایع‌ترین علت تکرار، کد «{top['label']}» است با {top['count']} مورد ({share}٪ از کل)."
        )

    by_device = summary.get('by_device') or []
    if by_device:
        top = by_device[0]
        share = round(top['count'] / total * 100, 1)
        lines.append(
            f"دستگاه «{top['label']}» بیشترین سهم را در نتیجه‌ی نهاییِ تکرارها دارد "
            f"({top['count']} مورد، {share}٪ از کل)."
        )

    by_tech = summary.get('by_tech') or []
    if len(by_tech) >= 2:
        top = by_tech[0]
        lines.append(
            f"از نظر تعداد خام، بیشترین تکرارها زیر نظر «{top['label']}» ثبت شده "
            f"({top['count']} مورد، {top['share_percent']}٪ از کل تکرارها) — این فقط سهم از کل "
            f"تکرارهاست، نه لزوماً نشانه‌ی ضعف؛ برای قضاوت واقعی به بخش «تحلیل کیفی» زیر نگاه کنید."
        )

    return lines


def load_repeats_data(dept, start=None, end=None):
    path = dept['repeats_cache_path']
    if not os.path.exists(path):
        return None

    print(f"[load_repeats_data] dept={dept['label']!r} <- reading from {os.path.abspath(path)}")
    with open(path, 'rb') as f:
        df = parse_repeats_excel(f)
    df = df.astype(str)
    date_col = pick_col(df, 'تاریخ')

    date_filter_note = None
    available_date_range = None
    if date_col:
        norm_all = df[date_col].apply(normalize_jalali_date)
        non_empty = norm_all[norm_all.str.strip() != '']
        if not non_empty.empty:
            available_date_range = {'min': non_empty.min(), 'max': non_empty.max()}

        if start and end:
            mask = (norm_all >= start) & (norm_all <= end)
            filtered = df[mask]
            if filtered.empty and not df.empty:
                date_filter_note = 'requested_range_had_no_data'
            else:
                df = filtered

    if date_col:
        df = df.sort_values(by=date_col, ascending=False)

    rows = df.to_dict(orient='records')
    columns = list(df.columns)

    test_col = pick_col(df, 'تست')
    reason_col = pick_col(df, 'علت تکرار')
    device_col = pick_col(df, 'دستگاه نهایی') or pick_col(df, 'دستگاه2') or pick_col(df, 'دستگاه 2')
    expert_col = pick_col(df, 'کارشناس')
    tech_col = pick_col(df, 'مسئول فنی')

    def count_by(col, normalize=False):
        if not col:
            return []
        series = df[col].astype(str).str.strip()
        if normalize:
            series = series.apply(normalize_name)
        counts = series[series != ''].value_counts()
        return [{'label': str(k), 'count': int(v)} for k, v in counts.items()]

    by_test = count_by(test_col)
    total_repeats = sum(r['count'] for r in by_test) or len(df)

    summary = {
        'total_rows': len(df),
        'columns': columns,
        'by_test': by_test,
        'by_reason': count_by(reason_col),
        'by_device': count_by(device_col),
        'by_expert': [
            {**row, 'share_percent': round(row['count'] / total_repeats * 100, 1)}
            for row in count_by(expert_col, normalize=True)
        ],
        'by_tech': [
            {**row, 'share_percent': round(row['count'] / total_repeats * 100, 1)}
            for row in count_by(tech_col, normalize=True)
        ],
    }

    summary['available_date_range'] = available_date_range
    summary['date_filter_note'] = date_filter_note
    summary['qc_analysis'] = compute_qc_analysis(df)
    summary['narrative'] = build_basic_narrative(summary) + (summary['qc_analysis']['narrative'] if summary['qc_analysis'] else [])

    return {'rows': rows, 'columns': columns, 'summary': summary}


def build_insights(results, top_n=5):
    all_kits_flat = []
    for r in results:
        for k in r['kits']:
            if k['qty_out'] > 0:
                all_kits_flat.append({
                    'test_name': r['name'],
                    'kit_name': k['kit_name'],
                    'qty_out': k['qty_out'],
                })
    top_kits_by_output = sorted(all_kits_flat, key=lambda x: -x['qty_out'])[:top_n]

    tests_with_count = [r for r in results if r['total_tests'] > 0]
    top_tests_by_count = [
        {'name': r['name'], 'total_tests': r['total_tests']}
        for r in sorted(tests_with_count, key=lambda r: -r['total_tests'])[:top_n]
    ]

    tests_with_waste = [r for r in results if r['waste'] is not None and r['waste'] > 0]
    top_tests_by_waste = [
        {'name': r['name'], 'waste': r['waste'], 'waste_percent': r['waste_percent']}
        for r in sorted(tests_with_waste, key=lambda r: -r['waste'])[:top_n]
    ]

    tests_with_waste_pct = [r for r in results if r['waste_percent'] is not None and r['waste_percent'] > 0]
    top_tests_by_waste_percent = [
        {'name': r['name'], 'waste_percent': r['waste_percent'], 'waste': r['waste']}
        for r in sorted(tests_with_waste_pct, key=lambda r: -r['waste_percent'])[:top_n]
    ]

    known_rows = [r for r in results if not r['capacity_unknown'] and r['capacity'] > 0]
    total_capacity_known = sum(r['capacity'] for r in known_rows)
    total_tests_known = sum(r['total_tests'] for r in known_rows)
    overall_efficiency_percent = (
        round((total_tests_known / total_capacity_known) * 100, 1) if total_capacity_known > 0 else None
    )

    return {
        'top_kits_by_output': top_kits_by_output,
        'top_tests_by_count': top_tests_by_count,
        'top_tests_by_waste': top_tests_by_waste,
        'top_tests_by_waste_percent': top_tests_by_waste_percent,
        'overall_efficiency_percent': overall_efficiency_percent,
    }


def build_report(dept, start, end):
    conn = get_db(dept)
    try:
        results = []
        device_groups_conf = dept.get('device_groups', {})
        test_count_source = dept.get('test_count_source', {})
        shared_capacity_groups = dept.get('shared_capacity_groups', [])
        code_to_shared_group = {}
        for grp in shared_capacity_groups:
            for c in grp['codes']:
                code_to_shared_group[c] = grp
        processed_shared_groups = set()

        for code, name in dept['test_names'].items():
            stats_code = test_count_source.get(code, code)
            days_covered = get_period_test_count(conn, stats_code, start, end)
            kits = get_kit_rows(conn, dept, code, start, end)
            partynos = get_partynos_rows(conn, code)

            shared_group = code_to_shared_group.get(code)
            if shared_group:
                group_id = id(shared_group)
                if group_id in processed_shared_groups:
                    continue
                processed_shared_groups.add(group_id)

                member_codes = shared_group['codes']
                pooled_kits = []
                member_data = {}
                for mcode in member_codes:
                    mname = dept['test_names'].get(mcode, mcode)
                    m_stats_code = test_count_source.get(mcode, mcode)
                    m_devices = get_period_devices(conn, dept, m_stats_code, start, end)
                    m_total_tests = sum(d['count'] for d in m_devices)
                    m_tests_by_device_type = {}
                    for d in m_devices:
                        m_tests_by_device_type[d['device_type']] = (
                            m_tests_by_device_type.get(d['device_type'], 0) + d['count']
                        )
                    m_kits = kits if mcode == code else get_kit_rows(conn, dept, mcode, start, end)
                    m_days_covered = get_period_test_count(conn, m_stats_code, start, end)
                    m_partynos = partynos if mcode == code else get_partynos_rows(conn, mcode)
                    pooled_kits.extend(m_kits)
                    member_data[mcode] = {
                        'name': mname, 'devices': m_devices, 'total_tests': m_total_tests,
                        'tests_by_device_type': m_tests_by_device_type, 'kits': m_kits,
                        'days_covered': m_days_covered, 'partynos': m_partynos,
                    }

                pooled_capacity = sum(k['capacity'] for k in pooled_kits)
                pooled_capacity_unknown = any(k['capacity_unknown'] for k in pooled_kits)
                total_group_tests = sum(m['total_tests'] for m in member_data.values())

                pooled_movements_by_date = {}
                for k in pooled_kits:
                    for m in k['movements']:
                        entry = pooled_movements_by_date.setdefault(m['date'], {'qty_in': 0.0, 'qty_out': 0.0})
                        entry['qty_in'] += m['qty_in']
                        entry['qty_out'] += m['qty_out']
                pooled_movements = [
                    {'date': d, 'qty_in': v['qty_in'], 'qty_out': v['qty_out']}
                    for d, v in sorted(pooled_movements_by_date.items(), reverse=True)
                ]
                pooled_qty_in = sum(k['qty_in'] for k in pooled_kits)
                pooled_qty_out = sum(k['qty_out'] for k in pooled_kits)
                pooled_current_stock = sum(k['current_stock'] for k in pooled_kits)
                pooled_partynos = []
                for m in member_data.values():
                    pooled_partynos.extend(m['partynos'])

                other_names = {mc: member_data[mc]['name'] for mc in member_codes}

                for mcode in member_codes:
                    m = member_data[mcode]
                    if total_group_tests > 0:
                        alloc_capacity = pooled_capacity * (m['total_tests'] / total_group_tests)
                    else:
                        alloc_capacity = pooled_capacity / len(member_codes)
                    waste, waste_percent = _waste_stats(alloc_capacity, m['total_tests'], pooled_capacity_unknown)

                    others_label = '، '.join(v for k, v in other_names.items() if k != mcode)
                    results.append({
                        'code': mcode,
                        'name': f"{m['name']} (کیت مشترک با {others_label})",
                        'total_tests': m['total_tests'],
                        'tests_by_device_type': m['tests_by_device_type'],
                        'days_covered': m['days_covered'],
                        'devices': m['devices'],
                        'qty_in': pooled_qty_in,
                        'qty_out': pooled_qty_out,
                        'capacity': alloc_capacity,
                        'capacity_unknown': pooled_capacity_unknown,
                        'waste': waste,
                        'waste_percent': waste_percent,
                        'current_stock': pooled_current_stock,
                        'kits': pooled_kits,
                        'movements': pooled_movements,
                        'partynos': pooled_partynos,
                        'near_expiry_count': sum(
                            1 for p in pooled_partynos
                            if p['days_remaining'] is not None and p['days_remaining'] <= NEAR_EXPIRY_DAYS and p['qty'] > 0
                        ),
                        'shared_kit_group': shared_group['group_name'],
                    })
                continue

            groups_conf = device_groups_conf.get(code)

            if groups_conf:
                for g in groups_conf:
                    source_code = g.get('count_source_code', code)
                    source_devices = get_period_devices(conn, dept, source_code, start, end)
                    group_devices = [d for d in source_devices if _device_matches_group(d['device_name'], g)]
                    total_tests = sum(d['count'] for d in group_devices)

                    tests_by_device_type = {}
                    for d in group_devices:
                        tests_by_device_type[d['device_type']] = (
                            tests_by_device_type.get(d['device_type'], 0) + d['count']
                        )

                    group_kits_raw = [k for k in kits if _item_matches_group(k['kit_name'], g)]
                    group_item_nos = {k['item_no'] for k in group_kits_raw}
                    group_partynos = [p for p in partynos if p['item_no'] in group_item_nos]

                    group_kits = []
                    for k in group_kits_raw:
                        kc = dict(k)
                        kc['waste'], kc['waste_percent'] = _waste_stats(
                            kc['capacity'], total_tests, kc['capacity_unknown']
                        )
                        group_kits.append(kc)

                    row_capacity = sum(k['capacity'] for k in group_kits)
                    row_capacity_unknown = any(k['capacity_unknown'] for k in group_kits)
                    row_waste = sum(k['waste'] for k in group_kits if k['waste'] is not None) if not row_capacity_unknown else None
                    row_waste_percent = (
                        round((row_waste / row_capacity) * 100, 1) if (row_waste is not None and row_capacity > 0) else None
                    )

                    movements_by_date = {}
                    for k in group_kits:
                        for m in k['movements']:
                            entry = movements_by_date.setdefault(m['date'], {'qty_in': 0.0, 'qty_out': 0.0})
                            entry['qty_in'] += m['qty_in']
                            entry['qty_out'] += m['qty_out']
                    movements = [
                        {'date': d, 'qty_in': v['qty_in'], 'qty_out': v['qty_out']}
                        for d, v in sorted(movements_by_date.items(), reverse=True)
                    ]

                    results.append({
                        'code': code,
                        'name': f"{name} — {g['group_name']}",
                        'total_tests': total_tests,
                        'tests_by_device_type': tests_by_device_type,
                        'days_covered': days_covered,
                        'devices': group_devices,
                        'qty_in': sum(k['qty_in'] for k in group_kits),
                        'qty_out': sum(k['qty_out'] for k in group_kits),
                        'capacity': row_capacity,
                        'capacity_unknown': row_capacity_unknown,
                        'waste': row_waste,
                        'waste_percent': row_waste_percent,
                        'current_stock': sum(k['current_stock'] for k in group_kits),
                        'kits': group_kits,
                        'movements': movements,
                        'partynos': group_partynos,
                        'near_expiry_count': sum(
                            1 for p in group_partynos
                            if p['days_remaining'] is not None and p['days_remaining'] <= NEAR_EXPIRY_DAYS and p['qty'] > 0
                        ),
                    })
                continue

            devices = get_period_devices(conn, dept, stats_code, start, end)
            total_tests = sum(d['count'] for d in devices)

            tests_by_device_type = {}
            for d in devices:
                tests_by_device_type[d['device_type']] = (
                    tests_by_device_type.get(d['device_type'], 0) + d['count']
                )

            movements_by_date = {}
            for k in kits:
                for m in k['movements']:
                    entry = movements_by_date.setdefault(m['date'], {'qty_in': 0.0, 'qty_out': 0.0})
                    entry['qty_in'] += m['qty_in']
                    entry['qty_out'] += m['qty_out']
            movements = [
                {'date': d, 'qty_in': v['qty_in'], 'qty_out': v['qty_out']}
                for d, v in sorted(movements_by_date.items(), reverse=True)
            ]

            capacity = sum(k['capacity'] for k in kits)
            capacity_unknown = any(k['capacity_unknown'] for k in kits)
            waste, waste_percent = _waste_stats(capacity, total_tests, capacity_unknown)

            results.append({
                'code': code,
                'name': name,
                'total_tests': total_tests,
                'tests_by_device_type': tests_by_device_type,
                'days_covered': days_covered,
                'devices': devices,
                'qty_in': sum(k['qty_in'] for k in kits),
                'qty_out': sum(k['qty_out'] for k in kits),
                'capacity': capacity,
                'capacity_unknown': capacity_unknown,
                'waste': waste,
                'waste_percent': waste_percent,
                'current_stock': sum(k['current_stock'] for k in kits),
                'kits': kits,
                'movements': movements,
                'partynos': partynos,
                'near_expiry_count': sum(
                    1 for p in partynos
                    if p['days_remaining'] is not None and p['days_remaining'] <= NEAR_EXPIRY_DAYS and p['qty'] > 0
                ),
            })

        total_tests_all = sum(r['total_tests'] for r in results)

        device_type_totals = {}
        for r in results:
            for dtype, cnt in r['tests_by_device_type'].items():
                device_type_totals[dtype] = device_type_totals.get(dtype, 0) + cnt

        consumables = get_general_consumables(conn, set(dept['test_names'].keys()), start, end)

        summary = {
            'total_tests': total_tests_all,
            'tests_by_device_type': device_type_totals,
            'total_qty_out': sum(r['qty_out'] for r in results),
            'total_capacity': sum(r['capacity'] for r in results),
            'total_waste': sum(r['waste'] for r in results if r['waste'] is not None),
            'total_current_stock': sum(r['current_stock'] for r in results),
            'total_near_expiry': sum(r['near_expiry_count'] for r in results),
            'total_consumables_qty_out': sum(c['qty_out'] for c in consumables),
        }

        missing_days = [r['name'] for r in results if r['days_covered'] == 0]
        missing_consumption = [r['name'] for r in results if r['capacity_unknown']]

        insights = build_insights(results)

        return {
            'tests': results,
            'consumables': consumables,
            'summary': summary,
            'missing_days': missing_days,
            'missing_consumption': missing_consumption,
            'insights': insights,
        }
    finally:
        conn.close()


# ------------------------------------------------------------
# مسیرهای وب
# ------------------------------------------------------------
@app.route('/')
def index():
    today = jdatetime.date.today()
    departments = [{'key': k, 'label': v['label']} for k, v in DEPARTMENTS.items()]
    return render_template(
        'index.html',
        today=today.strftime('%Y-%m-%d'),
        departments=departments,
        default_department=DEFAULT_DEPARTMENT,
    )


@app.route('/api/departments')
def api_departments():
    return jsonify({
        'departments': [{'key': k, 'label': v['label']} for k, v in DEPARTMENTS.items()],
        'default': DEFAULT_DEPARTMENT,
    })


@app.route('/api/today')
def api_today():
    today = jdatetime.date.today()
    return jsonify({'today': today.strftime('%Y-%m-%d')})


@app.route('/api/preset')
def api_preset():
    kind = request.args.get('range', 'this_month')
    today = jdatetime.date.today()
    yesterday = today - jdatetime.timedelta(days=1)

    if kind == 'this_month':
        start = jdatetime.date(today.year, today.month, 1)
    elif kind == 'last_3_months':
        m = today.month - 3
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        start = jdatetime.date(y, m, 1)
    elif kind == 'this_year':
        start = jdatetime.date(today.year, 1, 1)
    else:
        start = jdatetime.date(today.year, today.month, 1)

    return jsonify({'start': start.strftime('%Y-%m-%d'), 'end': yesterday.strftime('%Y-%m-%d')})


def resolve_department_or_error():
    key = request.args.get('dept', DEFAULT_DEPARTMENT)
    try:
        return get_department(key), None
    except ValueError as e:
        return None, (jsonify({'error': str(e)}), 400)


@app.route('/api/report')
def api_report():
    dept, err = resolve_department_or_error()
    if err:
        return err

    start = request.args.get('start')
    end = request.args.get('end')

    if not db_exists(dept):
        return jsonify({'error': f"فایل دیتابیس یافت نشد: {dept['db_path']}"}), 500

    if not start or not end:
        return jsonify({'error': 'تاریخ شروع و پایان الزامی است'}), 400

    try:
        for d in (start, end):
            y, m, day = map(int, d.split('-'))
            jdatetime.date(y, m, day)
    except Exception:
        return jsonify({'error': 'فرمت تاریخ نامعتبر است (مثال درست: 1405-01-01)'}), 400

    if start > end:
        return jsonify({'error': 'تاریخ شروع نمی‌تواند بعد از تاریخ پایان باشد'}), 400

    try:
        data = build_report(dept, start, end)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return jsonify({
            'error': f'خطای داخلی هنگام پردازش: {e}',
            'traceback': tb,
        }), 500

    return jsonify(data)


@app.route('/api/upload-repeats', methods=['POST'])
def api_upload_repeats():
    dept, err = resolve_department_or_error()
    if err:
        return err

    if 'file' not in request.files:
        return jsonify({'error': 'فایلی ارسال نشد'}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'فایلی انتخاب نشده است'}), 400

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'فقط فایل اکسل (.xlsx یا .xls) پذیرفته می‌شود'}), 400

    try:
        file.stream.seek(0)
        df = parse_repeats_excel(file)
        if df.empty:
            return jsonify({'error': 'فایل خوانده شد ولی هیچ ردیف داده‌ای در آن یافت نشد'}), 400

        file.stream.seek(0)
        file.save(dept['repeats_cache_path'])
        print(f"[upload-repeats] dept={dept['label']!r} -> saved to {os.path.abspath(dept['repeats_cache_path'])} ({len(df)} rows)")
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return jsonify({'error': f'خطا در خواندن فایل اکسل: {e}', 'traceback': tb}), 400

    return jsonify({'success': True, 'rows': len(df), 'columns': list(df.columns), 'saved_to': dept['repeats_cache_path']})


@app.route('/api/repeats')
def api_repeats():
    dept, err = resolve_department_or_error()
    if err:
        return err

    start = request.args.get('start')
    end = request.args.get('end')

    try:
        data = load_repeats_data(dept, start, end)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return jsonify({'error': f'خطا در خواندن فایل تکرارهای ذخیره‌شده: {e}', 'traceback': tb}), 500

    if data is None:
        return jsonify({'available': False})

    if start and end and db_exists(dept):
        try:
            conn = get_db(dept)
            try:
                totals = get_total_tests_by_name(conn, dept, start, end)
            finally:
                conn.close()

            totals_norm = {str(k).strip().upper(): v for k, v in totals.items()}
            enriched = []
            for row in data['summary']['by_test']:
                key = row['label'].strip().upper()
                total = totals_norm.get(key)
                if total:
                    row = {
                        **row,
                        'total_tests': total,
                        'repeat_rate_percent': round(row['count'] / total * 100, 2),
                    }
                else:
                    row = {**row, 'total_tests': None, 'repeat_rate_percent': None}
                enriched.append(row)
            enriched.sort(key=lambda r: (r['repeat_rate_percent'] is None, -(r['repeat_rate_percent'] or 0)))
            data['summary']['by_test'] = enriched
            data['summary']['repeat_rate_period'] = {'start': start, 'end': end}
        except Exception:
            print(traceback.format_exc())

    data['available'] = True
    return jsonify(data)


# ------------------------------------------------------------
# تب ۴ — دستیار هوشمند (RAG روی داده‌ی همین گزارش)
# ------------------------------------------------------------

def fmt_num(n):
    """قالب‌بندی ساده‌ی عدد برای متن اسناد (نه برای جدول‌ها)."""
    if n is None:
        return 'نامشخص'
    try:
        return f"{round(float(n)):,}"
    except (TypeError, ValueError):
        return str(n)


def normalize_fa(text):
    """یکسان‌سازی سبک متن فارسی + حذف stop-word، فقط برای بازیابی (نه نمایش)."""
    text = str(text or '')
    text = text.replace('ي', 'ی').replace('ك', 'ک').replace('\u200c', ' ')
    text = text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789'))
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    tokens = [t for t in text.split() if t not in FA_STOPWORDS and len(t) > 1]
    return ' '.join(tokens)


def build_chat_documents(dept, data, repeats, start=None, end=None):
    """گزارش جاری را به اسناد کوتاه و خودکفا تبدیل می‌کند — هر سند باید
    به‌تنهایی برای پاسخ به یک سؤال محتمل کافی باشد."""
    docs = []
    summary = data.get('summary', {}) or {}
    insights = data.get('insights', {}) or {}

    if start and end:
        docs.append({
            'tag': 'بازه گزارش',
            'text': f"این گزارش مربوط به بخش «{dept['label']}» و بازه‌ی {start} تا {end} (شمسی) است.",
        })

    docs.append({
        'tag': 'خلاصه بخش',
        'text': (
            f"خلاصه‌ی بخش {dept['label']} در بازه‌ی انتخابی: {fmt_num(summary.get('total_tests'))} تست انجام شد، "
            f"{fmt_num(summary.get('total_qty_out'))} کیت خروجی داده شد، ظرفیت کل کیت‌های مصرف‌شده "
            f"{fmt_num(summary.get('total_capacity'))} تست بود، مجموع ضایعات {fmt_num(summary.get('total_waste'))} عدد، "
            f"موجودی فعلی کل {fmt_num(summary.get('total_current_stock'))} و {fmt_num(summary.get('total_near_expiry'))} "
            f"پارتی نزدیک به انقضا (کمتر از {NEAR_EXPIRY_DAYS} روز) دارد."
        ),
    })

    if insights.get('overall_efficiency_percent') is not None:
        docs.append({
            'tag': 'بهره‌وری کلی',
            'text': (
                f"بهره‌وری کلی بخش {dept['label']} در این بازه {insights['overall_efficiency_percent']}٪ است "
                f"(نسبت تعداد تست انجام‌شده به ظرفیت کیت‌های مصرفی، فقط برای تست‌هایی که ظرفیت‌شان مشخص است)."
            ),
        })

    for row in (insights.get('top_tests_by_waste') or []):
        docs.append({
            'tag': f"ضایعات — {row['name']}",
            'text': f"تست {row['name']} در این بازه {fmt_num(row['waste'])} عدد ضایعات دارد، معادل {row.get('waste_percent')}٪ از ظرفیت کیت‌های مصرفی آن.",
        })
    for row in (insights.get('top_tests_by_waste_percent') or []):
        docs.append({
            'tag': f"درصد ضایعات — {row['name']}",
            'text': f"تست {row['name']} از نظر درصد ضایعات در رتبه‌ی بالایی است: {row.get('waste_percent')}٪ (معادل {fmt_num(row['waste'])} عدد).",
        })
    for row in (insights.get('top_tests_by_count') or []):
        docs.append({
            'tag': f"پرتست‌ترین — {row['name']}",
            'text': f"تست {row['name']} با {fmt_num(row['total_tests'])} بار انجام‌شده، یکی از پرتکرارترین آزمایش‌های این بازه است.",
        })
    for row in (insights.get('top_kits_by_output') or []):
        docs.append({
            'tag': f"خروجی کیت — {row['kit_name']}",
            'text': f"کیت «{row['kit_name']}» (برای تست {row['test_name']}) با {fmt_num(row['qty_out'])} عدد خروجی، یکی از پرمصرف‌ترین کیت‌های این بازه است.",
        })

    for r in data.get('tests', []):
        if not r.get('total_tests') and not r.get('qty_out'):
            continue
        devices_txt = '، '.join(
            f"{cnt} تست با {dtype}" for dtype, cnt in (r.get('tests_by_device_type') or {}).items()
        ) or 'بدون ثبت دستگاه مشخص'
        cap_txt = 'نامشخص' if r.get('capacity_unknown') else f"{fmt_num(r.get('capacity'))} تست"
        waste_txt = 'نامشخص' if r.get('waste') is None else f"{fmt_num(r.get('waste'))} عدد ({r.get('waste_percent')}٪)"
        docs.append({
            'tag': f"تست {r['name']}",
            'text': (
                f"تست {r['name']} (کد {r['code']}): {fmt_num(r.get('total_tests'))} بار در این بازه انجام شد ({devices_txt}). "
                f"کیت ورودی {fmt_num(r.get('qty_in'))}، کیت خروجی {fmt_num(r.get('qty_out'))}، ظرفیت کل کیت‌های مصرفی {cap_txt}، "
                f"ضایعات {waste_txt}، موجودی فعلی {fmt_num(r.get('current_stock'))}، تعداد پارتی نزدیک به انقضا {r.get('near_expiry_count', 0)}."
            ),
        })
        for p in r.get('partynos', []):
            if p.get('qty', 0) <= 0 or p.get('days_remaining') is None or p['days_remaining'] > NEAR_EXPIRY_DAYS:
                continue
            docs.append({
                'tag': f"انقضا — {p['kit_name']}",
                'text': (
                    f"کیت «{p['kit_name']}» (برای تست {r['name']}) پارتی {p['party_no']}: {fmt_num(p['qty'])} عدد مانده، "
                    f"تاریخ انقضا {p['expire_date']}، {p['days_remaining']} روز تا انقضا باقی مانده، وضعیت: {p.get('status_text') or 'نامشخص'}."
                ),
            })

    for c in data.get('consumables', [])[:30]:
        docs.append({
            'tag': f"ماده مصرفی — {c['name']}",
            'text': (
                f"ماده مصرفی عمومی «{c['name']}»: کیت ورودی {fmt_num(c.get('qty_in'))}، کیت خروجی {fmt_num(c.get('qty_out'))}، "
                f"موجودی فعلی {fmt_num(c.get('current_stock'))}."
            ),
        })

    if data.get('missing_days'):
        docs.append({'tag': 'داده ناقص', 'text': 'برای این تست‌ها آمار روزانه‌ای در این بازه ثبت نشده: ' + '، '.join(data['missing_days'])})
    if data.get('missing_consumption'):
        docs.append({'tag': 'ظرفیت نامشخص', 'text': 'برای این تست‌ها مصرف هر تست تعریف نشده و ظرفیت‌شان نامشخص است: ' + '، '.join(data['missing_consumption'])})

    if repeats and repeats.get('available'):
        rsum = repeats.get('summary', {}) or {}
        docs.append({'tag': 'تکرار تست‌ها — کلی', 'text': f"در این بازه {fmt_num(rsum.get('total_rows'))} مورد تکرار تست ثبت شده است."})
        for row in (rsum.get('by_reason') or [])[:10]:
            docs.append({'tag': f"علت تکرار — {row['label']}", 'text': f"علت «{row['label']}» باعث {fmt_num(row['count'])} مورد از تکرارهای این بازه شده است."})
        for row in (rsum.get('by_test') or [])[:10]:
            docs.append({'tag': f"تکرار تست — {row['label']}", 'text': f"تست «{row['label']}» در این بازه {fmt_num(row['count'])} بار تکرار شده است."})
        for row in (rsum.get('by_device') or [])[:10]:
            docs.append({'tag': f"تکرار دستگاه — {row['label']}", 'text': f"دستگاه «{row['label']}» در {fmt_num(row['count'])} مورد از تکرارهای این بازه دخیل بوده است."})

    for i, d in enumerate(docs):
        d['id'] = f"doc{i + 1}"
    return docs


def retrieve_relevant_docs(docs, question, top_k=CHAT_TOP_K):
    """رتبه‌بندی اسناد بر اساس شباهت به سؤال. اولویت: BM25 → TF-IDF → هم‌پوشانی ساده."""
    if not docs:
        return []

    texts = [normalize_fa(d['text'] + ' ' + d['tag']) for d in docs]
    q = normalize_fa(question)

    scores = None

    if _HAS_BM25 and q:
        try:
            tokenized = [t.split() for t in texts]
            bm25 = BM25Okapi(tokenized)
            scores = list(bm25.get_scores(q.split()))
        except Exception:
            scores = None

    if scores is None and _HAS_SKLEARN and q:
        try:
            vect = TfidfVectorizer()
            matrix = vect.fit_transform(texts + [q])
            scores = list(cosine_similarity(matrix[-1], matrix[:-1])[0])
        except Exception:
            scores = None

    if scores is None:
        q_tokens = set(q.split()) if q else set()
        scores = [len(q_tokens & set(t.split())) for t in texts]

    ranked = sorted(range(len(docs)), key=lambda i: -scores[i])
    top = [i for i in ranked if scores[i] > 0][:top_k]

    if not top:
        fallback_tags = ('بازه گزارش', 'خلاصه بخش', 'بهره‌وری کلی')
        fallback = [i for i, d in enumerate(docs) if d['tag'] in fallback_tags]
        fallback += [i for i, d in enumerate(docs) if d['tag'].startswith('ضایعات')][:2]
        fallback += [i for i, d in enumerate(docs) if d['tag'].startswith('انقضا')][:2]
        top = fallback[:top_k] or list(range(min(top_k, len(docs))))

    return [docs[i] for i in top]


def call_ollama_chat(question, retrieved_docs):
    context_block = '\n'.join(f"[{d['id']}] {d['text']}" for d in retrieved_docs) or 'سندی یافت نشد.'
    system_prompt = (
        'شما دستیار داده‌ای یک داشبورد آزمایشگاهی هستید. فقط بر اساس اسناد داده‌شده پاسخ بده و از خودت '
        'عددی نساز. اگر پاسخ سؤال در اسناد نبود، صریح بگو اطلاعات کافی برای این سؤال در بازه‌ی انتخاب‌شده '
        'وجود ندارد. پاسخ را کوتاه، دقیق و به زبان فارسی بنویس.'
    )
    user_prompt = f"اسناد:\n{context_block}\n\nسؤال: {question}"

    body = json.dumps({
        'model': OLLAMA_MODEL,
        'stream': False,
        'options': {'temperature': 0.2},
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ],
    }).encode('utf-8')

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat", data=body,
        headers={'Content-Type': 'application/json'}, method='POST',
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode('utf-8'))

    return (result.get('message') or {}).get('content', '').strip()


def fallback_answer(question, retrieved_docs):
    """پاسخ rule-based وقتی مدل زبانی محلی در دسترس نیست."""
    if not retrieved_docs:
        return "اطلاعات کافی برای پاسخ به این سؤال در بازه‌ی انتخاب‌شده وجود ندارد."

    lines = ["بر اساس داده‌های همین بازه:"]
    for d in retrieved_docs[:5]:
        lines.append(f"• [{d['tag']}] {d['text']}")
    lines.append("")
    lines.append("(توجه: مدل زبانی محلی در دسترس نیست؛ این پاسخ مستقیماً از اسناد داده ساخته شده است. "
                 "برای پاسخ روان‌تر، Ollama را اجرا کنید.)")
    return "\n".join(lines)


@app.route('/api/chat', methods=['POST'])
def api_chat():
    payload = request.get_json(silent=True) or {}
    question = (payload.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'سؤال خالی است'}), 400

    try:
        dept = get_department(payload.get('dept') or DEFAULT_DEPARTMENT)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    start = payload.get('start')
    end = payload.get('end')
    if not start or not end:
        return jsonify({'error': 'ابتدا یک بازه‌ی تاریخ (بالای صفحه) انتخاب کنید'}), 400

    if not db_exists(dept):
        return jsonify({'error': f"فایل دیتابیس یافت نشد: {dept['db_path']}"}), 500

    try:
        data = build_report(dept, start, end)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return jsonify({'error': f'خطا در ساخت گزارش: {e}', 'traceback': tb}), 500

    try:
        repeats = load_repeats_data(dept, start, end)
        if repeats is not None:
            repeats['available'] = True
    except Exception:
        print(traceback.format_exc())
        repeats = None

    try:
        docs = build_chat_documents(dept, data, repeats, start=start, end=end)
        retrieved = retrieve_relevant_docs(docs, question)
    except Exception as e:
        tb = traceback.format_exc()
        print(tb)
        return jsonify({'error': f'خطا در بازیابی اسناد: {e}', 'traceback': tb}), 500

    used_llm = True
    try:
        answer = call_ollama_chat(question, retrieved)
        if not answer:
            answer = fallback_answer(question, retrieved)
            used_llm = False
    except Exception as e:
        print(f"[chat] Ollama unavailable, using fallback: {e}")
        answer = fallback_answer(question, retrieved)
        used_llm = False

    return jsonify({
        'answer': answer,
        'retrieved': [{'tag': d['tag'], 'text': d['text']} for d in retrieved],
        'used_llm': used_llm,
    })


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5050)