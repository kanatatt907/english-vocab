import streamlit as st
import pandas as pd
import random
import time
import json
import unicodedata
import difflib
import re
from io import BytesIO, StringIO

st.set_page_config(page_title="Vocab Quiz+", page_icon="📘", layout="centered")

# =========================== Sidebar: Data & Settings ===========================
st.sidebar.title("📥 Data & Settings")

uploaded_vocab = st.sidebar.file_uploader("Upload vocabulary file", type=["xlsx", "csv"])
st.sidebar.caption("• .xlsx：每個 sheet 視為一個【類別】；.csv 視為單一類別\n• 第一欄=單字、第二欄=定義、第三欄=例句(可選)")

quiz_mode = st.sidebar.radio(
    "Question type",
    ["Definition ➜ Word (選詞)", "Word ➜ Definition (選義)", "Spelling (Definition ➜ Word)"],
    index=0,
)

mode_choice = st.sidebar.radio("練習模式", ["一般模式", "錯題本模式"], index=0, help="錯題本模式只考先前做錯的題目")
items_per_round = st.sidebar.slider("Questions per round (UI進度條用途)", 5, 50, 10, step=5)
shuffle_each_question = st.sidebar.checkbox("Shuffle options each question (僅選擇題)", value=True)
show_examples = st.sidebar.checkbox("Show example sentences (if available)", value=False)

st.sidebar.markdown("### Spelling settings")
enable_fuzzy = st.sidebar.checkbox("Enable fuzzy matching (tolerate small typos)", True)
near_threshold = st.sidebar.slider("Near-miss threshold (%)", 70, 95, 85, 1,
                                   help="相似度達到此百分比就算『接近』")
count_near_as_correct = st.sidebar.checkbox("Count near-miss as correct", False,
                                            help="勾選=接近就當作正確；不勾=接近仍算錯")

auto_delay = st.sidebar.slider(
    "Auto-advance delay (sec)",
    0.0, 3.0, 1.8, 0.1,
    help="0 = 不自動，需按 Next；>0 = 顯示結果後延遲再自動換題（對錯一視同仁）"
)

st.sidebar.markdown("---")
# 分級測驗設定
exam_len = st.sidebar.slider("分級測驗題數", 5, 50, 10, step=5)
start_exam = st.sidebar.button("🎯 開始分級測驗", use_container_width=True)

# 進度檔案：載入與匯出
st.sidebar.markdown("### 進度檔")
uploaded_progress = st.sidebar.file_uploader("載入進度 JSON", type=["json"])
export_json_btn = st.sidebar.button("⬇️ 匯出進度 JSON", use_container_width=True)
export_csv_btn = st.sidebar.button("⬇️ 匯出熟練度 CSV", use_container_width=True)

st.sidebar.markdown("---")
if st.sidebar.button("🧹 清空錯題本", use_container_width=True):
    st.session_state["wrong_book"] = []
    st.success("錯題本已清空。")

# ================================ Helpers ======================================
def load_excel(file_bytes: bytes) -> dict:
    xls = pd.ExcelFile(BytesIO(file_bytes))
    cats = {}
    for sheet in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name=sheet)
        if raw.shape[1] < 2:
            continue
        df = pd.DataFrame({
            "word": raw.iloc[:, 0],
            "definition": raw.iloc[:, 1],
            "example": raw.iloc[:, 2] if raw.shape[1] >= 3 else None
        }).dropna(subset=["word", "definition"])
        df["word"] = df["word"].astype(str).str.strip()
        df["definition"] = df["definition"].astype(str).str.strip()
        if "example" in df and df["example"] is not None:
            df["example"] = df["example"].astype(str)
        df = df[df["word"].str.len() > 0].reset_index(drop=True)
        if len(df) > 0 and sheet.lower() != "content page":
            cats[sheet] = df
    return cats

def load_csv(file_bytes: bytes) -> dict:
    raw = pd.read_csv(BytesIO(file_bytes))
    if raw.shape[1] < 2:
        st.error("CSV 至少需要兩欄：第一欄『單字』、第二欄『定義』。")
        st.stop()
    df = pd.DataFrame({
        "word": raw.iloc[:, 0],
        "definition": raw.iloc[:, 1],
        "example": raw.iloc[:, 2] if raw.shape[1] >= 3 else None
    }).dropna(subset=["word", "definition"])
    df["word"] = df["word"].astype(str).str.strip()
    df["definition"] = df["definition"].astype(str).str.strip()
    if "example" in df and df["example"] is not None:
        df["example"] = df["example"].astype(str)
    return {"All": df.reset_index(drop=True)}

