import streamlit as st
from dotenv import load_dotenv
import os
from src.ingestion import ingest_documents
from src.graph import run_graph

# Load environment variables
load_dotenv()

st.set_page_config(layout="wide")
st.title("AI 投研协作平台 (AlphaLens) Demo")

# --- Sidebar for Ingestion and Control ---
with st.sidebar:
    st.header("数据入库 (Ingestion)")
    
    # Check for API key
    if not os.getenv("OPENAI_API_KEY"):
        st.error("请在 .env 文件中设置 OPENAI_API_KEY")
    else:
        st.success("API Key 已加载")

    ingest_ticker = st.text_input("输入 Ticker 进行入库", "AAPL")
    uploaded_file = st.file_uploader("上传研报 (TXT格式)", type=["txt"])

    if st.button("执行入库"):
        if uploaded_file is not None and ingest_ticker:
            staging_path = "data/staging"
            if not os.path.exists(staging_path):
                os.makedirs(staging_path)
            
            # Save the uploaded file to the staging directory
            file_path = os.path.join(staging_path, uploaded_file.name)
            with open(file_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            
            with st.spinner(f"正在为 Ticker: {ingest_ticker} 处理文档..."):
                ingest_documents(ingest_ticker, staging_dir=staging_path)
            st.success(f"Ticker: {ingest_ticker} 数据入库完成！")
            # Clean up the staging file after ingestion
            os.remove(file_path)
        else:
            st.warning("请上传文件并输入 Ticker")

# --- Main App Layout (3 Columns) ---
col1, col2, col3 = st.columns(3)

with col1:
    st.header("关键假设 (Key Assumptions)")
    ticker_input = st.text_input("输入要分析的 Ticker", "AAPL")
    assumptions_input = st.text_area(
        "输入关键假设 (每行一个)",
        "iPhone sales are the primary driver of revenue.\nThe company is facing significant competitive pressure.\nServices division growth is accelerating."
    )
    
    start_analysis = st.button("开始分析")

with col2:
    st.header("多空辩论 (Bull vs. Bear)")
    if start_analysis:
        if not ticker_input or not assumptions_input:
            st.warning("请输入 Ticker 和关键假设")
        else:
            assumptions_list = [line.strip() for line in assumptions_input.split('\n') if line.strip()]
            
            try:
                with st.spinner("AI Agents 正在分析..."):
                    result = run_graph(ticker_input, assumptions_list)
                
                st.subheader("🐂 Bull 论点")
                st.markdown(result.get('bull_argument', "未能生成论点"))
                
                st.subheader("🐻 Bear 论点")
                st.markdown(result.get('bear_argument', "未能生成论点"))
                
                st.session_state['final_report'] = result.get('final_report')

            except FileNotFoundError as e:
                st.error(f"错误: {e}. 请先为该 Ticker 入库数据。")
            except Exception as e:
                st.error(f"分析过程中出现错误: {e}")

with col3:
    st.header("风险与迁移建议 (Risks & Mitigation)")
    if 'final_report' in st.session_state and st.session_state['final_report']:
        st.markdown(st.session_state['final_report'])
