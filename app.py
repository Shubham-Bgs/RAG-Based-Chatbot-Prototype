import streamlit as st
from rag import answer, Conversation, TOP_K   


if "conv" not in st.session_state:
    st.session_state.conv = Conversation()

st.set_page_config(page_title="Restaurant Menu Chatbot",
                   page_icon="🍔",
                   layout="centered")

st.title("Restaurant Menu Chatbot")

query = st.text_input(
    "Ask a question about the menus:",
    placeholder="Suggest a spicy vegetarian snack",
    key="query",
)

submit_clicked = st.button("Submit")

if submit_clicked and query:
    with st.spinner("Generating answer…"):
        reply, ctx = answer(query, st.session_state.conv)

    st.markdown("### Answer")
    st.write(reply)

    st.markdown(f"### Retrieved context (Top {TOP_K})")
    for c in ctx:
        st.write(f"- {c['text']}  (distance {c['distance']:.3f})")

# Optional reset button in the sidebar
with st.sidebar:
    if st.button("Reset conversation"):
        st.session_state.pop("conv", None)
        st.experimental_rerun()