def build_vocab_bank(file) -> dict:
    if file is None:
        return {}
    name = file.name.lower()
    if name.endswith(".xlsx"):
        return load_excel(file.read())
    elif name.endswith(".csv"):
        return load_csv(file.read())
    return {}

def pick_options(n_total, correct_idx, k=4):
    if n_total <= 1:
        return [correct_idx]
    k = min(k, n_total)
    all_idx = list(range(n_total))
    all_idx.remove(correct_idx)
    wrong = random.sample(all_idx, k - 1)
    opts = [correct_idx] + wrong
    random.shuffle(opts)
    return opts

def strip_accents(s: str) -> str:
    # 移除重音/變音符號（café -> cafe, naïve -> naive）
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def normalize_token(s: str) -> str:
    # 忽略大小寫、重音、空白/連字號/撇號等符號
    s = strip_accents(s or "")
    s = s.lower().strip()
    s = re.sub(r"[\s\-\u2010\u2011\u2013\u2014\u2019']", "", s)  # 空白、各種連字/破折、直/彎引號
    return s

def similarity_pct(a: str, b: str) -> float:
    a_n, b_n = normalize_token(a), normalize_token(b)
    return difflib.SequenceMatcher(None, a_n, b_n).ratio() * 100

def spelling_verdict(user: str, target: str, threshold_pct: float) -> str:
    # 回傳 'exact' | 'near' | 'wrong'
    if normalize_token(user) == normalize_token(target):
        return "exact"
    if similarity_pct(user, target) >= threshold_pct:
        return "near"
    return "wrong"

def ensure_session():
    if "stats" not in st.session_state:
        st.session_state.stats = {"xp": 0, "correct": 0, "total": 0, "streak": 0}
    if "wrong_book" not in st.session_state:
        st.session_state.wrong_book = []
    if "mastery" not in st.session_state:
        # mastery[word] = {"seen":0,"correct":0,"wrong":0}
        st.session_state.mastery = {}
    if "await_next" not in st.session_state:
        st.session_state.await_next = False
    if "sr_queue" not in st.session_state:
        # 間隔重複的待出題列：每題之後 due-1，為 0 時插入下一題
        st.session_state.sr_queue = []  # list[{"idx": int, "due": int}]
    if "exam_active" not in st.session_state:
        st.session_state.exam_active = False
    if "exam_remaining" not in st.session_state:
        st.session_state.exam_remaining = 0
    if "exam_correct" not in st.session_state:
        st.session_state.exam_correct = 0
    st.session_state.shuffle_each_question_flag = shuffle_each_question

def update_mastery(word: str, correct: bool):
    m = st.session_state.mastery.setdefault(word, {"seen": 0, "correct": 0, "wrong": 0})
    m["seen"] += 1
    if correct:
        m["correct"] += 1
    else:
        m["wrong"] += 1

def add_to_wrong_book(word: str, definition: str, example: str | None):
    for rec in st.session_state["wrong_book"]:
        if rec["word"] == word and rec["definition"] == definition:
            return
    st.session_state["wrong_book"].append({"word": word, "definition": definition, "example": example})

def remove_from_wrong_book(word: str, definition: str):
    st.session_state["wrong_book"] = [
        rec for rec in st.session_state["wrong_book"]
        if not (rec["word"] == word and rec["definition"] == definition)
    ]

def schedule_spaced_repetition(idx: int, delay: int = 3):
    # 錯題 after 3 題再出現（簡單版 SR）
    st.session_state.sr_queue.append({"idx": idx, "due": delay})

def sr_tick_and_pick():
    """每次出題前呼叫：讓 sr_queue 的 due 減 1，若有 due=0 的，優先出這題。"""
    for item in st.session_state.sr_queue:
        item["due"] = max(0, item["due"] - 1)
    due_items = [i for i in st.session_state.sr_queue if i["due"] == 0]
    if due_items:
        chosen = due_items[0]["idx"]
        # 移除第一個 due 的
        st.session_state.sr_queue = [i for i in st.session_state.sr_queue if not (i["due"] == 0 and i["idx"] == chosen)]
        return chosen
    return None

