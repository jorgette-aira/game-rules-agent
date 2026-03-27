import os
import chromadb
import telebot
import threading
from flask import Flask
from PyPDF2 import PdfReader
from openai import OpenAI
import chromadb.utils.embedding_functions as embedding_functions

# ---------------------------------------------------------
# 1. API KEYS & SETUP
# ---------------------------------------------------------
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Initialize OpenAI Client
client = OpenAI(api_key=OPENAI_KEY)

# Tell Chroma to use OpenAI's embedding model
openai_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key=OPENAI_KEY,
    model_name="text-embedding-3-small"
)

chroma_client = chromadb.Client()
collection = chroma_client.get_or_create_collection(
    name="game_rules",
    embedding_function=openai_ef
)

# ---------------------------------------------------------
# 2. DATA INGESTION: Read PDF and Chunk the Text
# ---------------------------------------------------------
def load_pdf_to_db(pdf_path: str):
    print(f"Loading and chunking {pdf_path}...")
    reader = PdfReader(pdf_path)
    
    full_text = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            full_text += text + "\n"
            
    chunk_size = 800
    overlap = 100
    chunks = []
    
    start = 0
    while start < len(full_text):
        end = start + chunk_size
        chunks.append(full_text[start:end])
        start += chunk_size - overlap 
        
    if chunks:
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        collection.add(
            documents=chunks,
            ids=ids
        )
    print(f"Successfully loaded {len(chunks)} chunks into the database!\n")

existing_ids = collection.get()['ids']
if existing_ids:
    collection.delete(ids=existing_ids)
load_pdf_to_db("OneNightUltimateWerewolf-rules.pdf")

# ---------------------------------------------------------
# 3. THE TOOL: How the AI searches the database
# ---------------------------------------------------------
def search_game_rules(query: str) -> str:
    print(f"\n[!] Searching database for: '{query}'")
    results = collection.query(query_texts=[query], n_results=3)
    
    if results['documents'] and results['documents'][0]:
        return "\n\n---NEXT EXCERPT---\n\n".join(results['documents'][0])
    return "No relevant rules found."

# ---------------------------------------------------------
# 4. THE PROTOTYPE LOOP (Now Powered by OpenAI)
# ---------------------------------------------------------
bot = telebot.TeleBot(TG_TOKEN)
print("Connecting to Telegram...")

system_prompt = (
    "You are Paul, a helpful game referee. I will provide you with a user's question "
    "and excerpts from the official rulebook. Answer the user's question using ONLY the rules provided. "
    "Keep it concise. Please format your answers cleanly. DO NOT use markdown formatting like bolding or asterisks."
)

@bot.message_handler(func=lambda message: True)
def handle_telegram_message(message):
    print(f"\n[!] RECEIVED MESSAGE: {message.text}")
    bot.send_chat_action(message.chat.id, 'typing')
    
    try:
        # 1. Search the rulebook using the user's message
        rules_context = search_game_rules(message.text)
        
        # 2. Package the rules and the question together
        user_prompt = f"User Question: {message.text}\n\nOfficial Rules Context:\n{rules_context}"
        
        print("[!] Asking OpenAI...")
        # 3. Send it all to GPT-4o-mini
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2
        )
        
        answer = response.choices[0].message.content
        print("[!] OpenAI replied! Sending to Telegram...")
        
        bot.reply_to(message, answer)
        print("[!] Success!")
        
    except Exception as e:
        bot.reply_to(message, "Sorry, the referee encountered an error reading the rules.")
        print(f"Error Caught: {e}")

# ---------------------------------------------------------
# 5. CLOUD DEPLOYMENT: Keep-Alive Server
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Referee Bot is awake and monitoring the game!"

def run_server():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

server_thread = threading.Thread(target=run_server)
server_thread.start()

bot.infinity_polling(skip_pending=True)
