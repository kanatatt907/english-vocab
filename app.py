import streamlit as st
import pandas as pd
import random
import time
from io import BytesIO

st.set_page_config(page_title="Vocab Quiz", page_icon="📘", layout="centered")

# =========================== Sidebar: Data & Settings ===========================
st.sidebar.title("📥 Data & Settings")

uploaded = st.sidebar.file_uploader("Upload vocabulary file", type=["xlsx", "csv"])
st.sidebar.caption("• .xlsx：每個 sheet 視為一個【類別】；.csv 視為單一類別\n• 第一欄=單字、第二欄=定義、第三欄=例句(可選)")

quiz_mode = st.sidebar.radio(
    "Question type",
    ["Definition ➜ Word (選詞)", "Word ➜ Definition (選義)"],
    index=0,
)

mode_choice = st.sidebar.radio("練習模式", ["一般模式", "錯題本模式"], index=0, help="錯題本模式只考你先前做錯的題目")
items_per_round = st.sidebar.slider("Questions per round", 5, 50, 10, step=5)
shuffle_each_question = st.sidebar.checkbox("Shuffle options each question", value=True)
show_examples = st.sidebar.checkbox("Show example sentences (if available)", value=False)

auto_delay = st.sidebar.slider(
    "Auto-advance delay (sec)",
    0.0, 3.0, 1.8, 0.1,
    help="0 = 不自動，需按 Next；>0 = 顯示結果後延遲再自動換題（對錯一視同仁）"
)

st.sidebar.markdown("---")
if st.sidebar.button("🧹 清空錯題本", use_container_width=True):
    st.session_state["wrong_book"] = []
    st.success("錯題本已清空。")

# ================================ Helpers ======================================
def load_excel(file_bytes: bytes) -> dict:
    """Return dict[category] -> DataFrame(['word','definition','example'])"""
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
    """Return list of indices including 1 correct + (k-1) wrong (if available)."""
    if n_total <= 1:
        return [correct_idx]
    k = min(k, n_total)
    all_idx = list(range(n_total))
    all_idx.remove(correct_idx)
    wrong = random.sample(all_idx, k - 1)
    opts = [correct_idx] + wrong
    random.shuffle(opts)
    return opts

def add_to_wrong_book(word: str, definition: str, example: str | None):
    # 避免重複加入
    for rec in st.session_state["wrong_book"]:
        if rec["word"] == word and rec["definition"] == definition:
            return
    st.session_state["wrong_book"].append({"word": word, "definition": definition, "example": example})

def remove_from_wrong_book(word: str, definition: str):
    st.session_state["wrong_book"] = [
        rec for rec in st.session_state["wrong_book"]
        if not (rec["word"] == word and rec["definition"] == definition)
    ]

def next_question(state):
    data = state["data"]
    if not state["indices_left"]:
        idx = list(range(len(data)))
        random.shuffle(idx)
        state["indices_left"] = idx[:]
    q_idx = state["indices_left"].pop()
    state["current_idx"] = q_idx

    if state["mode"] == "Definition ➜ Word (選詞)":
        state["prompt_text"] = data.loc[q_idx, "definition"]
        state["prompt_is_definition"] = True
        opts_idx = pick_options(len(data), q_idx, k=4)
        state["options_idx"] = opts_idx
        state["options_text"] = [data.loc[i, "word"] for i in opts_idx]
    else:
        state["prompt_text"] = data.loc[q_idx, "word"]
        state["prompt_is_definition"] = False
        opts_idx = pick_options(len(data), q_idx, k=4)
        state["options_idx"] = opts_idx
        state["options_text"] = [data.loc[i, "definition"] for i in opts_idx]

    if st.session_state.get("shuffle_each_question_flag", True):
        pair = list(zip(state["options_idx"], state["options_text"]))
        random.shuffle(pair)
        state["options_idx"], state["options_text"] = map(list, zip(*pair))

    state["selected"] = None
    if "await_next" not in st.session_state:
        st.session_state.await_next = False

def ensure_session():
    if "stats" not in st.session_state:
        st.session_state.stats = {"xp": 0, "correct": 0, "total": 0, "streak": 0}
    if "wrong_book" not in st.session_state:
        st.session_state.wrong_book = []
    # 把 sidebar 的 shuffle 勾選存起來，供 next_question 使用
    st.session_state.shuffle_each_question_flag = shuffle_each_question