def next_question(state):
    data = state["data"]

    # SR 檢查
    sr_idx = sr_tick_and_pick()
    if sr_idx is not None:
        q_idx = sr_idx
    else:
        if not state["indices_left"]:
            idx = list(range(len(data)))
            random.shuffle(idx)
            state["indices_left"] = idx[:]
        q_idx = state["indices_left"].pop()

    state["current_idx"] = q_idx

    if state["mode"] == "Definition ➜ Word (選詞)":
        state["prompt_text"] = data.loc[q_idx, "definition"]
        state["prompt_is_definition"] = True
        state["is_spelling"] = False
        opts_idx = pick_options(len(data), q_idx, k=4)
        state["options_idx"] = opts_idx
        state["options_text"] = [data.loc[i, "word"] for i in opts_idx]
    elif state["mode"] == "Word ➜ Definition (選義)":
        state["prompt_text"] = data.loc[q_idx, "word"]
        state["prompt_is_definition"] = False
        state["is_spelling"] = False
        opts_idx = pick_options(len(data), q_idx, k=4)
        state["options_idx"] = opts_idx
        state["options_text"] = [data.loc[i, "definition"] for i in opts_idx]
    else:  # Spelling
        state["prompt_text"] = data.loc[q_idx, "definition"]
        state["prompt_is_definition"] = True
        state["is_spelling"] = True
        state["options_idx"] = []
        state["options_text"] = []
        state["typed_answer"] = ""

    if st.session_state.shuffle_each_question_flag and not state["is_spelling"]:
        pair = list(zip(state["options_idx"], state["options_text"]))
        random.shuffle(pair)
        if pair:
            state["options_idx"], state["options_text"] = map(list, zip(*pair))

    state["selected"] = None
    state["await_next"] = False

# ================================ Load Data ====================================
categories = build_vocab_bank(uploaded_vocab)
if not categories:
    st.title("📘 Vocabulary Quiz+")
    st.info("左側上傳 Excel/CSV 開始。建議：Excel 第一欄=單字、第二欄=定義、第三欄=例句（可選）。")
    st.stop()

ensure_session()

# 類別選擇（一般模式要選 sheet；錯題本模式忽略）
cat_names = list(categories.keys())
if mode_choice == "一般模式":
    selected_cat = st.selectbox("Category / 類別", cat_names, index=0)
else:
    selected_cat = None  # 錯題本模式不需要 sheet

# 取資料：一般模式=從選定 sheet；錯題本模式=從 wrong_book
if mode_choice == "一般模式":
    df_base = categories[selected_cat].copy()
else:
    if len(st.session_state.get("wrong_book", [])) == 0:
        st.warning("你的錯題本目前是空的。請先在『一般模式』做題累積錯題。")
        st.stop()
    df_base = pd.DataFrame(st.session_state.wrong_book)

if len(df_base) < 1:
    st.warning("有效詞條不足，請更換類別或補充資料。")
    st.stop()

# 初始化 / 模式或類別或題型變更時重置
if ("mode" not in st.session_state) or (st.session_state.mode != quiz_mode) or \
   ("cat" not in st.session_state) or (st.session_state.cat != selected_cat) or \
   ("data" not in st.session_state) or \
   ("practice_mode" not in st.session_state) or (st.session_state.practice_mode != mode_choice):
    st.session_state.mode = quiz_mode
    st.session_state.cat = selected_cat
    st.session_state.practice_mode = mode_choice
    st.session_state.data = df_base.reset_index(drop=True)
    indices = list(range(len(st.session_state.data)))
    random.shuffle(indices)
    st.session_state.indices_left = indices[:]
    st.session_state.stats = {"xp": 0, "correct": 0, "total": 0, "streak": 0}
    next_question(st.session_state)

# 開始分級測驗（固定題數，結束給成績）
if start_exam:
    st.session_state.exam_active = True
    st.session_state.exam_remaining = min(exam_len, len(st.session_state.data))
    st.session_state.exam_correct = 0
    # 重建不重複題組
    indices = list(range(len(st.session_state.data)))
    random.shuffle(indices)
    st.session_state.indices_left = indices[:]
    next_question(st.session_state)

# ================================== Main UI ====================================
st.title("📘 Vocabulary Quiz+")
c1, c2, c3, c4 = st.columns([1,1,1,1])
with c1:
    st.metric("XP", st.session_state.stats["xp"])
with c2:
    total = max(st.session_state.stats["total"], 1)
    acc = st.session_state.stats["correct"] / total * 100
    st.metric("Accuracy", f"{acc:.0f}%")
with c3:
    st.metric("Streak", st.session_state.stats["streak"])
with c4:
    done = st.session_state.stats["total"] % items_per_round
    st.progress(done / items_per_round)

# 分級測驗狀態提示
if st.session_state.exam_active:
    st.info(f"🎯 分級測驗進行中 | 剩餘題數：{st.session_state.exam_remaining} | 目前得分：{st.session_state.exam_correct}")

st.divider()

# 題幹
if quiz_mode == "Word ➜ Definition (選義)":
    st.subheader("Word")
else:
    st.subheader("Definition")
st.write(st.session_state["prompt_text"])

# 例句
if show_examples and "example" in st.session_state.data.columns:
    try:
        ex = st.session_state.data.loc[st.session_state["current_idx"], "example"]
        if pd.notna(ex) and str(ex).strip():
            with st.expander("Example sentence"):
                st.write(str(ex))
    except Exception:
        pass

# 顯示題目互動區
typed = None
choice = None
if st.session_state.get("is_spelling", False):
    typed = st.text_input("Type the correct word (拼寫)", value=st.session_state.get("typed_answer", ""))
else:
    choice = st.radio(
        label="Select one:",
        options=range(len(st.session_state["options_text"])),
        format_func=lambda i: st.session_state["options_text"][i],
        index=None,
        key="selected_radio",
    )

# ================================= Buttons =====================================
b1, b2, b3 = st.columns([1,1,1])
await_next = st.session_state.get("await_next", False)

def finish_or_next():
    """處理分級測驗結束與換題邏輯"""
    if st.session_state.exam_active:
        st.session_state.exam_remaining -= 1
        if st.session_state.exam_remaining <= 0:
            score = st.session_state.exam_correct
            st.session_state.exam_active = False
            st.success(f"🎉 測驗結束！得分 {score} / {exam_len}（{score/exam_len*100:.0f}%）")
            st.session_state.await_next = True  # 等使用者重設/繼續
            return
    next_question(st.session_state)
    st.rerun()

with b1:
    if await_next:
        if st.button("Next", type="primary", use_container_width=True):
            st.session_state.await_next = False
            next_question(st.session_state)
            st.rerun()
    else:
        if st.button("Submit", type="primary", use_container_width=True,
                     disabled=(typed is None and choice is None) or (st.session_state.get("is_spelling", False) and (typed or "").strip()=="" )):
            st.session_state.stats["total"] += 1
            data = st.session_state.data
            correct_idx = st.session_state["current_idx"]
            word_corr = data.loc[correct_idx, "word"]
            def_corr  = data.loc[correct_idx, "definition"]
            ex_corr   = data.loc[correct_idx, "example"] if "example" in data.columns else None

            # 判斷正誤（含拼寫模糊比對）
            feedback_mode = "wrong"
            is_correct = False
            user = None

            if st.session_state.get("is_spelling", False):
                user = (typed or "").strip()
                if enable_fuzzy:
                    verdict = spelling_verdict(user, str(word_corr), near_threshold)
                    if verdict == "exact":
                        is_correct = True
                        feedback_mode = "exact"
                    elif verdict == "near":
                        is_correct = bool(count_near_as_correct)  # 近似是否當正確
                        feedback_mode = "near"
                    else:
                        is_correct = False
                        feedback_mode = "wrong"
                else:
                    is_correct = (normalize_token(user) == normalize_token(str(word_corr)))
                    feedback_mode = "exact" if is_correct else "wrong"
            else:
                picked_idx  = st.session_state["options_idx"][choice]
                is_correct  = (picked_idx == correct_idx)
                feedback_mode = "exact" if is_correct else "wrong"

            # 顯示回饋
            if feedback_mode == "exact":
                st.success("✅ Correct! +1 XP")
            elif feedback_mode == "near":
                sim = similarity_pct(user, str(word_corr))
                if count_near_as_correct:
                    st.info(f"🟡 Almost! ({sim:.0f}%) — 已視為正確")
                else:
                    st.warning(f"🟡 Almost! ({sim:.0f}%) — 正確拼法：{word_corr}")
            else:
                show_ans = word_corr if st.session_state.get('prompt_is_definition', True) else def_corr
                st.error(f"❌ Wrong. Answer: {show_ans}")

            # 更新統計/記錄
            update_mastery(word_corr, is_correct)
            if is_correct:
                st.session_state.stats["xp"] += 1
                st.session_state.stats["correct"] += 1
                st.session_state.stats["streak"] += 1
                if mode_choice == "錯題本模式":
                    remove_from_wrong_book(word_corr, def_corr)
            else:
                st.session_state.stats["streak"] = 0
                if mode_choice == "一般模式":
                    add_to_wrong_book(word_corr, def_corr, ex_corr)
                # SR：錯題 3 題後再出現（若 near 但算錯，也安排）
                schedule_spaced_repetition(correct_idx, delay=3)

            # 分級測驗得分
            if st.session_state.exam_active and is_correct:
                st.session_state.exam_correct += 1

            # 自動/手動換題
            if auto_delay > 0 and not st.session_state.exam_active:
                time.sleep(auto_delay)
                finish_or_next()
            elif auto_delay > 0 and st.session_state.exam_active:
                time.sleep(auto_delay)
                finish_or_next()
            else:
                st.session_state.await_next = True