# ================================ Load Data ====================================
categories = build_vocab_bank(uploaded)
if not categories:
    st.title("📘 Vocabulary Quiz")
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
    if len(st.session_state.wrong_book) == 0:
        st.warning("你的錯題本目前是空的。請先在『一般模式』做題累積錯題。")
        st.stop()
    df_base = pd.DataFrame(st.session_state.wrong_book)

if len(df_base) < 2:
    st.warning("有效詞條少於 2 筆，請更換類別或補充資料。")
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

# ================================== Main UI ====================================
st.title("📘 Vocabulary Quiz")
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

st.divider()

st.subheader("Definition" if st.session_state.get("prompt_is_definition", True) else "Word")
st.write(st.session_state["prompt_text"])

# 例句（僅一般模式時顯示來源例句；錯題本若有也照樣顯示）
if show_examples:
    try:
        ex = st.session_state.data.loc[st.session_state["current_idx"], "example"]
        if pd.notna(ex) and str(ex).strip():
            with st.expander("Example sentence"):
                st.write(str(ex))
    except Exception:
        pass

# 選項
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

with b1:
    if await_next:
        if st.button("Next", type="primary", use_container_width=True):
            st.session_state.await_next = False
            next_question(st.session_state)
            st.rerun()
    else:
        if st.button("Submit", type="primary", use_container_width=True, disabled=(choice is None)):
            st.session_state.stats["total"] += 1
            picked_idx  = st.session_state["options_idx"][choice]
            correct_idx = st.session_state["current_idx"]
            is_correct  = (picked_idx == correct_idx)

            # 正解/誤答處理
            word_corr = st.session_state.data.loc[correct_idx, "word"]
            def_corr  = st.session_state.data.loc[correct_idx, "definition"]
            ex_corr   = st.session_state.data.loc[correct_idx, "example"] if "example" in st.session_state.data.columns else None

            if is_correct:
                st.success("✅ Correct! +1 XP")
                st.session_state.stats["xp"] += 1
                st.session_state.stats["correct"] += 1
                st.session_state.stats["streak"] += 1
                # 在錯題本模式下，答對即移除該題
                if mode_choice == "錯題本模式":
                    remove_from_wrong_book(word_corr, def_corr)
            else:
                st.error(f"❌ Wrong. Answer: {word_corr if st.session_state.get('prompt_is_definition', True) else def_corr}")
                st.session_state.stats["streak"] = 0
                # 在一般模式下，答錯加入錯題本
                if mode_choice == "一般模式":
                    add_to_wrong_book(word_corr, def_corr, ex_corr)

            # 換題邏輯：自動 or 手動
            if auto_delay > 0:
                time.sleep(auto_delay)          # 對錯一致延遲
                # 如果錯題本模式且已被清空，提示並退出
                if mode_choice == "錯題本模式" and len(st.session_state.wrong_book) == 0:
                    st.info("🎉 錯題本已清空！切回『一般模式』繼續練習吧。")
                    st.stop()
                next_question(st.session_state)
                st.rerun()
            else:
                st.session_state.await_next = True

with b2:
    if await_next:
        st.button("Skip", use_container_width=True, disabled=True)
    else:
        if st.button("Skip", use_container_width=True):
            st.info("⏭️ Skipped.")
            st.session_state.stats["streak"] = 0
            if auto_delay > 0:
                time.sleep(auto_delay)
                next_question(st.session_state)
                st.rerun()
            else:
                st.session_state.await_next = True

with b3:
    if st.button("Reset round", use_container_width=True):
        # 重置當前題庫（一般模式=當前 sheet；錯題模式=當前錯題集）
        st.session_state.data = (categories[selected_cat].copy().reset_index(drop=True)
                                 if mode_choice == "一般模式"
                                 else pd.DataFrame(st.session_state.wrong_book).reset_index(drop=True))
        idx = list(range(len(st.session_state.data)))
        random.shuffle(idx)
        st.session_state.indices_left = idx[:]
        st.session_state.stats = {"xp": 0, "correct": 0, "total": 0, "streak": 0}
        next_question(st.session_state)
        st.rerun()

st.caption("Tip: 一般模式可累積錯題；錯題本模式只練錯過的題，答對即自動移除。CSV 視為單一類別。")