with b2:
    if await_next:
        st.button("Skip", use_container_width=True, disabled=True)
    else:
        if st.button("Skip", use_container_width=True):
            st.info("⏭️ Skipped.")
            st.session_state.stats["streak"] = 0
            if auto_delay > 0 and not st.session_state.exam_active:
                time.sleep(auto_delay)
                next_question(st.session_state)
                st.rerun()
            else:
                st.session_state.await_next = True

with b3:
    if st.button("Reset round", use_container_width=True):
        st.session_state.data = (categories[selected_cat].copy().reset_index(drop=True)
                                 if mode_choice == "一般模式"
                                 else pd.DataFrame(st.session_state.wrong_book).reset_index(drop=True))
        idx = list(range(len(st.session_state.data)))
        random.shuffle(idx)
        st.session_state.indices_left = idx[:]
        st.session_state.stats = {"xp": 0, "correct": 0, "total": 0, "streak": 0}
        st.session_state.sr_queue = []
        next_question(st.session_state)
        st.rerun()

st.caption("Tip: 一般模式可累積錯題；錯題本模式只練錯過的題（答對即移除）。Spelling 模式支援模糊比對。分級測驗：固定題數、給總分。")

# ========================= Export / Import Progress ============================
# 匯出 JSON（mastery + wrong_book）
if export_json_btn:
    payload = {
        "mastery": st.session_state.mastery,
        "wrong_book": st.session_state.wrong_book
    }
    buf = json.dumps(payload, ensure_ascii=False, indent=2)
    st.download_button("下載 progress.json", data=buf, file_name="progress.json", mime="application/json")

# 匯出熟練度 CSV
if export_csv_btn:
    rows = []
    for w, m in st.session_state.mastery.items():
        seen = m["seen"]
        correct = m["correct"]
        wrong = m["wrong"]
        acc = (correct / seen * 100) if seen > 0 else 0
        rows.append({"word": w, "seen": seen, "correct": correct, "wrong": wrong, "accuracy_%": f"{acc:.0f}"})
    df_out = pd.DataFrame(rows).sort_values(by=["accuracy_%","seen"], ascending=[False, False])
    csv_buf = StringIO()
    df_out.to_csv(csv_buf, index=False)
    st.download_button("下載 mastery.csv", data=csv_buf.getvalue(), file_name="mastery.csv", mime="text/csv")

# 載入 JSON 進度
if uploaded_progress is not None:
    try:
        loaded = json.loads(uploaded_progress.getvalue())
        if "mastery" in loaded and isinstance(loaded["mastery"], dict):
            st.session_state.mastery = loaded["mastery"]
        if "wrong_book" in loaded and isinstance(loaded["wrong_book"], list):
            st.session_state.wrong_book = loaded["wrong_book"]
        st.success("已載入進度（mastery / wrong_book）")
    except Exception as e:
        st.error(f"讀取進度檔失敗：{e}")

